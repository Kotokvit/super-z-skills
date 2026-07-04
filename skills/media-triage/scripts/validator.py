#!/usr/bin/env python3
"""
validator.py — Output validator for media-triage skill.

Used by orchestrator Executor to validate the skill's output envelope.
"""
from __future__ import annotations
from typing import Any, Dict, Tuple


def validate(output: Dict[str, Any]) -> Tuple[bool, str]:
    """Validate media-triage output.

    Returns (ok, message).
    """
    if not isinstance(output, dict):
        return False, "Output is not a dict"

    status = output.get("status")
    if status not in ("success", "error", "skipped"):
        return False, f"Invalid status: {status!r}"

    if status == "error":
        err = output.get("error")
        if not err:
            return False, "status=error but no 'error' field"
        return True, f"Validated: error state — {err[:100]}"

    if status == "skipped":
        return True, "Validated: skipped"

    # status == success
    confidence = output.get("confidence")
    if not isinstance(confidence, (int, float)):
        return False, f"confidence is not numeric: {confidence!r}"
    if confidence < 0 or confidence > 1:
        return False, f"confidence out of [0,1]: {confidence}"

    data = output.get("data")
    if not isinstance(data, dict):
        return False, "data is not a dict"

    # Required data fields
    for f in ("brief", "transcript_chars", "source"):
        if f not in data:
            return False, f"data missing required field: {f}"

    brief = data.get("brief", "")
    if not isinstance(brief, str) or len(brief) < 10:
        return False, f"brief too short: {len(brief)} chars"

    # Confidence threshold
    if confidence < 0.4:
        return False, f"confidence too low ({confidence} < 0.4) — likely ASR failure"

    return True, (
        f"Validated: confidence={confidence}, "
        f"transcript_chars={data.get('transcript_chars', '?')}, "
        f"theme={data.get('theme') or 'n/a'}, "
        f"keywords={len(data.get('keywords') or [])}"
    )


if __name__ == "__main__":
    import json
    import sys
    output = json.loads(sys.stdin.read())
    ok, msg = validate(output)
    print(json.dumps({"ok": ok, "message": msg}, ensure_ascii=False, indent=2))
    sys.exit(0 if ok else 1)
