#!/usr/bin/env python3
"""doctor.py — self-test for site-context-loader skill."""
import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
_ORCH = SKILL_DIR.parent / "_orchestrator" / "scripts"


def check(label, ok, detail=""):
    icon = "✓" if ok else "✗"
    print(f"  {icon} {label}: {detail if not ok else 'OK'}")
    return ok


def main() -> int:
    print("site-context-loader doctor")
    all_ok = True

    all_ok &= check("Python 3.8+", sys.version_info >= (3, 8), str(sys.version_info))
    all_ok &= check("_orchestrator scripts dir exists", _ORCH.exists(), str(_ORCH))

    # Pattern 1
    try:
        sys.path.insert(0, str(_ORCH))
        from patterns.source_grounded_brief import build_brief, Claim  # noqa
        check("Pattern 1 importable", True)
    except Exception as e:
        check("Pattern 1 importable", False, str(e))
        all_ok = False

    # Network — try Nominatim (might be down or rate-limited)
    print("\n  Network test (Nominatim search for 'Paris')...")
    try:
        r = subprocess.run(
            [sys.executable, str(SKILL_DIR / "scripts" / "site_context_loader.py"),
             "Paris", "--json"],
            capture_output=True, text=True, timeout=20,
        )
        if r.returncode == 0:
            import json
            env = json.loads(r.stdout)
            status = env.get("status")
            has_claims = isinstance(env.get("data", {}).get("claims"), list) and len(env["data"]["claims"]) > 0
            check("network test status=success", status == "success", f"got {status}")
            check("network test has claims[]", has_claims)
            if status == "success" and has_claims:
                print("  ✓ network test PASS")
            else:
                print("  ✗ network test FAIL")
                all_ok = False
        else:
            print(f"  ✗ network test FAIL: rc={r.returncode}")
            print(f"    stderr: {r.stderr[:300]}")
            all_ok = False
    except subprocess.TimeoutExpired:
        print("  ⚠ network test TIMEOUT (Nominatim might be rate-limited) — non-fatal")

    print(f"\n  Overall: {'PASS' if all_ok else 'FAIL'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
