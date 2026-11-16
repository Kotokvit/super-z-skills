#!/usr/bin/env python3
"""
observer.py — Binary Observer for v2.1 Architecture

The observer is the agent itself (Super-Z), reading its own work and deciding:
    - "Which ONE skill should I invoke next?"  (planner mode)
    - "Does my brief answer the question?"     (evaluator mode)
    - "Is this entity a fact, hypothesis, or thesis?"  (layer mode)

This is POLER[Ψ]'s Observer-Kill in reverse: during skill execution, the
observer is suppressed (skills fire "без воли", v2.0). Between cycles, the
observer re-emerges to evaluate and decide.

All decisions are BINARY (yes/no) or ONE-OF-N (single pick), never continuous.
The observer returns exactly:
    - planner:  ONE skill name, or NONE
    - evaluator: YES, NO, or REFRACT
    - layer:    FACT, HYPOTHESIS, or THESIS
    - refraction: REFRACT, ABSORB, or ESCALATE (+ which alternative)

Each observer call is one LLM invocation (~1-2 seconds). For a typical query,
2-4 cycles → 2-4 observer calls. Acceptable cost for genuine agency.

API:
    obs = Observer()
    next_skill = obs.planner(user_message, brief_summary, candidate_skills)
    decision = obs.evaluator(user_message, brief_summary)
    layer = obs.layer_for_entity(entity, context, user_message)
    action = obs.refraction(failed_skill, capability, gap, alternatives)
    world = obs.world_for_entity(entity, context, user_message, existing_worlds)

Author: Super-Z team, 2026-07-04
"""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Make sibling modules importable
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR.parent / "_shared"))

from llm_wrapper import call_z_ai_chat  # noqa: E402


# ─── Response types ────────────────────────────────────────────────────

@dataclass
class PlannerDecision:
    """One result from the planner observer."""
    skill_name: Optional[str]      # ONE skill name, or None if no skill would help
    rationale: str = ""
    parallel_skill: Optional[str] = None  # If observer identified independent sub-goal
    raw_response: str = ""

    @property
    def is_none(self) -> bool:
        return self.skill_name is None

    @property
    def is_parallel(self) -> bool:
        return self.parallel_skill is not None


@dataclass
class EvaluatorDecision:
    """One result from the evaluator observer."""
    decision: str  # YES | NO | REFRACT
    rationale: str = ""
    gap_description: str = ""  # If NO: what's missing
    raw_response: str = ""

    @property
    def is_yes(self) -> bool:
        return self.decision.upper() == "YES"

    @property
    def is_no(self) -> bool:
        return self.decision.upper() == "NO"

    @property
    def is_refract(self) -> bool:
        return self.decision.upper() == "REFRACT"


@dataclass
class RefractionDecision:
    """One result from the refraction observer (No Excuses protocol)."""
    action: str  # REFRACT | ABSORB | ESCALATE
    alternative_skill: Optional[str] = None
    rationale: str = ""
    raw_response: str = ""


# ─── Observer class ────────────────────────────────────────────────────

