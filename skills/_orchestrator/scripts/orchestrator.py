#!/usr/bin/env python3
"""
orchestrator.py — Top-level Orchestrator for the skills ecosystem.

Combines: SkillRegistry → Planner → Executor → Validator (built-in) → Aggregator.

Mirrors the 5-phase POLER[Ψ] cycle:
  ℘ Percept: parse user query
  O  Obraz:  build DAG (Planner)
  ε  Energy: cost-aware scheduler (currently sequential)
  L  Logika: validate each output (in Executor via skill's validator.py)
  Ψ  Intent: aggregate final answer (Aggregator with "report" strategy)

Usage:
    python3 orchestrator.py "analyze this PDF and make a report" --input paper.pdf
    python3 orchestrator.py "проанализируй PDF" --input book.pdf --strategy report
    python3 orchestrator.py "extract text from x.pdf" --input x.pdf --json

    # As a module:
    from orchestrator import Orchestrator
    orch = Orchestrator("/home/z/my-project/skills")
    result = orch.process("analyze notes.md", input_path="notes.md")

Author: Task 9 (manifest-based architecture), 2026-07-03
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from registry import SkillRegistry  # noqa: E402
from planner import Planner  # noqa: E402
from executor import Executor  # noqa: E402
from aggregator import Aggregator  # noqa: E402


class Orchestrator:
    """The full pipeline: registry → planner → executor → aggregator."""

    def __init__(self, skills_dir: str | Path, verbose: bool = False):
        self.skills_dir = Path(skills_dir)
        self.verbose = verbose
        self.registry = SkillRegistry(self.skills_dir)
        self.planner = Planner(self.registry)
        self.executor = Executor(self.registry, verbose=verbose)
        self.aggregator = Aggregator()

    def process(self, query: str,
                input_path: Optional[str] = None,
                extra_input: Optional[Dict[str, Any]] = None,
                strategy: str = "report",
                min_confidence: float = 0.3,
                dry_run: bool = False) -> Dict[str, Any]:
        """Process a user query end-to-end.

        Args:
            query: user query in natural language
            input_path: optional input file path
            extra_input: optional dict of additional input params (e.g. {"llm": True})
            strategy: aggregation strategy ("last", "merge", "report", "files")
            min_confidence: if a skill returns confidence below this, mark as
                            failed and try to continue with remaining skills
            dry_run: if True, only build the plan and return it (don't execute)

        Returns:
            {
              "status": "success" | "partial" | "error",
              "confidence": 0.0-1.0,
              "data": {
                "plan": {...},      # from planner
                "results": {...},   # skill_name → output envelope
                "final": {...},     # from aggregator
                "elapsed_sec": 0.0
              }
            }
        """
        t0 = time.time()

        # ── ℘ Percept + O Obraz: Build the plan ──────────────────────
        if self.verbose:
            sys.stderr.write(f"\n[orchestrator] Planning: {query!r}\n")
        plan = self.planner.plan(query, input_path=input_path, verbose=self.verbose)

        if not plan["dag"]:
            return self._error(
                "No skills matched the query. Available: "
                + ", ".join(self.registry.list_skills()[:10]) + ", ...",
                plan=plan, elapsed=time.time() - t0
            )

        if dry_run:
            return {
                "status": "success",
                "confidence": 1.0,
                "data": {
                    "plan": plan,
                    "results": {},
                    "final": None,
                    "dry_run": True,
                    "elapsed_sec": round(time.time() - t0, 3),
                },
            }

        # ── ε Energy + L Logika: Execute the DAG ────────────────────
        results: Dict[str, Dict[str, Any]] = {}
        dag = plan["dag"]
        # Build input data for the first skill
        current_input: Dict[str, Any] = {}
        if input_path:
            current_input["input"] = input_path
        if extra_input:
            current_input.update(extra_input)

        for i, skill_name in enumerate(dag):
            if self.verbose:
                sys.stderr.write(
                    f"\n[orchestrator] ▶ Stage {i+1}/{len(dag)}: "
                    f"{skill_name}\n"
                )

            # Skip skill if its triggers don't match the current input file
            # (this prevents e.g. pdf-ocr from running on a .md file just
            # because poler-toolkit declared it as a dependency).
            current_input_path = current_input.get("input", "")
            if current_input_path and current_input_path != "-":
                ext = Path(current_input_path).suffix.lower()
                if ext:
                    manifest = self.registry.get_manifest(skill_name)
                    trigger_exts = manifest.get("triggers", {}).get(
                        "file_extensions", [])
                    # Special case: poler-toolkit handles many formats and
                    # is the general analyzer — let it always run.
                    if (skill_name != "poler-toolkit"
                            and trigger_exts
                            and ext not in trigger_exts
                            and ext not in (".txt",)):  # universal fallback
                        if self.verbose:
                            sys.stderr.write(
                                f"[orchestrator] ⏭ {skill_name} skipped "
                                f"(ext {ext} not in triggers {trigger_exts[:3]})\n"
                            )
                        results[skill_name] = {
                            "status": "skipped",
                            "confidence": 1.0,
                            "data": None,
                            "error": f"Extension {ext} not in skill triggers",
                        }
                        continue

            # For non-first skills, pipe the previous skill's text output
            # as input to the next skill (if input not explicitly set)
            if i > 0:
                prev_output = results.get(dag[i-1], {})
                prev_data = prev_output.get("data") or {}
                # Heuristic: if previous skill produced text and current
                # skill expects input, pipe text via stdin ("-")
                if "input" not in current_input and "text" in prev_data:
                    # Write to a temp file for the next skill
                    tmp_path = self._write_temp_text(prev_data["text"])
                    current_input["input"] = tmp_path

            try:
                result = self.executor.run(skill_name, current_input)
            except Exception as e:
                result = {
                    "status": "error",
                    "confidence": 0.0,
                    "data": None,
                    "error": f"Executor crashed: {e}",
                }
                if self.verbose:
                    sys.stderr.write(
                        f"[orchestrator] ✗ {skill_name} crashed: {e}\n"
                    )

            results[skill_name] = result

            # Check confidence threshold
            conf = result.get("confidence", 0.0)
            if (isinstance(conf, (int, float)) and conf < min_confidence
                    and result.get("status") == "success"):
                if self.verbose:
                    sys.stderr.write(
                        f"[orchestrator] ⚠ {skill_name} returned low confidence "
                        f"({conf:.2f} < {min_confidence}) — continuing anyway\n"
                    )

            # Clear input for next iteration if it was a temp file
            if i > 0 and current_input.get("input", "").startswith("/tmp/orch_"):
                try:
                    Path(current_input["input"]).unlink(missing_ok=True)
                except Exception:
                    pass
                current_input.pop("input", None)

        # ── Ψ Intent: Aggregate ─────────────────────────────────────
        if self.verbose:
            sys.stderr.write(f"\n[orchestrator] Aggregating (strategy={strategy})...\n")
        final = self.aggregator.aggregate(plan, results, strategy=strategy)

        elapsed = time.time() - t0
        # Ensure data dict exists before adding metadata
        if not isinstance(final.get("data"), dict):
            final["data"] = {}
        final["data"]["elapsed_sec"] = round(elapsed, 3)
        final["data"]["plan"] = plan

        if self.verbose:
            sys.stderr.write(
                f"\n[orchestrator] ✓ Done in {elapsed:.2f}s, "
                f"status={final['status']}, confidence={final['confidence']}\n"
            )

        return final

    # -----------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------

    def _write_temp_text(self, text: str) -> str:
        """Write text to a temp file and return path."""
        import tempfile
        with tempfile.NamedTemporaryFile(
            prefix="orch_", suffix=".txt", delete=False, mode="w", encoding="utf-8"
        ) as f:
            f.write(text)
            return f.name

    def _error(self, msg: str, plan: Optional[Dict] = None,
               elapsed: float = 0.0) -> Dict[str, Any]:
        return {
            "status": "error",
            "confidence": 0.0,
            "data": {
                "plan": plan,
                "results": {},
                "final": None,
                "error": msg,
                "elapsed_sec": round(elapsed, 3),
            },
            "error": msg,
        }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(
        description="Skills Orchestrator — runs a multi-skill pipeline.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 orchestrator.py "analyze this PDF" --input paper.pdf
  python3 orchestrator.py "проанализируй PDF и сделай отчёт" --input book.pdf --strategy report
  python3 orchestrator.py "extract text from x.pdf" --input x.pdf --json
  python3 orchestrator.py --dry-run "analyze" --input x.pdf
  python3 orchestrator.py list    # list available skills
""",
    )
    ap.add_argument("query", nargs="?", help="User query in natural language")
    ap.add_argument("--input", help="Input file path")
    ap.add_argument("--skills-dir", default="/home/z/my-project/skills")
    ap.add_argument("--strategy", default="report",
                    choices=["last", "merge", "report", "files"])
    ap.add_argument("--min-confidence", type=float, default=0.3,
                    help="Warn if skill returns confidence below this")
    ap.add_argument("--llm", action="store_true",
                    help="Pass --llm flag to skills (e.g., poler-toolkit)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Only build the plan, don't execute")
    ap.add_argument("--watch", action="store_true",
                    help="Start conversation watcher mode (online layer)")
    ap.add_argument("--watch-process", metavar="MSG",
                    help="Process a single message via watcher and exit")
    ap.add_argument("--watch-brief", action="store_true",
                    help="Print current context_brief.json (raw)")
    ap.add_argument("--watch-brief-for-agent", action="store_true",
                    help="Print formatted context_brief for agent consumption")
    ap.add_argument("--pre-answer", metavar="MSG",
                    help="Pattern 2+3: print combined brief + gap-detector verdict + "
                         "query type classification for this user message. "
                         "The agent reads this BEFORE composing its reply.")
    ap.add_argument("--transient", action="store_true",
                    help="Pattern 5: mark all watcher entries as transient (purged on shutdown)")
    ap.add_argument("--session-id", default=None,
                    help="Pattern 5: explicit watcher session id")
    ap.add_argument("--json", action="store_true",
                    help="Output full JSON envelope (default: report only)")
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args()

    # ── Watcher mode (online layer) ─────────────────────────────
    if (args.watch or args.watch_process or args.watch_brief
            or args.watch_brief_for_agent or args.pre_answer):
        from watcher import ConversationWatcher
        watcher = ConversationWatcher(
            skills_dir=Path(args.skills_dir),
            verbose=args.verbose,
            session_id=args.session_id,
            transient=args.transient,
        )
        if args.watch_brief:
            print(json.dumps(watcher.get_context_brief(),
                             ensure_ascii=False, indent=2))
            return 0
        if args.watch_brief_for_agent:
            text = watcher.format_brief_for_agent()
            print(text if text else "(context_brief is empty)")
            return 0
        if args.pre_answer:
            # Pattern 2 + Pattern 3 combined: the agent's pre-answer brief.
            # This is what the agent should read BEFORE composing its reply.
            from planner import Planner
            planner = Planner(watcher.registry)

            # 1. Pattern 3: classify query type
            qtype = planner.classify_query_type(args.pre_answer)

            # 2. Pattern 2: gap-detector verdict + brief
            reason_text = watcher.format_reason_for_agent(
                args.pre_answer, timeout_sec=60
            )

            # 3. Print combined output
            print(reason_text)
            print()
            print("─" * 60)
            print("🔀 ADAPTIVE ROUTER (Pattern 3: query type)")
            print("─" * 60)
            print(f"type: {qtype['type']} (confidence={qtype['confidence']})")
            print(f"rationale: {qtype['rationale']}")
            print(f"routing: {qtype['routing']}")
            print()
            if qtype["type"] == "undefined" and qtype["routing"]["ask_user_if_ambiguous"]:
                print("→ QUERY AMBIGUOUS: ask user to clarify before doing anything.")
            elif qtype["type"] == "simple_fact":
                print("→ CHEAP PATH: 1 skill, no LLM. Fast answer.")
            elif qtype["type"] == "synthesis":
                print("→ MEDIUM PATH: 2-3 skills + LLM. Verified answer.")
            elif qtype["type"] == "creative":
                print("→ FULL PATH: up to 5 skills + LLM + Content Studio.")
            print("─" * 60)
            return 0
        if args.watch_process:
            report = watcher.process_message(args.watch_process)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0
        # --watch without sub-action: stdin loop
        sys.stderr.write(
            "[orchestrator] Watcher mode. Type messages, Ctrl-D to exit.\n"
        )
        try:
            for line in sys.stdin:
                line = line.rstrip("\n")
                if not line:
                    continue
                if line.lower() in ("exit", "quit"):
                    break
                report = watcher.process_message(line)
                sys.stderr.write(
                    f"[orchestrator] dispatched: "
                    f"{len(report.get('dispatched', []))} skill(s)\n"
                )
        except KeyboardInterrupt:
            pass
        return 0

    orch = Orchestrator(args.skills_dir, verbose=args.verbose)

    # Special mode: list available skills
    if args.query == "list":
        print(f"\n{len(orch.registry.list_skills())} skills available:\n")
        for s in orch.registry.list_skills():
            m = orch.registry.get_manifest(s)
            print(f"  {s:30s} v{m['version']:8s} pri={m['priority']:3d}  [{m['category']}]")
        return 0

    if not args.query:
        ap.error("query is required (or use 'list')")

    extra_input = {}
    if args.llm:
        extra_input["llm"] = True

    result = orch.process(
        args.query,
        input_path=args.input,
        extra_input=extra_input,
        strategy=args.strategy,
        min_confidence=args.min_confidence,
        dry_run=args.dry_run,
    )

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        # Human-readable: print the report if available
        final_data = result.get("data") or {}
        if isinstance(final_data, dict):
            # With strategy=report, the report is at data.report
            if final_data.get("report"):
                print(final_data["report"])
            elif final_data.get("final"):
                final_inner = final_data["final"]
                if isinstance(final_inner, dict) and final_inner.get("report"):
                    print(final_inner["report"])
                else:
                    print(json.dumps(final_inner, ensure_ascii=False, indent=2))
            elif result.get("status") == "error":
                print(f"\n  ✗ ERROR: {result.get('error') or final_data.get('error')}\n")
            else:
                print(json.dumps(result, ensure_ascii=False, indent=2))
        elif result.get("status") == "error":
            print(f"\n  ✗ ERROR: {result.get('error')}\n")
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))

    return 0 if result.get("status") in ("success", "partial") else 1


if __name__ == "__main__":
    sys.exit(main())
