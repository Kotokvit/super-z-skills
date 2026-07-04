#!/usr/bin/env python3
"""
validator.py — Output validator for poler-toolkit skill.

Validates that ingest() / ingest.py output conforms to the manifest's
output schema. Used by the Orchestrator's Validator stage (Task 9).

Usage:
  # As a module (called by Orchestrator):
  from validator import validate
  ok, msg = validate(output_dict)

  # As a CLI (called manually):
  python3 validator.py path/to/output.json
  python3 validator.py --stdin < output.json

Validation rules (from ../manifest.json → outputs.schema):
  1. status must be "success" or "error"
  2. confidence must be a number in [0, 1]
  3. data must be present (when status=success) with:
     - meta (dict)
       - source, source_type, format, chars
     - theme (dict)
       - name, scores, method
  4. When status=error, error must be a non-empty string
  5. confidence < min_confidence (0.3) → WARN (returns ok=True but msg warns)
  6. confidence < 0.3 AND no LLM semantic → FAIL (likely garbage)

Exit codes:
  0 — valid
  1 — invalid
  2 — validator error (couldn't run)

Author: Task 9 (manifest-based architecture), 2026-07-03
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, Tuple

# ---------------------------------------------------------------------------
# Config (mirrors ../manifest.json — kept in sync manually)
# ---------------------------------------------------------------------------

MIN_CONFIDENCE = 0.3       # hard fail below this
WARN_CONFIDENCE = 0.5      # warn below this
REQUIRED_DATA_FIELDS = ["meta", "theme"]
REQUIRED_META_FIELDS = ["source", "source_type", "format", "chars"]
REQUIRED_THEME_FIELDS = ["name", "scores", "method"]


# ---------------------------------------------------------------------------
# Core validator
# ---------------------------------------------------------------------------

def validate(output: Dict[str, Any]) -> Tuple[bool, str]:
    """Validate poler-toolkit output against manifest schema.

    Returns:
        (ok, message) — ok=True if valid, False if invalid.
        message describes the first failure or success reason.
    """
    if not isinstance(output, dict):
        return False, "Output is not a dict"

    # 1. status
    status = output.get("status")
    if status not in ("success", "error"):
        return False, f"status must be 'success' or 'error', got {status!r}"

    # 2. confidence
    confidence = output.get("confidence")
    if not isinstance(confidence, (int, float)):
        return False, f"confidence must be a number, got {type(confidence).__name__}"
    if not (0.0 <= confidence <= 1.0):
        return False, f"confidence out of [0,1]: {confidence}"

    # 3. error case
    if status == "error":
        err = output.get("error")
        if not err or not isinstance(err, str):
            return False, "status=error but no 'error' string field"
        return True, f"Validated error envelope: {err[:80]}"

    # 4. success case — validate data
    data = output.get("data")
    if not isinstance(data, dict):
        return False, "status=success but data is not a dict"

    for field in REQUIRED_DATA_FIELDS:
        if field not in data:
            return False, f"Missing data.{field}"

    # 5. meta sub-validation
    meta = data["meta"]
    if not isinstance(meta, dict):
        return False, "data.meta is not a dict"
    for f in REQUIRED_META_FIELDS:
        if f not in meta:
            return False, f"Missing data.meta.{f}"
    if not isinstance(meta["chars"], int) or meta["chars"] < 0:
        return False, f"data.meta.chars must be non-negative int, got {meta['chars']}"

    # 6. theme sub-validation
    theme = data["theme"]
    if not isinstance(theme, dict):
        return False, "data.theme is not a dict"
    for f in REQUIRED_THEME_FIELDS:
        if f not in theme:
            return False, f"Missing data.theme.{f}"
    if not isinstance(theme["scores"], dict):
        return False, "data.theme.scores must be a dict"

    # 7. keywords (optional but if present, must be list[str])
    kw = data.get("keywords", [])
    if not isinstance(kw, list):
        return False, "data.keywords must be a list"
    if kw and not all(isinstance(k, str) for k in kw):
        return False, "data.keywords must be list[str]"

    # 8. clusters (optional but if present, must be list[dict])
    cl = data.get("clusters", [])
    if not isinstance(cl, list):
        return False, "data.clusters must be a list"
    if cl and not all(isinstance(c, dict) for c in cl):
        return False, "data.clusters must be list[dict]"

    # 9. confidence warning bands
    if confidence < MIN_CONFIDENCE:
        # Hard fail only if no LLM semantic — otherwise just warn
        if not theme.get("semantic"):
            return False, (
                f"confidence={confidence} < {MIN_CONFIDENCE} and no LLM semantic "
                f"backup — result likely garbage"
            )
        return True, (
            f"VALID but low confidence={confidence} (LLM semantic present, "
            f"consider re-running with --llm)"
        )
    if confidence < WARN_CONFIDENCE:
        return True, (
            f"VALID with warning: confidence={confidence} < {WARN_CONFIDENCE} "
            f"(consider using --llm for higher reliability)"
        )

    # 10. text presence check (only if --no-text wasn't used)
    if "text" not in data:
        # This is OK if --no-text was used, but flag it
        return True, (
            f"Validated envelope (confidence={confidence}, "
            f"text omitted — likely --no-text mode)"
        )

    return True, (
        f"Validated: confidence={confidence}, theme='{theme['name']}', "
        f"{len(kw)} keywords, {len(cl)} clusters, "
        f"{meta.get('chars', 0)} chars"
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Validate poler-toolkit output")
    ap.add_argument("input", nargs="?", help="Path to JSON output file")
    ap.add_argument("--stdin", action="store_true",
                    help="Read JSON from stdin instead of file")
    ap.add_argument("--strict", action="store_true",
                    help="Treat warnings as failures")
    args = ap.parse_args()

    if args.stdin:
        raw = sys.stdin.read()
    elif args.input:
        raw = Path(args.input).read_text(encoding="utf-8")
    else:
        ap.error("Either provide INPUT path or --stdin")

    try:
        output = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"INVALID: not valid JSON: {e}")
        return 1

    ok, msg = validate(output)

    # In strict mode, treat low-confidence warnings as failures
    if args.strict and "warning" in msg.lower() and ok:
        ok = False
        msg = f"[STRICT] {msg}"

    print(("VALID  " if ok else "INVALID") + f": {msg}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