class Observer:
    """The agent itself, observing its own work.

    All methods make ONE LLM call and return a strictly-parsed binary decision.
    """

    def __init__(self, llm_timeout_sec: int = 60):
        self.llm_timeout = llm_timeout_sec
        self.log: list[dict] = []  # history of all observer calls (for --observer-log)
        self._fallback_warned = False  # one-time warning when z-ai CLI is unavailable

    # ── LLM call with provider fallback ────────────────────────────────

    def _call_llm(self, system_prompt: str, user_prompt: str) -> Optional[str]:
        """Call LLM with provider fallback chain.

        Provider order (first non-None wins):
            1. z-ai CLI (primary, via llm_wrapper.call_z_ai_chat)
            2. OpenAI-compatible API  — if $OPENAI_API_KEY is set
            3. Anthropic API          — if $ANTHROPIC_API_KEY is set
            4. Ollama local           — if $OLLAMA_HOST reachable (default localhost:11434)

        Returns the assistant's text response, or None if every provider failed.
        Each observer method already has a safe default for None — so the system
        keeps running even with no LLM available, just with degraded decisions.

        Env vars (all optional):
            OPENAI_API_KEY, OPENAI_BASE_URL (default https://api.openai.com/v1),
                OPENAI_MODEL (default gpt-4o-mini)
            ANTHROPIC_API_KEY, ANTHROPIC_MODEL (default claude-3-5-sonnet-20241022)
            OLLAMA_HOST (default http://localhost:11434),
                OLLAMA_MODEL (default llama3)
        """
        response = call_z_ai_chat(
            system_prompt, user_prompt, timeout_sec=self.llm_timeout
        )
        if response:
            return response

        # Primary failed — emit one-time warning, then try fallbacks
        if not self._fallback_warned:
            sys.stderr.write(
                "[observer] z-ai CLI unavailable. Trying fallback providers "
                "(OPENAI_API_KEY / ANTHROPIC_API_KEY / OLLAMA_HOST).\n"
            )
            self._fallback_warned = True

        # 2. OpenAI-compatible
        openai_key = os.environ.get("OPENAI_API_KEY")
        if openai_key:
            response = self._call_openai(system_prompt, user_prompt, openai_key)
            if response:
                return response

        # 3. Anthropic
        anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
        if anthropic_key:
            response = self._call_anthropic(system_prompt, user_prompt, anthropic_key)
            if response:
                return response

        # 4. Ollama (local, no key required — try only if host is reachable)
        response = self._call_ollama(system_prompt, user_prompt)
        if response:
            return response

        return None

    def _call_openai(
        self, system: str, user: str, api_key: str
    ) -> Optional[str]:
        """OpenAI-compatible chat completion (works for Azure OpenAI, OpenRouter,
        Together, Groq, etc. — anything that mimics the OpenAI API shape)."""
        import urllib.request
        import urllib.error

        base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        body = json.dumps({
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": 400,
            "temperature": 0.3,
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{base}/chat/completions",
            data=body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.llm_timeout) as r:
                data = json.loads(r.read().decode("utf-8"))
                content = (
                    data.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                )
                return content.strip() or None
        except Exception as e:
            sys.stderr.write(f"[observer] OpenAI fallback failed: {e}\n")
            return None

    def _call_anthropic(
        self, system: str, user: str, api_key: str
    ) -> Optional[str]:
        """Anthropic Messages API."""
        import urllib.request
        import urllib.error

        model = os.environ.get("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022")
        body = json.dumps({
            "model": model,
            "max_tokens": 400,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }).encode("utf-8")
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=body,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.llm_timeout) as r:
                data = json.loads(r.read().decode("utf-8"))
                # Anthropic returns content as a list of blocks
                blocks = data.get("content", [])
                if blocks and isinstance(blocks, list):
                    text = blocks[0].get("text", "")
                    return text.strip() or None
                return None
        except Exception as e:
            sys.stderr.write(f"[observer] Anthropic fallback failed: {e}\n")
            return None

    def _call_ollama(self, system: str, user: str) -> Optional[str]:
        """Ollama local API (no API key needed). Silently skipped if host
        unreachable — avoids slow startup when no Ollama is running."""
        import urllib.request
        import urllib.error

        host = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
        model = os.environ.get("OLLAMA_MODEL", "llama3")
        body = json.dumps({
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "options": {"temperature": 0.3},
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{host}/api/chat",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.llm_timeout) as r:
                data = json.loads(r.read().decode("utf-8"))
                content = data.get("message", {}).get("content", "")
                return content.strip() or None
        except Exception as e:
            # Don't warn loudly for Ollama — it's expected to be absent on most systems
            sys.stderr.write(f"[observer] Ollama fallback skipped: {e}\n")
            return None

    # ── Planner mode: "which ONE skill next?" ──────────────────────────

    def planner(
        self,
        user_message: str,
        brief_summary: str,
        candidate_skills: list[dict],
        max_response_tokens: int = 200,
    ) -> PlannerDecision:
        """Ask the observer which ONE skill to invoke next.

        Args:
            user_message:  the original user query
            brief_summary: short summary of what's already in the brief
            candidate_skills: list of {name, description, capabilities} dicts
            max_response_tokens: not used directly, but informs prompt brevity

        Returns:
            PlannerDecision with skill_name (or None) + optional parallel_skill
        """
        candidates_str = "\n".join(
            f"  - {s['name']}: {s.get('description', '')[:120]}"
            for s in candidate_skills[:20]  # cap at 20 to keep prompt bounded
        )

        system_prompt = (
            "You are the observer of your own work, in PLANNER mode.\n"
            "Your job: decide which ONE skill to invoke next to make progress on the user's question.\n\n"
            "RULES:\n"
            "1. Binary principle: either a skill is needed (1) or it isn't (0). No degrees.\n"
            "2. Pick the SINGLE most useful skill. Do not pick multiple.\n"
            "3. If you identify TWO clearly independent sub-goals (e.g. 'compare X and Y' where X and Y need different skills), you may specify a parallel_skill.\n"
            "4. If NO skill would meaningfully reduce the gap, return NONE — do not pad.\n"
            "5. Skills fire 'без воли' — you don't decide their content, only which one to invoke.\n"
        )

        user_prompt = (
            f"USER QUESTION:\n{user_message}\n\n"
            f"CURRENT BRIEF SUMMARY:\n{brief_summary or '(empty — first cycle)'}\n\n"
            f"CANDIDATE SKILLS:\n{candidates_str}\n\n"
            "Respond in EXACTLY this format (no other text):\n"
            "SKILL: <skill_name>\n"
            "PARALLEL: <skill_name_or_NONE>\n"
            "RATIONALE: <one sentence>\n"
            "\n"
            "If no skill would help, respond:\n"
            "SKILL: NONE\n"
            "PARALLEL: NONE\n"
            "RATIONALE: <why no skill would help>\n"
        )

        response = self._call_llm(system_prompt, user_prompt)
        if not response:
            return PlannerDecision(skill_name=None, rationale="LLM call failed", raw_response="")

        # Parse strict format
        skill_match = re.search(r"^SKILL:\s*(.+?)\s*$", response, re.MULTILINE)
        parallel_match = re.search(r"^PARALLEL:\s*(.+?)\s*$", response, re.MULTILINE)
        rationale_match = re.search(r"^RATIONALE:\s*(.+?)\s*$", response, re.MULTILINE | re.DOTALL)

        skill = skill_match.group(1).strip() if skill_match else "NONE"
        parallel = parallel_match.group(1).strip() if parallel_match else "NONE"
        rationale = rationale_match.group(1).strip() if rationale_match else ""

        if skill.upper() == "NONE":
            skill = None
        if parallel.upper() == "NONE":
            parallel = None

        decision = PlannerDecision(
            skill_name=skill,
            rationale=rationale,
            parallel_skill=parallel,
            raw_response=response,
        )

        self.log.append({
            "mode": "planner",
            "user_message": user_message[:200],
            "brief_summary": brief_summary[:200] if brief_summary else "",
            "decision": skill,
            "parallel": parallel,
            "rationale": rationale,
        })

        return decision

    # ── Evaluator mode: "does brief answer the question?" ──────────────

    def evaluator(
        self,
        user_message: str,
        brief_summary: str,
        brief_entities: int = 0,
        brief_sources: int = 0,
    ) -> EvaluatorDecision:
        """Ask the observer if the brief is sufficient.

        Returns YES (cognitive rest, H^Ψ=0), NO (more work needed), or
        REFRACT (a skill failed and we need an alternative path).
        """
        system_prompt = (
            "You are the observer of your own work, in EVALUATOR mode.\n"
            "Your job: decide if your current brief is sufficient to answer the user's question.\n\n"
            "RULES:\n"
            "1. This is YOUR work. You will compose the final answer from this brief.\n"
            "2. Answer YES if you can write a complete, grounded answer from this brief.\n"
            "3. Answer NO if more skill invocations are needed (be specific about the gap).\n"
            "4. Answer REFRACT only if a previous skill failed and you need an alternative path.\n"
            "5. Do NOT pad — if the brief is sufficient, say YES even if it's short.\n"
            "6. Do NOT over-gather — if you have enough to answer confidently, say YES.\n"
        )

        user_prompt = (
            f"USER QUESTION:\n{user_message}\n\n"
            f"YOUR CURRENT BRIEF:\n{brief_summary or '(empty)'}\n\n"
            f"STATS:\n"
            f"  Entities gathered: {brief_entities}\n"
            f"  Sources gathered: {brief_sources}\n\n"
            "Respond in EXACTLY this format:\n"
            "DECISION: <YES | NO | REFRACT>\n"
            "GAP: <if NO, describe what's missing in one sentence; if YES, write 'none'>\n"
            "RATIONALE: <one sentence>\n"
        )

        response = self._call_llm(system_prompt, user_prompt)
        if not response:
            # Default to NO if LLM failed — safer than assuming YES
            return EvaluatorDecision(
                decision="NO",
                rationale="LLM call failed, defaulting to NO",
                gap_description="LLM unavailable",
                raw_response="",
            )

        decision_match = re.search(r"^DECISION:\s*(\w+)\s*$", response, re.MULTILINE)
        gap_match = re.search(r"^GAP:\s*(.+?)\s*$", response, re.MULTILINE | re.DOTALL)
        rationale_match = re.search(r"^RATIONALE:\s*(.+?)\s*$", response, re.MULTILINE | re.DOTALL)

        decision = decision_match.group(1).upper() if decision_match else "NO"
        if decision not in ("YES", "NO", "REFRACT"):
            decision = "NO"  # default safe

        gap = gap_match.group(1).strip() if gap_match else ""
        rationale = rationale_match.group(1).strip() if rationale_match else ""

        result = EvaluatorDecision(
            decision=decision,
            rationale=rationale,
            gap_description=gap,
            raw_response=response,
        )

        self.log.append({
            "mode": "evaluator",
            "user_message": user_message[:200],
            "brief_summary": brief_summary[:200] if brief_summary else "",
            "decision": decision,
            "gap": gap,
            "rationale": rationale,
        })

        return result

    # ── Layer mode: "fact / hypothesis / thesis?" ──────────────────────

    def layer_for_entity(
        self,
        entity_name: str,
        entity_type: str,
        skill_origin: str,
        user_message: str,
        context_snippet: str = "",
    ) -> str:
        """Ask the observer to assign a layer to an entity.

        Returns one of: 'fact', 'hypothesis', 'thesis'.
        """
        system_prompt = (
            "You are the observer, in LAYER-ASSIGNMENT mode.\n"
            "Your job: decide which layer of reality an entity belongs to.\n\n"
            "LAYERS:\n"
            "- FACT: verified real-world knowledge (Paris is capital of France)\n"
            "- HYPOTHESIS: speculation about the real world (GPT-5 might release 2026)\n"
            "- THESIS: fictional canon, belongs to a world (Arelia's magic is lunar)\n\n"
            "RULES:\n"
            "1. Binary choice — pick exactly ONE layer.\n"
            "2. If the entity is from a creative writing / RPG / fictional context, pick THESIS.\n"
            "3. If the entity is a 'what if' / speculation / prediction, pick HYPOTHESIS.\n"
            "4. Otherwise, pick FACT.\n"
        )

        user_prompt = (
            f"USER QUESTION (context):\n{user_message}\n\n"
            f"ENTITY:\n  name: {entity_name}\n  type: {entity_type}\n  from skill: {skill_origin}\n\n"
            f"CONTEXT SNIPPET (if any):\n{context_snippet[:500]}\n\n"
            "Respond in EXACTLY this format:\n"
            "LAYER: <FACT | HYPOTHESIS | THESIS>\n"
            "RATIONALE: <one sentence>\n"
        )

        response = self._call_llm(system_prompt, user_prompt)
        if not response:
            return "fact"  # default safe

        layer_match = re.search(r"^LAYER:\s*(\w+)\s*$", response, re.MULTILINE)
        layer = layer_match.group(1).lower() if layer_match else "fact"
        if layer not in ("fact", "hypothesis", "thesis"):
            layer = "fact"

        self.log.append({
            "mode": "layer",
            "entity_name": entity_name,
            "entity_type": entity_type,
            "skill_origin": skill_origin,
            "decision": layer,
        })

        return layer

    # ── World assignment mode ──────────────────────────────────────────

    def world_for_entity(
        self,
        entity_name: str,
        user_message: str,
        existing_worlds: list[str],
    ) -> Optional[str]:
        """If layer is THESIS, decide which world this entity belongs to.

        Returns:
            - Existing world name (if observer matches it)
            - New world name (if observer creates one)
            - None if observer says no world needed
        """
        if not existing_worlds:
            worlds_str = "(none yet)"
        else:
            worlds_str = ", ".join(existing_worlds)

        system_prompt = (
            "You are the observer, in WORLD-ASSIGNMENT mode.\n"
            "Your job: decide which fictional world an entity belongs to.\n\n"
            "RULES:\n"
            "1. Only thesis-layer entities need worlds.\n"
            "2. If the entity clearly matches an existing world (e.g. user mentioned 'Arelia'), pick it.\n"
            "3. If no existing world fits and a new world is warranted, propose a name (lowercase, hyphenated).\n"
            "4. If the entity is too generic to assign, return NONE.\n"
        )

        user_prompt = (
            f"USER QUESTION:\n{user_message}\n\n"
            f"ENTITY: {entity_name}\n\n"
            f"EXISTING WORLDS: {worlds_str}\n\n"
            "Respond in EXACTLY this format:\n"
            "WORLD: <existing_name | new_name | NONE>\n"
            "RATIONALE: <one sentence>\n"
        )

        response = self._call_llm(system_prompt, user_prompt)
        if not response:
            return None

        world_match = re.search(r"^WORLD:\s*(.+?)\s*$", response, re.MULTILINE)
        world = world_match.group(1).strip() if world_match else "NONE"
        if world.upper() == "NONE":
            return None
        # Sanitize
        world = re.sub(r"[^\w\-]", "-", world.lower()).strip("-")
        if not world:
            return None

        self.log.append({
            "mode": "world",
            "entity_name": entity_name,
            "decision": world,
        })

        return world

    # ── Refraction mode: "No Excuses" protocol ─────────────────────────

    def refraction(
        self,
        failed_skill: str,
        capability: str,
        gap_description: str,
        alternatives: list[str],
    ) -> RefractionDecision:
        """When a skill fails, decide: refract, absorb, or escalate?

        REFRACT: try an alternative skill for the same capability
        ABSORB:  accept the partial result and continue
        ESCALATE: invoke LLM directly with the failed skill's SKILL.md
        """
        alts_str = ", ".join(alternatives) if alternatives else "(none available)"

        system_prompt = (
            "You are the observer, in REFRACTION mode (No Excuses protocol).\n"
            "A skill has failed. You must decide how to proceed.\n\n"
            "RULES:\n"
            "1. REFRACT: try an alternative skill for the same capability.\n"
            "2. ABSORB: accept the partial result and continue (if it's good enough).\n"
            "3. ESCALATE: invoke LLM directly with the failed skill's methodology as system prompt.\n"
            "4. Never return 'I can't do this' — always pick one of the three.\n"
            "5. Prefer REFRACT if alternatives exist. Prefer ABSORB if partial result is usable. ESCALATE only as last resort.\n"
        )

        user_prompt = (
            f"FAILED SKILL: {failed_skill}\n"
            f"CAPABILITY: {capability}\n"
            f"GAP REMAINING: {gap_description}\n"
            f"ALTERNATIVE SKILLS: {alts_str}\n\n"
            "Respond in EXACTLY this format:\n"
            "ACTION: <REFRACT | ABSORB | ESCALATE>\n"
            "ALTERNATIVE: <skill_name_if_REFRACT_else_NONE>\n"
            "RATIONALE: <one sentence>\n"
        )

        response = self._call_llm(system_prompt, user_prompt)
        if not response:
            # Default to ABSORB on LLM failure — least disruptive
            return RefractionDecision(action="ABSORB", rationale="LLM unavailable, absorbing partial")

        action_match = re.search(r"^ACTION:\s*(\w+)\s*$", response, re.MULTILINE)
        alt_match = re.search(r"^ALTERNATIVE:\s*(.+?)\s*$", response, re.MULTILINE)
        rationale_match = re.search(r"^RATIONALE:\s*(.+?)\s*$", response, re.MULTILINE | re.DOTALL)

        action = action_match.group(1).upper() if action_match else "ABSORB"
        if action not in ("REFRACT", "ABSORB", "ESCALATE"):
            action = "ABSORB"

        alt = alt_match.group(1).strip() if alt_match else "NONE"
        if alt.upper() == "NONE":
            alt = None

        rationale = rationale_match.group(1).strip() if rationale_match else ""

        result = RefractionDecision(
            action=action,
            alternative_skill=alt,
            rationale=rationale,
            raw_response=response,
        )

        self.log.append({
            "mode": "refraction",
            "failed_skill": failed_skill,
            "action": action,
            "alternative": alt,
            "rationale": rationale,
        })

        return result

    # ── Logging / introspection ────────────────────────────────────────

    def get_log(self) -> list[dict]:
        """Return the full observer log (for --observer-log CLI)."""
        return self.log

    def clear_log(self) -> None:
        self.log.clear()

    def summary(self) -> dict:
        """Aggregate stats from the log."""
        by_mode = {}
        for entry in self.log:
            mode = entry.get("mode", "unknown")
            by_mode[mode] = by_mode.get(mode, 0) + 1
        return {
            "total_calls": len(self.log),
            "by_mode": by_mode,
        }


# ─── CLI (for testing) ─────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Observer test harness")
    parser.add_argument("--planner", action="store_true", help="Test planner mode")
    parser.add_argument("--evaluator", action="store_true", help="Test evaluator mode")
    parser.add_argument("--message", "-m", help="User message")
    parser.add_argument("--brief", help="Brief summary")
    parser.add_argument("--candidates", help="JSON file with candidate skills")
    args = parser.parse_args()

    obs = Observer()

    if args.planner:
        if not args.message:
            print("Need --message for planner mode")
            sys.exit(1)
        candidates = []
        if args.candidates:
            candidates = json.loads(Path(args.candidates).read_text())
        else:
            # default test candidates
            candidates = [
                {"name": "web-search", "description": "Search the web for current info"},
                {"name": "blog-writer", "description": "Generate long-form content"},
                {"name": "pdf", "description": "Render PDF documents"},
            ]
        decision = obs.planner(
            user_message=args.message,
            brief_summary=args.brief or "",
            candidate_skills=candidates,
        )
        print(f"SKILL: {decision.skill_name}")
        print(f"PARALLEL: {decision.parallel_skill}")
        print(f"RATIONALE: {decision.rationale}")
        print(f"\nRaw response:\n{decision.raw_response}")

    elif args.evaluator:
        if not args.message:
            print("Need --message for evaluator mode")
            sys.exit(1)
        decision = obs.evaluator(
            user_message=args.message,
            brief_summary=args.brief or "",
            brief_entities=5,
            brief_sources=3,
        )
        print(f"DECISION: {decision.decision}")
        print(f"GAP: {decision.gap_description}")
        print(f"RATIONALE: {decision.rationale}")

    else:
        print("Use --planner or --evaluator. See --help.")
