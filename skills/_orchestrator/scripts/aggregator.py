#!/usr/bin/env python3
"""
aggregator.py — Aggregates results from multiple skills into a final answer.

Stages (mirrors Gemini's Ψ phase — Observer-Kill: strip intermediate
artifacts, return only the crystallized final answer).

Strategies:
  - "last": return the last skill's output (default)
  - "merge": merge all .data dicts into one (later skills override earlier)
  - "report": build a Markdown report summarizing all skills executed
  - "files": return a list of files produced by all skills

Usage (as a module):
    from aggregator import Aggregator
    agg = Aggregator()
    final = agg.aggregate(plan, results, strategy="report")

CLI:
    python3 aggregator.py --plan plan.json --results results.json
    python3 aggregator.py --plan plan.json --results results.json --strategy report

Author: Task 9 (manifest-based architecture), 2026-07-03
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


class Aggregator:
    """Aggregates results from a multi-skill pipeline."""

    def aggregate(self, plan: Dict[str, Any], results: Dict[str, Dict[str, Any]],
                  strategy: str = "last") -> Dict[str, Any]:
        """Aggregate results from multiple skills.

        Args:
            plan: the planner's output dict (with 'dag', 'query', etc.)
            results: dict mapping skill_name → skill_output_envelope
            strategy: one of "last", "merge", "report", "files"

        Returns:
            Aggregated envelope {status, confidence, data: {final, ...}}.
        """
        dag = plan.get("dag", [])
        if not dag:
            return {
                "status": "error",
                "confidence": 0.0,
                "data": None,
                "error": "Empty DAG — no skills to aggregate",
            }

        # Filter out failed skills (skipped skills are not failures)
        successful = {n: r for n, r in results.items()
                      if r.get("status") == "success"}
        failed = {n: r for n, r in results.items()
                  if r.get("status") == "error"}
        skipped = {n: r for n, r in results.items()
                   if r.get("status") == "skipped"}

        # Compute overall confidence (min of all skills — weakest link)
        confidences = [r.get("confidence", 0.0) for r in successful.values()]
        overall_conf = min(confidences) if confidences else 0.0

        if strategy == "last":
            return self._aggregate_last(dag, successful, failed, overall_conf, plan)
        elif strategy == "merge":
            return self._aggregate_merge(dag, successful, failed, overall_conf, plan)
        elif strategy == "report":
            return self._aggregate_report(dag, successful, failed, overall_conf, plan)
        elif strategy == "files":
            return self._aggregate_files(dag, successful, failed, overall_conf, plan)
        else:
            return {
                "status": "error",
                "confidence": 0.0,
                "data": None,
                "error": f"Unknown strategy: {strategy}",
            }

    # -----------------------------------------------------------------
    # Strategies
    # -----------------------------------------------------------------

    def _aggregate_last(self, dag, successful, failed, conf, plan):
        """Return the last successful skill's output as the final answer."""
        last_skill = None
        for s in reversed(dag):
            if s in successful:
                last_skill = s
                break

        if not last_skill:
            return {
                "status": "error",
                "confidence": 0.0,
                "data": None,
                "error": f"All skills failed: {list(failed.keys())}",
                "failed_skills": list(failed.keys()),
            }

        last_output = successful[last_skill]
        return {
            "status": "success" if not failed else "partial",
            "confidence": conf,
            "data": {
                "final": last_output.get("data"),
                "final_skill": last_skill,
                "skills_executed": list(successful.keys()),
                "skills_failed": list(failed.keys()),
                "query": plan.get("query"),
            },
        }

    def _aggregate_merge(self, dag, successful, failed, conf, plan):
        """Merge all .data dicts together (later skills override earlier)."""
        merged: Dict[str, Any] = {}
        for s in dag:
            if s in successful:
                data = successful[s].get("data") or {}
                if isinstance(data, dict):
                    merged.update(data)

        return {
            "status": "success" if not failed else "partial",
            "confidence": conf,
            "data": {
                "merged": merged,
                "skills_executed": list(successful.keys()),
                "skills_failed": list(failed.keys()),
                "query": plan.get("query"),
            },
        }

    def _aggregate_report(self, dag, successful, failed, conf, plan):
        """Build a Markdown report summarizing the pipeline execution."""
        lines: List[str] = []
        lines.append(f"# Pipeline Report\n")
        lines.append(f"**Query:** {plan.get('query', '?')}\n")
        if plan.get("input_path"):
            lines.append(f"**Input:** `{plan['input_path']}`\n")
        lines.append(f"**Skills executed:** {len(successful)}/{len(dag)}\n")
        lines.append(f"**Overall confidence:** {conf:.2f}\n")
        lines.append(f"**Rationale:** {plan.get('rationale', 'N/A')}\n")
        lines.append("\n---\n")

        lines.append("## Pipeline Stages\n")
        for i, skill in enumerate(dag, 1):
            r = successful.get(skill) or failed.get(skill, {})
            status = r.get("status", "?")
            icon = {"success": "✓", "error": "✗", "partial": "⚠"}.get(status, "?")
            conf_str = f"conf={r.get('confidence', 0):.2f}" if isinstance(r.get("confidence"), (int, float)) else "conf=?"
            lines.append(f"\n### {i}. {skill} `{icon} {status} {conf_str}`\n")

            if status == "error":
                lines.append(f"**Error:** {r.get('error', 'unknown')}\n")
            else:
                data = r.get("data") or {}
                # Include skill-specific summary
                if "meta" in data:
                    m = data["meta"]
                    lines.append(f"- format: `{m.get('format', '?')}`")
                    lines.append(f"- chars: {m.get('chars', 0)}")
                    if "ocr_used" in m:
                        lines.append(f"- OCR used: {m['ocr_used']}")
                    if "extraction_method" in m:
                        lines.append(f"- extraction method: `{m['extraction_method']}`")
                if "theme" in data:
                    t = data["theme"]
                    lines.append(f"- theme: **{t.get('name', '?')}**")
                    if t.get("semantic"):
                        lines.append(f"- semantic topic: _{t['semantic']}_")
                if "keywords" in data and data["keywords"]:
                    kw = data["keywords"][:5]
                    lines.append(f"- top keywords: {', '.join(kw)}")
                if "file" in data:
                    lines.append(f"- output file: `{data['file']}`")

            # Validation
            v = r.get("_validation", {})
            if v:
                lines.append(f"- validation: {v.get('status', '?')} — {v.get('message', '')[:120]}")

        if failed:
            lines.append("\n## Failed Skills\n")
            for s, r in failed.items():
                lines.append(f"- **{s}**: {r.get('error', 'unknown')[:200]}")

        report_md = "\n".join(lines)

        return {
            "status": "success" if not failed else "partial",
            "confidence": conf,
            "data": {
                "report": report_md,
                "skills_executed": list(successful.keys()),
                "skills_failed": list(failed.keys()),
                "query": plan.get("query"),
            },
        }

    def _aggregate_files(self, dag, successful, failed, conf, plan):
        """Collect all file paths produced by the pipeline."""
        files: List[str] = []
        for s in dag:
            if s in successful:
                data = successful[s].get("data") or {}
                if isinstance(data, dict):
                    if "file" in data and data["file"]:
                        files.append(data["file"])
                    if "files" in data and isinstance(data["files"], list):
                        files.extend(data["files"])
                    if "output_path" in data and data["output_path"]:
                        files.append(data["output_path"])

        return {
            "status": "success" if not failed else "partial",
            "confidence": conf,
            "data": {
                "files": files,
                "skills_executed": list(successful.keys()),
                "skills_failed": list(failed.keys()),
                "query": plan.get("query"),
            },
        }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Aggregate skill pipeline results")
    ap.add_argument("--plan", required=True, help="Path to plan.json")
    ap.add_argument("--results", required=True, help="Path to results.json")
    ap.add_argument("--strategy", default="report",
                    choices=["last", "merge", "report", "files"])
    args = ap.parse_args()

    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    results = json.loads(Path(args.results).read_text(encoding="utf-8"))

    agg = Aggregator()
    final = agg.aggregate(plan, results, strategy=args.strategy)
    print(json.dumps(final, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
