#!/usr/bin/env python3
"""
orchestrator_v21.py — Binary Observer-Driven Orchestration Loop (v2.1)

Replaces the v2.0 rule-based planner with the binary observer architecture
described in doc 04-binary-observer-final.md.

Core principles:
    1. Binary Foundation (Principle #4): 0 or 1. No continuous degrees.
    2. Reality is Layered (Principle #5): fact / hypothesis / thesis, never cross-checked.
    3. Observer is the Agent (Principle #6): the LLM itself decides when to rest.

The loop:
    1. Watcher detects signals (binary: present / absent)
    2. Observer (planner mode): "which ONE skill next?" → skill name or NONE
    3. Executor runs the skill (or 2 if observer identified parallel sub-goals)
    4. Context builder merges into brief (layer-aware, world-aware)
    5. Observer (evaluator mode): "does brief answer the question?" YES / NO / REFRACT
       - YES → cognitive rest (H^Ψ = 0), return brief
       - NO → loop back to step 2
       - REFRACT → trigger "No Excuses" protocol, try alternative skill
    6. Safety net: if observer says NO 10 times in a row, force rest with warning.

NO HARDCODED CAPS. NO PRESET BUDGETS. The observer decides everything.

API:
    orch = BinaryOrchestrator(skills_root=Path("/path/to/skills"))
    brief = orch.run("user message here")
    print(orch.observer_log())  # for --observer-log CLI

Author: Super-Z team, 2026-07-04
"""
from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Sibling modules
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR.parent.parent / "_shared"))

from registry import SkillRegistry  # noqa: E402
from executor import Executor  # noqa: E402
from watcher import ConversationWatcher  # noqa: E402
from memory_graph import MemoryGraph  # noqa: E402
from worlds import WorldManager  # noqa: E402
from observer import Observer, EvaluatorDecision, PlannerDecision  # noqa: E402
from context_builder import ContextBuilder, SkillResult  # noqa: E402


@dataclass
class OrchestrationResult:
    """Final result of running the binary orchestration loop."""
    brief: dict
    cycles: int
    observer_log: list[dict]
    final_decision: str  # YES (rest) | SAFETY_NET (forced rest)
    skills_invoked: list[str]
    duration_sec: float
    warnings: list[str] = field(default_factory=list)


