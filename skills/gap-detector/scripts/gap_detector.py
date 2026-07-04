#!/usr/bin/env python3
"""
gap_detector.py — Pattern 2: Citation-or-decline Reasoner.

Given a user message and the current context_brief.json, uses an LLM
(z-ai chat) to determine:
  1. Which aspects of the user's question are COVERED by existing claims
     in context_brief (each with a citation back to the source skill)
  2. Which aspects are MISSING (gaps) — these are the "I don't have a
     source for this" cases
  3. For each gap, either:
     - suggest a skill that COULD fill it (e.g., "media-triage could
       transcribe the YouTube link if user provides one")
     - or emit an "ask_user" prompt that the agent should show to the
       user before attempting to answer

This is the architectural constraint from NotebookLM research:
"Build the constraint, not the algorithm." Instead of a clever gap
detector, every claim MUST cite. If no citation is possible, the system
asks the user instead of guessing.

CLI:
    python3 gap_detector.py "what did the speaker say about X?" --json
    echo "user question" | python3 gap_detector.py - --json
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# ─────────────────────────────────────────────────────────────────────
# Paths
# ─────────────────────────────────────────────────────────────────────

SKILL_DIR = Path(__file__).resolve().parent.parent
Z_AI = shutil.which("z-ai") or "/usr/local/bin/z-ai"
DEFAULT_BRIEF_PATH = Path("/home/z/my-project/.context/context_brief.json")

# Pattern 1 helper
_ORCH_SCRIPTS = SKILL_DIR.parent / "_orchestrator" / "scripts"
if str(_ORCH_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_ORCH_SCRIPTS))
try:
    from patterns.source_grounded_brief import build_brief, Claim, validate_brief
    _HAS_PATTERN1 = True
except Exception as _e:
    sys.stderr.write(f"[gap-detector] WARNING: source_grounded_brief unavailable: {_e}\n")
    _HAS_PATTERN1 = False


# ─────────────────────────────────────────────────────────────────────
# Load context_brief
# ─────────────────────────────────────────────────────────────────────

def load_context_brief(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"entries": [], "entities": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"entries": [], "entities": {}}


def extract_claims_from_brief(brief: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Flatten all claims from all entries in context_brief."""
    claims = []
    for entry in brief.get("entries", []):
        ts = entry.get("timestamp", 0)
        for skill_name, result in entry.get("results", {}).items():
            data = result.get("data") or {}
            if not isinstance(data, dict):
                continue
            for c in data.get("claims", []):
                if isinstance(c, dict):
                    # Annotate with provenance
                    c2 = dict(c)
                    c2["produced_by_skill"] = skill_name
                    c2["entry_timestamp"] = ts
                    claims.append(c2)
    return claims


def summarize_brief_for_llm(claims: List[Dict[str, Any]], max_chars: int = 4000) -> str:
    """Build a compact text summary of available claims for the LLM."""
    if not claims:
        return "(no claims available in context_brief)"
    lines = []
    total = 0
    for i, c in enumerate(claims):
        text = c.get("text", "")[:200]
        source = c.get("source", "?")
        skill = c.get("produced_by_skill", "?")
        line = f"  [{i}] src={source} via={skill}: {text}"
        if total + len(line) > max_chars:
            lines.append(f"  ... ({len(claims) - i} more claims truncated)")
            break
        lines.append(line)
        total += len(line)
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────
# LLM prompt
# ─────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a citation-or-decline Reasoner. You receive:
1. A user's message (their question or statement)
2. A list of available "claims" from the agent's context_brief — each claim has a source and a span (citation locator).

Your job is to determine what knowledge is MISSING to answer the user's message with high confidence and proper citations.

Output STRICT JSON (no markdown fences, no prose before or after):
{
  "covered_aspects": [
    {"aspect": "<short name>", "claim_indices": [0, 2], "confidence": 0.85}
  ],
  "gaps": [
    {
      "aspect": "<short name of missing knowledge>",
      "why_missing": "<one sentence>",
      "skill_suggestion": "<name of a skill that could fill this, or null>",
      "ask_user_prompt": "<question to ask user if no skill can fill it, or null>"
    }
  ],
  "confidence": 0.0-1.0,
  "verdict": "answer_with_citations" | "answer_with_caveat" | "ask_user_first" | "decline"
}

