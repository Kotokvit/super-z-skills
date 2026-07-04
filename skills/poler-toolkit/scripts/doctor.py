#!/usr/bin/env python3
"""
doctor.py — Self-diagnostic for poler-toolkit skill.

Checks that all dependencies and entry points are functional.
Run via `skill doctor poler-toolkit` or directly.

Checks performed:
  1. Python version >= 3.8
  2. Core scripts exist (poler_v6.py, ingest.py, topic_common.py)
  3. poler_v6 imports cleanly (zero-deps promise)
  4. ingest.py runs end-to-end on a tiny sample
  5. pdf-ocr dependency available (sibling skill)
  6. (Optional) z-ai chat CLI for --llm mode
  7. Manifest.json is valid JSON and has required fields

Usage:
  python3 doctor.py            # human-readable
  python3 doctor.py --json     # machine-readable

Exit codes:
  0 — all critical checks passed (warnings allowed)
  1 — at least one critical check failed
  2 — doctor itself crashed

Author: Task 9 (manifest-based architecture), 2026-07-03
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Tuple

SKILL_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SKILL_DIR / "scripts"

REQUIRED_PYTHON = (3, 8)
REQUIRED_SCRIPTS = ["poler_v6.py", "ingest.py", "topic_common.py",
                    "validator.py", "topic_local.py", "topic_llm.py",
                    "lens_query.py", "z_ai_api.py"]
MANIFEST_PATH = SKILL_DIR / "manifest.json"


def check_python_version() -> Tuple[bool, str, str]:
    """Check Python version >= 3.8."""
    v = sys.version_info
    actual = f"{v.major}.{v.minor}.{v.micro}"
    if (v.major, v.minor) >= REQUIRED_PYTHON:
        return True, f"Python {actual}", f"Need >={REQUIRED_PYTHON[0]}.{REQUIRED_PYTHON[1]}, have {actual}"
    return False, f"Python {actual} (too old)", f"Need >={REQUIRED_PYTHON[0]}.{REQUIRED_PYTHON[1]}, have {actual}"


def check_scripts_exist() -> Tuple[bool, str, str]:
    """Check that all required scripts are present."""
    missing = []
    for s in REQUIRED_SCRIPTS:
        if not (SCRIPTS_DIR / s).exists():
            missing.append(s)
    if missing:
        return False, f"Missing: {', '.join(missing)}", f"All {len(REQUIRED_SCRIPTS)} scripts must exist"
    return True, f"All {len(REQUIRED_SCRIPTS)} scripts present", ""


def check_poler_v6_imports() -> Tuple[bool, str, str]:
    """Check that poler_v6.py imports cleanly (zero-deps promise)."""
    try:
        sys.path.insert(0, str(SCRIPTS_DIR))
        import poler_v6  # noqa: F401
        # Spot-check a key function exists
        assert hasattr(poler_v6, "read_file"), "poler_v6.read_file missing"
        assert hasattr(poler_v6, "analyze_text"), "poler_v6.analyze_text missing"
        return True, "poler_v6 imports cleanly (zero-deps OK)", ""
    except Exception as e:
        return False, f"Import failed: {e}", "poler_v6 must import with stdlib only"


def check_ingest_smoke() -> Tuple[bool, str, str]:
    """Run ingest.py on a tiny sample and verify it returns the standard envelope."""
    try:
        sample = "Test text for astronomy: звёзды, планеты, галактика, телескоп, космос, астрономия, марс, юпитер, сатурн."
        result = subprocess.run(
            ["python3", str(SCRIPTS_DIR / "ingest.py"), "-", "--no-clusters", "--json"],
            input=sample, capture_output=True, text=True, timeout=15
        )
        if result.returncode != 0:
            return False, f"ingest.py exit {result.returncode}", result.stderr[:200]
        out = json.loads(result.stdout)
        if out.get("status") != "success":
            return False, f"status={out.get('status')}", "Expected status=success"
        if not isinstance(out.get("confidence"), (int, float)):
            return False, "confidence missing", "Envelope must have confidence"
        if not isinstance(out.get("data"), dict):
            return False, "data missing", "Envelope must have data"
        return True, f"ingest OK (confidence={out['confidence']})", ""
    except subprocess.TimeoutExpired:
        return False, "ingest.py timeout (15s)", "Should run in <1s"
    except Exception as e:
        return False, f"ingest crashed: {e}", ""


def check_pdf_ocr_dependency() -> Tuple[bool, str, str]:
    """Check that sibling pdf-ocr skill is available."""
    pdf_ocr_script = SKILL_DIR.parent / "pdf-ocr" / "scripts" / "ocr_pdf.py"
    if pdf_ocr_script.exists():
        return True, f"pdf-ocr found at {pdf_ocr_script.relative_to(SKILL_DIR.parent)}", ""
    return False, "pdf-ocr skill not found", "poler-toolkit depends on pdf-ocr (see manifest.json)"


def check_z_ai_cli() -> Tuple[bool, str, str]:
    """Check z-ai CLI availability (optional, only needed for --llm mode)."""
    try:
        result = subprocess.run(["which", "z-ai"], capture_output=True, text=True, timeout=3)
        if result.returncode == 0:
            return True, f"z-ai CLI at {result.stdout.strip()}", "Available for --llm mode"
        return True, "z-ai CLI not in PATH", "Optional — only needed for --llm semantic mode"
    except Exception:
        return True, "z-ai check skipped", "Optional"


def check_manifest_valid() -> Tuple[bool, str, str]:
    """Check that manifest.json is valid and has required top-level fields."""
    if not MANIFEST_PATH.exists():
        return False, "manifest.json missing", "Required for orchestrator routing"
    try:
        m = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        required = ["name", "version", "category", "priority", "cost",
                    "triggers", "inputs", "outputs", "requires"]
        missing = [f for f in required if f not in m]
        if missing:
            return False, f"manifest missing fields: {missing}", ""
        if m["name"] != "poler-toolkit":
            return False, f"manifest name={m['name']!r}", "Expected 'poler-toolkit'"
        return True, f"manifest OK (v{m['version']}, pri={m['priority']})", ""
    except json.JSONDecodeError as e:
        return False, f"manifest.json invalid JSON: {e}", ""


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

CHECKS = [
    ("python_version", "Python version", check_python_version, True),
    ("scripts_exist", "Required scripts present", check_scripts_exist, True),
    ("poler_v6_imports", "poler_v6 imports cleanly", check_poler_v6_imports, True),
    ("ingest_smoke", "ingest.py smoke test", check_ingest_smoke, True),
    ("pdf_ocr_dep", "pdf-ocr dependency", check_pdf_ocr_dependency, True),
    ("manifest_valid", "manifest.json valid", check_manifest_valid, True),
    ("z_ai_cli", "z-ai CLI (optional)", check_z_ai_cli, False),
]


def run_all() -> Tuple[List[dict], bool]:
    """Run all checks. Returns (results, all_critical_passed)."""
    results = []
    all_ok = True
    for cid, name, fn, critical in CHECKS:
        try:
            ok, summary, detail = fn()
        except Exception as e:
            ok, summary, detail = False, f"doctor check crashed: {e}", ""
        status = "PASS" if ok else ("FAIL" if critical else "WARN")
        if not ok and critical:
            all_ok = False
        results.append({
            "id": cid,
            "name": name,
            "status": status,
            "summary": summary,
            "detail": detail,
            "critical": critical,
        })
    return results, all_ok


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="poler-toolkit self-diagnostic")
    ap.add_argument("--json", action="store_true", help="Output JSON instead of human-readable")
    args = ap.parse_args()

    results, all_ok = run_all()

    if args.json:
        print(json.dumps({
            "skill": "poler-toolkit",
            "all_critical_passed": all_ok,
            "checks": results,
        }, ensure_ascii=False, indent=2))
    else:
        print(f"\n  ═══ poler-toolkit doctor ═══\n")
        for r in results:
            icon = {"PASS": "✓", "FAIL": "✗", "WARN": "⚠"}[r["status"]]
            print(f"  {icon} [{r['status']}] {r['name']}: {r['summary']}")
        print(f"\n  {'✓ All critical checks passed' if all_ok else '✗ Some critical checks failed'}\n")

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