class BinaryOrchestrator:
    """The v2.1 orchestration loop, driven by the binary observer."""

    SAFETY_NET_CYCLES = 10  # Force rest after 10 NOs in a row

    def __init__(
        self,
        skills_root: Optional[Path] = None,
        db_path: Optional[Path] = None,
        verbose: bool = False,
    ):
        if skills_root is None:
            skills_root = SCRIPT_DIR.parents[2] / "skills"
        if db_path is None:
            db_path = Path(".context/memory_graph.db")

        self.skills_root = Path(skills_root)
        self.registry = SkillRegistry(self.skills_root)
        self.executor = Executor(self.registry, verbose=verbose)
        self.watcher = ConversationWatcher(self.skills_root)
        self.graph = MemoryGraph(db_path)
        self.worlds = WorldManager(self.graph)
        self.context_builder = ContextBuilder(db_path)
        self.observer = Observer()

        self.verbose = verbose

    # ── Main entry point ────────────────────────────────────────────────

    def run(
        self,
        user_message: str,
        detected_signals: Optional[list[dict]] = None,
    ) -> OrchestrationResult:
        """Run the binary observer-driven orchestration loop.

        Args:
            user_message: the user's raw query
            detected_signals: optional pre-detected signals (else watcher detects)

        Returns:
            OrchestrationResult with brief, cycles, observer log, etc.
        """
        start_time = time.time()
        warnings: list[str] = []
        skills_invoked: list[str] = []
        skill_results: list[SkillResult] = []
        cycles = 0
        consecutive_nos = 0
        final_decision = "UNKNOWN"

        # Step 1: detect signals if not provided
        if detected_signals is None:
            detected_signals = self.watcher.scan(user_message)
            if self.verbose:
                print(f"[v2.1] Detected {len(detected_signals)} signals: "
                      f"{[s.get('skill', s.get('signal', '?')) for s in detected_signals]}")

        # Step 2: orchestration loop
        while True:
            cycles += 1
            if self.verbose:
                print(f"\n[v2.1] === Cycle {cycles} ===")

            # Build current brief summary for observer
            current_brief = self._build_partial_brief(
                user_message, detected_signals, skill_results,
            )
            brief_summary = current_brief.get("summary", "")
            brief_entities = len(current_brief.get("entities", []))
            brief_sources = len(current_brief.get("sources", []))

            # Observer: planner mode — which ONE skill next?
            candidates = self._get_candidate_skills(detected_signals, skills_invoked)
            planner_decision = self.observer.planner(
                user_message=user_message,
                brief_summary=brief_summary,
                candidate_skills=candidates,
            )

            if self.verbose:
                print(f"[v2.1] Planner: skill={planner_decision.skill_name}, "
                      f"parallel={planner_decision.parallel_skill}, "
                      f"rationale={planner_decision.rationale[:80]}")

            # If observer says NONE — we're done (cognitive rest by planner)
            if planner_decision.is_none:
                final_decision = "YES"
                if self.verbose:
                    print(f"[v2.1] Planner said NONE — cognitive rest by planner")
                break

            # Execute the chosen skill(s)
            skills_this_cycle = [planner_decision.skill_name]
            if planner_decision.is_parallel and planner_decision.parallel_skill:
                skills_this_cycle.append(planner_decision.parallel_skill)

            for skill_name in skills_this_cycle:
                if skill_name is None or skill_name in skills_invoked:
                    continue
                result = self._execute_skill(skill_name, user_message)
                if result is not None:
                    skill_results.append(result)
                    skills_invoked.append(skill_name)
                    if self.verbose:
                        print(f"[v2.1]   Executed {skill_name}: status={result.status}, "
                              f"confidence={result.confidence:.2f}")

            # Observer: evaluator mode — does brief answer the question?
            updated_brief = self._build_partial_brief(
                user_message, detected_signals, skill_results,
            )
            eval_decision = self.observer.evaluator(
                user_message=user_message,
                brief_summary=updated_brief.get("summary", ""),
                brief_entities=len(updated_brief.get("entities", [])),
                brief_sources=len(updated_brief.get("sources", [])),
            )

            if self.verbose:
                print(f"[v2.1] Evaluator: {eval_decision.decision}, "
                      f"gap={eval_decision.gap_description[:80]}")

            if eval_decision.is_yes:
                final_decision = "YES"
                break
            elif eval_decision.is_refract:
                # "No Excuses" — find alternative for last skill
                # For simplicity, just continue the loop (next planner call will pick alternative)
                consecutive_nos = 0
                warnings.append(
                    f"Cycle {cycles}: REFRACT — {eval_decision.gap_description}"
                )
                continue
            else:  # NO
                consecutive_nos += 1
                if consecutive_nos >= self.SAFETY_NET_CYCLES:
                    final_decision = "SAFETY_NET"
                    warnings.append(
                        f"Force rest after {consecutive_nos} consecutive NOs — "
                        f"returning partial brief"
                    )
                    if self.verbose:
                        print(f"[v2.1] Safety net triggered — force rest")
                    break

        # Build final brief
        final_brief = self._build_partial_brief(
            user_message, detected_signals, skill_results, finalize=True,
        )
        duration = time.time() - start_time

        return OrchestrationResult(
            brief=final_brief,
            cycles=cycles,
            observer_log=self.observer.get_log(),
            final_decision=final_decision,
            skills_invoked=skills_invoked,
            duration_sec=duration,
            warnings=warnings,
        )

    # ── Helpers ─────────────────────────────────────────────────────────

    def _get_candidate_skills(
        self,
        signals: list[dict],
        already_invoked: list[str],
    ) -> list[dict]:
        """Get candidate skills from signals, excluding already-invoked ones."""
        candidates = []
        seen = set(already_invoked)

        # From watcher signals
        for sig in signals:
            skill_name = sig.get("skill")
            if skill_name and skill_name not in seen:
                manifest = self.registry.get_manifest(skill_name)
                if manifest:
                    candidates.append({
                        "name": skill_name,
                        "description": manifest.get("description", ""),
                        "capabilities": manifest.get("capabilities", []),
                    })
                    seen.add(skill_name)

        # Always include LLM as fallback (if not already invoked)
        if "LLM" not in seen:
            manifest = self.registry.get_manifest("LLM")
            if manifest:
                candidates.append({
                    "name": "LLM",
                    "description": "Direct LLM chat — fallback when no specialized skill fits",
                    "capabilities": [{"name": "chat"}],
                })

        return candidates

    def _execute_skill(
        self,
        skill_name: str,
        user_message: str,
    ) -> Optional[SkillResult]:
        """Execute one skill and return a SkillResult."""
        start = time.time()
        try:
            output = self.executor.run(skill_name, {"input": user_message, "json": True})
            end = time.time()
            if output is None:
                return None
            return SkillResult(
                skill_name=skill_name,
                output=output if isinstance(output, dict) else {"status": "error", "summary": str(output)},
                started_at=start,
                finished_at=end,
            )
        except Exception as e:
            end = time.time()
            return SkillResult(
                skill_name=skill_name,
                output={
                    "status": "error",
                    "summary": f"Skill execution failed: {e}",
                    "confidence": 0.0,
                    "entities": [],
                    "relations": [],
                    "sources": [],
                    "warnings": [str(e)],
                    "artifacts": [],
                    "metrics": {},
                },
                started_at=start,
                finished_at=end,
            )

    def _build_partial_brief(
        self,
        user_message: str,
        detected_signals: list[dict],
        skill_results: list[SkillResult],
        finalize: bool = False,
    ) -> dict:
        """Build a brief from current skill results.

        If finalize=True, also queries memory for related context and detects
        contradictions (only within same layer+world).
        """
        if not skill_results:
            return {
                "schema": "context_brief/v2.1",
                "user_message": user_message,
                "detected_signals": detected_signals,
                "skills_used": [],
                "summary": "",
                "entities": [],
                "relations": [],
                "memory": {"related_entities": [], "related_relations": []},
                "sources": [],
                "artifacts": [],
                "warnings": [],
                "contradictions": [],
                "confidence": 0.0,
            }

        # Use context_builder to merge results
        brief = self.context_builder.build(
            user_message=user_message,
            detected_signals=detected_signals,
            skill_results=skill_results,
            topic=user_message[:50] if finalize else None,
        )
        brief["schema"] = "context_brief/v2.1"

        if finalize:
            # Add layer summary and active worlds
            layers = {"fact": 0, "hypothesis": 0, "thesis": 0}
            worlds_active = set()
            for e in brief.get("entities", []):
                layer = e.get("layer", "fact")
                layers[layer] = layers.get(layer, 0) + 1
                if e.get("world"):
                    worlds_active.add(e["world"])
            brief["layer_summary"] = layers
            brief["active_worlds"] = sorted(worlds_active)

        return brief

    # ── Introspection ───────────────────────────────────────────────────

    def observer_log(self) -> list[dict]:
        """Return the observer's call log (for --observer-log CLI)."""
        return self.observer.get_log()

    def clear_state(self) -> None:
        """Clear observer log between runs."""
        self.observer.clear_log()


