#!/usr/bin/env python3
"""validator.py — validates doc-triage output envelope."""
import json
import sys
from pathlib import Path

# Pattern 1 helper
_ORCH = Path(__file__).resolve().parents[2] / "_orchestrator" / "scripts"
if str(_ORCH) not in sys.path:
    sys.path.insert(0, str(_ORCH))
try:
    from patterns.source_grounded_brief import validate_brief
except Exception:
    validate_brief = None


def validate(envelope: dict) -> tuple:
    """Validate envelope. Returns (is_valid: bool, message: str) tuple.

    This matches the contract expected by _orchestrator/scripts/executor.py
    which does `ok, msg = module.validate(output)`.
    """
    errs = []
    if not isinstance(envelope, dict):
        return (False, "envelope is not a dict")
    for f in ("status", "confidence"):
        if f not in envelope:
            errs.append(f"missing required field: {f}")
    if envelope.get("status") not in ("success", "error", "skipped"):
        errs.append(f"invalid status: {envelope.get('status')}")
    conf = envelope.get("confidence", -1)
    if not (0.0 <= conf <= 1.0):
        errs.append(f"confidence out of [0,1]: {conf}")
    if envelope.get("status") == "success":
        data = envelope.get("data") or {}
        if not isinstance(data, dict):
            errs.append("data must be a dict on success")
        elif validate_brief:
            # Pattern 1 validation
            errs.extend(validate_brief(data))
    if errs:
        return (False, "; ".join(errs))
    return (True, f"Validated: status={envelope.get('status')}, "
                  f"confidence={envelope.get('confidence')}")


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="doc-triage envelope validator")
    ap.add_argument("input", help="JSON file path or - for stdin")
    args = ap.parse_args()
    if args.input == "-":
        env = json.loads(sys.stdin.read())
    else:
        env = json.loads(Path(args.input).read_text(encoding="utf-8"))
    ok, msg = validate(env)
    if not ok:
        print(f"✗ {msg}", file=sys.stderr)
        return 1
    print(f"✓ {msg}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