Rules:
- A claim is "covered" only if its text actually addresses the aspect.
- A "gap" is a specific thing the user asked about that NO claim addresses.
- If you can answer with at least one citation, prefer "answer_with_citations".
- If you can partially answer but some aspect is missing, use "answer_with_caveat".
- If the user's question is entirely outside the available claims, use "ask_user_first" with a clarifying question.
- Never invent claims. Only use the indices provided.
- Skill suggestions should be one of: media-triage, doc-triage, site-context-loader, web-search, agent-browser, poler-toolkit, pdf-ocr. If none fits, use null.
"""


def call_llm(user_message: str, claims_summary: str) -> Optional[Dict[str, Any]]:
    """Call z-ai chat with the gap-detection prompt. Returns parsed JSON or None."""
    user_prompt = f"""USER MESSAGE:
{user_message}

AVAILABLE CLAIMS (context_brief):
{claims_summary}

Return STRICT JSON now."""

    try:
        cmd = [
            Z_AI, "chat",
            "--prompt", user_prompt,
            "--system", SYSTEM_PROMPT,
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            sys.stderr.write(f"[gap-detector] z-ai chat failed: {r.stderr[:200]}\n")
            return None

        # z-ai CLI prints log lines (🚀 Initializing...) before the JSON envelope.
        # Find the first '{' and parse from there.
        out = r.stdout
        envelope_start = out.find("{")
        envelope = None
        if envelope_start >= 0:
            try:
                envelope = json.loads(out[envelope_start:])
            except json.JSONDecodeError:
                envelope = None

        if envelope and isinstance(envelope, dict) and "choices" in envelope:
            # Standard OpenAI-style envelope: choices[0].message.content
            content = (
                envelope.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
            )
        else:
            # Maybe z-ai printed just the content (no envelope)
            content = out

        if not content or not content.strip():
            sys.stderr.write(f"[gap-detector] empty content from LLM\n")
            return None

        # Strip markdown code fences if present
        content = content.strip()
        if content.startswith("```"):
            # Remove first fence line
            first_nl = content.find("\n")
            if first_nl > 0:
                content = content[first_nl + 1:]
            # Remove trailing fence
            if content.rstrip().endswith("```"):
                content = content.rstrip()[:-3].rstrip()

        # Now find the JSON object in the cleaned content
        start = content.find("{")
        if start < 0:
            sys.stderr.write(f"[gap-detector] no JSON in LLM content: {content[:200]}\n")
            return None
        # Brace-matching (handles nested objects)
        depth = 0
        end = -1
        in_str = False
        esc = False
        for i in range(start, len(content)):
            ch = content[i]
            if esc:
                esc = False
                continue
            if ch == "\\":
                esc = True
                continue
            if ch == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if end < 0:
            sys.stderr.write(f"[gap-detector] incomplete JSON in LLM content\n")
            return None
        return json.loads(content[start:end])
    except subprocess.TimeoutExpired:
        sys.stderr.write("[gap-detector] z-ai chat timed out (60s)\n")
        return None
    except json.JSONDecodeError as e:
        sys.stderr.write(f"[gap-detector] JSON parse error: {e}\n")
        return None
    except Exception as e:
        sys.stderr.write(f"[gap-detector] LLM call failed: {e}\n")
        return None


# ─────────────────────────────────────────────────────────────────────
# Build grounded brief from LLM result
# ─────────────────────────────────────────────────────────────────────

def build_grounded_from_llm(
    user_message: str,
    llm_result: Dict[str, Any],
    available_claims: List[Dict[str, Any]],
    transient: bool = False,
    elapsed: float = 0.0,
) -> Dict[str, Any]:
    """Convert LLM JSON result into a Pattern 1 source-grounded brief."""
    covered = llm_result.get("covered_aspects", [])
    gaps = llm_result.get("gaps", [])
    confidence = float(llm_result.get("confidence", 0.5))
    verdict = llm_result.get("verdict", "answer_with_caveat")

    # Build claims list: existing claims referenced + gap claims
    brief_claims: List[Claim] = []

    # Covered aspects → reference existing claims (with citation)
    for cov in covered:
        idxs = cov.get("claim_indices", [])
        aspect = cov.get("aspect", "unknown")
        conf = float(cov.get("confidence", 0.7))
        for idx in idxs:
            if 0 <= idx < len(available_claims):
                src_claim = available_claims[idx]
                brief_claims.append(Claim(
                    text=f"[covered: {aspect}] {src_claim.get('text', '')[:200]}",
                    source=src_claim.get("source", "context_brief"),
                    span=src_claim.get("span", f"claim[{idx}]"),
                    confidence=conf,
                    tags=["covered", aspect],
                ))

    # Gaps → claims about missing knowledge (source = "gap-detector")
    suggested_skills = []
    ask_user_prompts = []
    for gap in gaps:
        aspect = gap.get("aspect", "unknown")
        why = gap.get("why_missing", "")
        skill = gap.get("skill_suggestion")
        ask = gap.get("ask_user_prompt")
        brief_claims.append(Claim(
            text=f"[gap: {aspect}] {why}",
            source="gap-detector",
            span=f"llm:gap:{aspect}",
            confidence=0.6,
            tags=["gap", aspect],
        ))
        if skill:
            suggested_skills.append({"skill": skill, "aspect": aspect, "why": why})
        if ask:
            ask_user_prompts.append(ask)

    # If no covered and no gaps, add a fallback claim
    if not brief_claims:
        brief_claims.append(Claim(
            text=f"LLM returned no covered aspects and no gaps for: {user_message[:100]}",
            source="gap-detector",
            span="llm:empty",
            confidence=0.3,
            tags=["empty"],
        ))

    aspects_queried = (
        {c.get("aspect", "unknown") for c in covered} |
        {g.get("aspect", "unknown") for g in gaps} |
        {"user_intent"}
    )
    aspects_covered = {c.get("aspect", "unknown") for c in covered}

    # Build brief summary text
    summary_lines = [
        f"🧠 gap-detector: verdict={verdict}, confidence={confidence:.2f}",
        f"  - covered aspects: {len(covered)} | gaps: {len(gaps)}",
    ]
    if suggested_skills:
        summary_lines.append(f"  - suggested skills: {', '.join(s['skill'] for s in suggested_skills)}")
    if ask_user_prompts:
        summary_lines.append(f"  - ASK USER: {' / '.join(ask_user_prompts)[:200]}")
    if verdict == "answer_with_citations":
        summary_lines.append("  → agent can answer with citations from context_brief")
    elif verdict == "answer_with_caveat":
        summary_lines.append("  → agent should answer but flag what's missing")
    elif verdict == "ask_user_first":
        summary_lines.append("  → agent should ask user before answering")
    else:  # decline
        summary_lines.append("  → agent should decline (no sources)")
    brief_text = "\n".join(summary_lines)

    # Build Pattern 1 brief
    if _HAS_PATTERN1:
        try:
            return build_brief(
                summary=brief_text,
                claims=brief_claims,
                aspects_queried=sorted(aspects_queried),
                aspects_covered=sorted(aspects_covered),
                sources_used=1 + (1 if covered else 0),
                sources_total=2,
                transient=transient,
                extra={
                    "gaps": gaps,
                    "suggested_skills": suggested_skills,
                    "ask_user": " | ".join(ask_user_prompts) if ask_user_prompts else None,
                    "verdict": verdict,
                    "user_message": user_message[:200],
                    "extraction_meta": {
                        "elapsed_sec": round(elapsed, 2),
                        "available_claims_count": len(available_claims),
                    },
                },
            )
        except Exception as e:
            sys.stderr.write(f"[gap-detector] grounded brief build failed: {e}\n")

    # Fallback
    return {
        "brief": brief_text,
        "claims": [c.to_dict() if hasattr(c, "to_dict") else c for c in brief_claims],
        "coverage": {
            "sources_used": 1,
            "sources_total": 2,
            "aspects_queried": sorted(aspects_queried),
            "aspects_covered": sorted(aspects_covered),
            "unanswered_aspects": sorted(aspects_queried - aspects_covered),
            "transient": bool(transient),
        },
        "gaps": gaps,
        "suggested_skills": suggested_skills,
        "ask_user": " | ".join(ask_user_prompts) if ask_user_prompts else None,
        "verdict": verdict,
        "extraction_meta": {"elapsed_sec": round(elapsed, 2)},
    }


# ─────────────────────────────────────────────────────────────────────
# Main detect
# ─────────────────────────────────────────────────────────────────────

def _error_envelope(msg: str, confidence: float = 0.0) -> Dict[str, Any]:
    return {"status": "error", "confidence": confidence, "data": None, "error": msg}


def detect(input_value: str,
           context_brief_path: Path = DEFAULT_BRIEF_PATH,
           transient: bool = False) -> Dict[str, Any]:
    """Run gap detection pipeline."""
    t_start = time.time()

    # 1. Get user message
    if input_value == "-":
        try:
            user_message = sys.stdin.read().strip()
        except Exception:
            user_message = ""
        if not user_message:
            return _error_envelope("No message provided on stdin")
    else:
        user_message = input_value.strip()

    if not user_message:
        return _error_envelope("Empty message")

    # 2. Load context_brief and extract claims
    brief = load_context_brief(context_brief_path)
    available_claims = extract_claims_from_brief(brief)

    # 3. Summarize claims for LLM
    claims_summary = summarize_brief_for_llm(available_claims)

    # 4. Call LLM
    llm_result = call_llm(user_message, claims_summary)
    elapsed = time.time() - t_start

    if llm_result is None:
        # LLM failed — fall back to a trivial "no gaps detected" answer
        # This is the citation-or-decline fallback: if we can't reason about gaps,
        # we still produce a brief but with low confidence and an "ask_user" prompt.
        if _HAS_PATTERN1:
            fallback_claim = Claim(
                text=f"Gap-detector LLM call failed — cannot determine coverage for: {user_message[:100]}",
                source="gap-detector",
                span="llm:fallback",
                confidence=0.3,
                tags=["fallback"],
            )
            try:
                data = build_brief(
                    summary=f"🧠 gap-detector: LLM unavailable, fallback verdict=ask_user_first\n"
                            f"  → agent should consider asking user to clarify",
                    claims=[fallback_claim],
                    aspects_queried=["user_intent"],
                    aspects_covered=[],
                    transient=transient,
                    extra={
                        "gaps": [],
                        "suggested_skills": [],
                        "ask_user": "Could you clarify what you'd like to know?",
                        "verdict": "ask_user_first",
                        "user_message": user_message[:200],
                        "extraction_meta": {
                            "elapsed_sec": round(elapsed, 2),
                            "available_claims_count": len(available_claims),
                            "llm_failed": True,
                        },
                    },
                )
                return {"status": "success", "confidence": 0.3, "data": data, "error": None}
            except Exception:
                pass
        return _error_envelope("LLM call failed and fallback failed", confidence=0.2)

    # 5. Build grounded brief from LLM result
    data = build_grounded_from_llm(
        user_message=user_message,
        llm_result=llm_result,
        available_claims=available_claims,
        transient=transient,
        elapsed=elapsed,
    )
    confidence = float(llm_result.get("confidence", 0.5))

    return {
        "status": "success",
        "confidence": round(confidence, 2),
        "data": data,
        "error": None,
    }


# ─────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────

def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(
        description="gap-detector — Pattern 2 citation-or-decline Reasoner",
    )
    ap.add_argument("input", help="User message or '-' for stdin")
    ap.add_argument("--context-brief-path", default=str(DEFAULT_BRIEF_PATH))
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--transient", action="store_true")
    args = ap.parse_args()
    result = detect(
        args.input,
        context_brief_path=Path(args.context_brief_path),
        transient=args.transient,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