# ─── CLI ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Super-Z v2.1 Binary Orchestrator")
    parser.add_argument("message", help="User message to process")
    parser.add_argument("--skills-root", default=str(Path(__file__).resolve().parents[2]),
                        help="Skills root directory")
    parser.add_argument("--db", default=".context/memory_graph.db",
                        help="Memory graph DB path")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Verbose output")
    parser.add_argument("--json", action="store_true",
                        help="Output as JSON")
    args = parser.parse_args()

    orch = BinaryOrchestrator(
        skills_root=Path(args.skills_root),
        db_path=Path(args.db),
        verbose=args.verbose,
    )

    result = orch.run(args.message)

    if args.json:
        print(json.dumps({
            "brief": result.brief,
            "cycles": result.cycles,
            "final_decision": result.final_decision,
            "skills_invoked": result.skills_invoked,
            "duration_sec": result.duration_sec,
            "warnings": result.warnings,
            "observer_log": result.observer_log,
        }, ensure_ascii=False, indent=2, default=str))
    else:
        print(f"\n{'='*60}")
        print(f"  Super-Z v2.1 — Binary Observer Orchestrator")
        print(f"{'='*60}")
        print(f"\nUser: {args.message}")
        print(f"\nCycles: {result.cycles}")
        print(f"Decision: {result.final_decision}")
        print(f"Duration: {result.duration_sec:.2f}s")
        print(f"Skills invoked: {', '.join(result.skills_invoked) or '(none)'}")
        print(f"\nBrief summary: {result.brief.get('summary', '(empty)')[:500]}")
        if result.warnings:
            print(f"\nWarnings:")
            for w in result.warnings:
                print(f"  - {w}")
        print(f"\nObserver calls: {len(result.observer_log)}")
        for entry in result.observer_log:
            mode = entry.get("mode", "?")
            if mode == "planner":
                print(f"  [planner]   skill={entry.get('decision')} "
                      f"parallel={entry.get('parallel')}")
            elif mode == "evaluator":
                print(f"  [evaluator] decision={entry.get('decision')} "
                      f"gap={entry.get('gap', '')[:60]}")
            elif mode == "layer":
                print(f"  [layer]     entity={entry.get('entity_name')} "
                      f"layer={entry.get('decision')}")
            elif mode == "refraction":
                print(f"  [refract]   action={entry.get('action')} "
                      f"alt={entry.get('alternative')}")
