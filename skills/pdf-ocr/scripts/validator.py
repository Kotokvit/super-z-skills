#!/usr/bin/env python3
"""
validator.py — Output validator for pdf-ocr skill.

Validates that ocr_pdf.py output conforms to the manifest's output schema.

Usage:
  from validator import validate
  ok, msg = validate(output_dict)

  python3 validator.py path/to/output.json
  python3 validator.py --stdin < output.json

Validation rules (from ../manifest.json → outputs.schema):
  1. status must be "success" or "error"
  2. confidence in [0, 1]
  3. data.text must be a non-empty string (when status=success)
  4. data.meta must have:
     - ocr_used (bool)
     - pages_total (int >= 0)
     - extraction_method (str)
  5. confidence < 0.4 → FAIL (OCR produced too little text per page)

Author: Task 9 (manifest-based architecture), 2026-07-03
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, Tuple

MIN_CONFIDENCE = 0.4
REQUIRED_META_FIELDS = ["ocr_used", "pages_total", "extraction_method"]


def validate(output: Dict[str, Any]) -> Tuple[bool, str]:
    """Validate pdf-ocr output against manifest schema."""
    if not isinstance(output, dict):
        return False, "Output is not a dict"

    status = output.get("status")
    if status not in ("success", "error"):
        return False, f"status must be 'success' or 'error', got {status!r}"

    confidence = output.get("confidence")
    if not isinstance(confidence, (int, float)):
        return False, f"confidence must be a number, got {type(confidence).__name__}"
    if not (0.0 <= confidence <= 1.0):
        return False, f"confidence out of [0,1]: {confidence}"

    if status == "error":
        err = output.get("error")
        if not err or not isinstance(err, str):
            return False, "status=error but no 'error' string field"
        return True, f"Validated error envelope: {err[:80]}"

    data = output.get("data")
    if not isinstance(data, dict):
        return False, "status=success but data is not a dict"

    # text required
    text = data.get("text")
    if not isinstance(text, str):
        return False, "data.text must be a string"
    if not text.strip():
        return False, "data.text is empty — PDF extraction produced no text"

    # meta required
    meta = data.get("meta")
    if not isinstance(meta, dict):
        return False, "data.meta must be a dict"
    for f in REQUIRED_META_FIELDS:
        if f not in meta:
            return False, f"Missing data.meta.{f}"

    if not isinstance(meta["ocr_used"], bool):
        return False, "data.meta.ocr_used must be bool"
    if not isinstance(meta["pages_total"], int) or meta["pages_total"] < 0:
        return False, f"data.meta.pages_total must be non-negative int"

    # Confidence threshold (lower bar for OCR since it's noisier)
    if confidence < MIN_CONFIDENCE:
        return False, (
            f"confidence={confidence} < {MIN_CONFIDENCE} — "
            f"OCR output too sparse ({len(text)} chars on "
            f"{meta.get('pages_processed', meta['pages_total'])} pages)"
        )

    method = meta["extraction_method"]
    chars_per_page = meta.get("chars_per_page_avg", 0)
    return True, (
        f"Validated: confidence={confidence}, "
        f"method={method}, "
        f"{len(text)} chars, "
        f"~{chars_per_page}/page"
    )


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Validate pdf-ocr output")
    ap.add_argument("input", nargs="?")
    ap.add_argument("--stdin", action="store_true")
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
    print(("VALID  " if ok else "INVALID") + f": {msg}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
