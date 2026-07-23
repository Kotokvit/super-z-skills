"""
smoke_test_stage5.py — End-to-end test for Stage 5 UI (Stage 4-based).

Verifies:
  1. SmartInterpreter with Stage 4 transforms finds violations
  2. apply_fix(violation) returns source_before / source_after
  3. apply_all_fixes(accepted_violations) produces patched source
  4. diff_engine.violation_diff produces segments for UI
  5. bad_python sample: eval→ast.literal_eval, except→except Exception
"""
from __future__ import annotations

import sys
from pathlib import Path

# Add src to path
HERE = Path(__file__).parent
sys.path.insert(0, str(HERE / "src"))

# Stub poler_edit — not needed for Stage 5 testing
import types
class _StubPolerEdit:
    def __init__(self, **kw): pass
    def analyze(self): return {"poler_v3": {"veins": [], "navigation_map": {}}}

pe_mod = types.ModuleType("super_z.poler_edit")
pe_mod.PolerEdit = _StubPolerEdit
sys.modules["super_z.poler_edit"] = pe_mod

from super_z.poler_smart_interpreter import SmartInterpreter, analyze_code  # noqa: E402
from super_z.smart_rules.diff_engine import unified_diff, violation_diff  # noqa: E402


# =============================================================================
# Sample source — Stage 4 has transforms for R001 (eval) and R006 (bare except)
# =============================================================================

SAMPLE_PY = '''"""Sample for Stage 5 smoke test (Stage 4 architecture)."""
def danger(user_input):
    result = eval(user_input)
    return result


def swallow_error():
    try:
        do_something()
    except:
        pass
'''


# =============================================================================
# Tests
# =============================================================================

def test_analyze():
    print("\n[1] Analyzing sample...")
    report = analyze_code(SAMPLE_PY, filename="sample.py", language="python")
    assert report["syntax_ok"], "Syntax should be OK"
    assert report["summary"]["total"] > 0, "Should find violations"

    # Check that some violations have transformations
    has_transform_count = 0
    for v in report["violations"]:
        assert "transformation" in v, f"Missing transformation field on {v['rule_id']}"
        assert "_node" in v, f"Missing _node field on {v['rule_id']}"
        if v["transformation"] is not None:
            has_transform_count += 1

    assert has_transform_count > 0, "Expected at least one violation with a transformation"
    print(f"    OK — found {report['summary']['total']} violations")
    print(f"    By rule: {report['summary']['by_rule']}")
    print(f"    Violations with transforms: {has_transform_count}/{report['summary']['total']}")
    return report


def test_apply_fix_single(report):
    print("\n[2] Testing apply_fix on single violation...")
    # Create a fresh SmartInterpreter (apply_fix mutates state)
    interp = SmartInterpreter(SAMPLE_PY, filename="sample.py", language="python")
    fresh_violations = interp.find_violations()

    # Find the eval violation
    eval_v = next((v for v in fresh_violations if v["rule_id"] == "R001"), None)
    assert eval_v is not None, "Need an R001 (eval) violation"
    print(f"    Before: L{eval_v['line']} {eval_v['source_snippet']!r}")

    result = interp.apply_fix(eval_v)
    print(f"    success: {result['success']}")
    print(f"    message: {result['message']}")
    print(f"    action:  {result['action']}")
    print(f"    source_after line: {result['source_after'].split(chr(10))[eval_v['line']-1]!r}")
    assert result["success"], f"apply_fix should succeed: {result['message']}"
    assert "ast.literal_eval" in result["source_after"], "Fix should introduce ast.literal_eval"
    assert "eval(" not in result["source_after"].replace("literal_eval", ""), "Original eval should be gone"
    print("    OK — eval → ast.literal_eval applied")

    # Test bare except
    interp2 = SmartInterpreter(SAMPLE_PY, filename="sample.py", language="python")
    fresh2 = interp2.find_violations()
    except_v = next((v for v in fresh2 if v["rule_id"] == "R006"), None)
    assert except_v is not None, "Need an R006 (bare except) violation"
    result2 = interp2.apply_fix(except_v)
    print(f"    bare except success: {result2['success']}")
    assert result2["success"], f"R006 apply_fix should succeed: {result2['message']}"
    assert "except Exception" in result2["source_after"], "Should introduce except Exception"
    print(f"    After: ...{result2['source_after'].split(chr(10))[except_v['line']-1]!r}")
    print("    OK — bare except → except Exception applied")


def test_apply_all_fixes(report):
    print("\n[3] Testing apply_all_fixes (batch)...")
    interp = SmartInterpreter(SAMPLE_PY, filename="sample.py", language="python")
    fresh_violations = interp.find_violations()

    # Accept all violations that have transforms
    accepted = [v for v in fresh_violations if v.get("transformation") is not None]
    print(f"    Accepted: {len(accepted)} violations with transforms")

    result = interp.apply_all_fixes(accepted)
    print(f"    total:   {result['total']}")
    print(f"    applied: {result['applied']}")
    print(f"    failed:  {result['failed']}")
    assert result["applied"] > 0, "Should have applied at least one fix"

    print()
    print("    Patched source:")
    for line in result["final_source"].split("\n"):
        print(f"      | {line}")
    print()
    return result


def test_diff_engine(apply_result):
    print("\n[4] Testing diff_engine...")
    original = SAMPLE_PY
    patched = apply_result["final_source"]
    diff = unified_diff(original, patched, "sample.py")
    print(f"    Diff length: {len(diff)} chars, {len(diff.splitlines())} lines")
    for line in diff.splitlines()[:15]:
        print(f"      {line}")
    if len(diff.splitlines()) > 15:
        print(f"      ... ({len(diff.splitlines()) - 15} more lines)")

    # Test violation_diff
    vd = violation_diff(original, patched, has_transform=True)
    assert "before" in vd and "after" in vd and "segments" in vd
    print(f"    violation_diff: {len(vd['segments'])} segments")
    print("    OK — diff engine works")


def main():
    print("=" * 70)
    print("Stage 5 Smoke Test (Stage 4 architecture)")
    print("=" * 70)

    report = test_analyze()
    test_apply_fix_single(report)
    apply_result = test_apply_all_fixes(report)
    test_diff_engine(apply_result)

    print("\n" + "=" * 70)
    print("All Stage 5 smoke tests passed.")
    print("=" * 70)
    print("\nTo launch the UI:")
    print("  cd /home/z/my-project/super-z-skills")
    print("  python -m super_z.web_ui")
    print("  then open http://localhost:5000")


if __name__ == "__main__":
    main()
