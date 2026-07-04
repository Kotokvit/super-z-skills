#!/usr/bin/env python3
"""doctor.py — self-test for gap-detector skill."""
import shutil
import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
_ORCH = SKILL_DIR.parent / "_orchestrator" / "scripts"
DEFAULT_BRIEF = Path("/home/z/my-project/.context/context_brief.json")


def check(label, ok, detail=""):
    icon = "✓" if ok else "✗"
    print(f"  {icon} {label}: {detail if not ok else 'OK'}")
    return ok


def main() -> int:
    print("gap-detector doctor")
    all_ok = True

    all_ok &= check("Python 3.8+", sys.version_info >= (3, 8), str(sys.version_info))
    all_ok &= check("_orchestrator scripts dir exists", _ORCH.exists(), str(_ORCH))

    z_ai = shutil.which("z-ai")
    all_ok &= check("z-ai CLI available", bool(z_ai), "install z-ai-web-dev-sdk")

    # Pattern 1
    try:
        sys.path.insert(0, str(_ORCH))
        from patterns.source_grounded_brief import build_brief, Claim  # noqa
        check("Pattern 1 importable", True)
    except Exception as e:
        check("Pattern 1 importable", False, str(e))
        all_ok = False

    # context_brief.json exists?
    check("context_brief.json exists", DEFAULT_BRIEF.exists(), str(DEFAULT_BRIEF))

    # Smoke test: detect gaps for a question that doesn't match the existing brief
    print("\n  Smoke test (detect gaps for an unrelated question)...")
    r = subprocess.run(
        [sys.executable, str(SKILL_DIR / "scripts" / "gap_detector.py"),
         "what's the weather on Mars right now?", "--json"],
        capture_output=True, text=True, timeout=90,
    )
    if r.returncode == 0:
        import json
        env = json.loads(r.stdout)
        status = env.get("status")
        has_claims = isinstance(env.get("data", {}).get("claims"), list) and len(env["data"]["claims"]) > 0
        verdict = (env.get("data") or {}).get("verdict", "?")
        check(f"smoke test status=success", status == "success", f"got {status}")
        check("smoke test has claims[]", has_claims)
        check(f"smoke test verdict is ask_user_first or answer_with_caveat or decline",
              verdict in ("ask_user_first", "answer_with_caveat", "decline"),
              f"got {verdict}")
        if status == "success" and has_claims:
            print("  ✓ smoke test PASS")
        else:
            print("  ✗ smoke test FAIL")
            all_ok = False
    else:
        print(f"  ✗ smoke test FAIL: rc={r.returncode}")
        print(f"    stderr: {r.stderr[:300]}")
        all_ok = False

    print(f"\n  Overall: {'PASS' if all_ok else 'FAIL'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
