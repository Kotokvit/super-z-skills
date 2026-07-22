"""Tests for the canonical POLER v3.0 API (PolerAnalyzer).

These tests exercise the real poler_enhanced.py module — the same module
that lives at skills/_shared/sandbox/poler_enhanced.py and that the rest of
the system (src/super_z/poler_edit.py, sandbox/__init__.py) imports.
"""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SHARED_ROOT = REPO_ROOT / "skills" / "_shared"
if str(SHARED_ROOT) not in sys.path:
    sys.path.insert(0, str(SHARED_ROOT))

from sandbox.poler_enhanced import PolerAnalyzer, run_poler_analyzer, build_veins


SAMPLE_TEXT = """
Quantum mechanics describes the behaviour of matter at very small scales.
The Hamiltonian operator governs the time evolution of a closed system.
In a stationary state, the wavefunction satisfies the Schrodinger equation.
Observers measure eigenvalues of the operator, not the operator itself.
The quantum harmonic oscillator is a canonical model in introductory courses.
""".strip()


@pytest.fixture
def analyzer() -> PolerAnalyzer:
    return PolerAnalyzer(window=500, top=5)


def test_poler_analyzer_constructs_with_defaults() -> None:
    a = PolerAnalyzer()
    assert a.window > 0
    assert a.top > 0
    assert 0.0 < a.phi <= 1.0


def test_build_veins_returns_expected_keys(analyzer: PolerAnalyzer) -> None:
    result = analyzer.build_veins(SAMPLE_TEXT, keywords=["quantum"])
    assert isinstance(result, dict)
    for key in ("veins", "stats", "source_file"):
        assert key in result, f"missing key: {key}"


def test_build_veins_returns_list_of_veins(analyzer: PolerAnalyzer) -> None:
    result = analyzer.build_veins(SAMPLE_TEXT, keywords=["quantum"])
    veins = result.get("veins", [])
    assert isinstance(veins, list)
    # Each vein should expose epsilon_peak and resonance_integral (v3.0 API)
    for vein in veins:
        assert "epsilon_peak" in vein or "epsilon" in vein
        assert "resonance_integral" in vein or "resonance" in vein


def test_build_veins_handles_empty_text(analyzer: PolerAnalyzer) -> None:
    result = analyzer.build_veins("", keywords=["anything"])
    assert isinstance(result, dict)
    assert result.get("veins") == [] or result.get("veins") is None


def test_build_veins_auto_extracts_keywords_when_none_given(analyzer: PolerAnalyzer) -> None:
    result = analyzer.build_veins(SAMPLE_TEXT)
    assert isinstance(result, dict)
    # auto-extraction should produce at least one vein or stats entry
    assert "veins" in result or "stats" in result


def test_run_poler_analyzer_returns_dict() -> None:
    # run_poler_analyzer(text, keyword, window, phi, kappa, top, source_file="")
    result = run_poler_analyzer(SAMPLE_TEXT, "quantum", 500, 0.85, 1.0, 5)
    assert isinstance(result, dict)
    assert "keyword" in result
    assert result["keyword"] == "quantum"


def test_module_level_build_veins_function_works() -> None:
    result = build_veins(SAMPLE_TEXT, ["quantum"], None, 500, 0.85, 1.0, 5, "")
    assert isinstance(result, dict)
