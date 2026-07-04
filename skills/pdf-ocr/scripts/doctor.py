#!/usr/bin/env python3
"""
doctor.py — Self-diagnostic for pdf-ocr skill.

Checks that all binary dependencies and Python imports are functional.

Checks performed:
  1. Python version >= 3.8
  2. Binary dependencies: pdftotext, pdftoppm, tesseract
  3. Tesseract languages: rus + ukr (bundled in ../tessdata/)
  4. Bundled tessdata files exist
  5. ocr_pdf.py imports cleanly
  6. End-to-end: extract a tiny digital PDF (generated on-the-fly)
  7. manifest.json valid

Usage:
  python3 doctor.py            # human-readable
  python3 doctor.py --json     # machine-readable

Exit codes:
  0 — all critical checks passed
  1 — at least one critical check failed

Author: Task 9 (manifest-based architecture), 2026-07-03
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Tuple

SKILL_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SKILL_DIR / "scripts"
TESSDATA_DIR = SKILL_DIR / "tessdata"

REQUIRED_PYTHON = (3, 8)
REQUIRED_BINARIES = ["pdftotext", "pdftoppm", "tesseract"]
REQUIRED_TESSDATA = ["rus.traineddata", "ukr.traineddata"]


def check_python_version() -> Tuple[bool, str, str]:
    v = sys.version_info
    actual = f"{v.major}.{v.minor}.{v.micro}"
    if (v.major, v.minor) >= REQUIRED_PYTHON:
        return True, f"Python {actual}", ""
    return False, f"Python {actual} (too old)", f"Need >={REQUIRED_PYTHON[0]}.{REQUIRED_PYTHON[1]}"


def check_binaries() -> Tuple[bool, str, str]:
    """Check that pdftotext, pdftoppm, tesseract are installed."""
    missing = []
    for b in REQUIRED_BINARIES:
        if not shutil.which(b):
            missing.append(b)
    if missing:
        return False, f"Missing: {', '.join(missing)}", \
               "Install poppler-utils (pdftotext, pdftoppm) and tesseract-ocr"
    return True, f"All {len(REQUIRED_BINARIES)} binaries in PATH", ""


def check_tessdata() -> Tuple[bool, str, str]:
    """Check that bundled tessdata files exist."""
    if not TESSDATA_DIR.exists():
        return False, f"tessdata dir missing: {TESSDATA_DIR}", ""
    missing = []
    for td in REQUIRED_TESSDATA:
        if not (TESSDATA_DIR / td).exists():
            missing.append(td)
    if missing:
        return False, f"Missing tessdata: {', '.join(missing)}", \
               "Download rus.traineddata and ukr.traineddata from tesseract-ocr/tessdata"
    return True, f"All {len(REQUIRED_TESSDATA)} tessdata files present", ""


def check_tesseract_langs() -> Tuple[bool, str, str]:
    """Check that tesseract can find rus and ukr languages."""
    # Set TESSDATA_PREFIX to bundled dir
    env = os.environ.copy()
    env["TESSDATA_PREFIX"] = str(TESSDATA_DIR.parent)  # tessdata/ is the dir name
    try:
        result = subprocess.run(
            ["tesseract", "--list-langs"],
            capture_output=True, text=True, timeout=5, env=env
        )
        if result.returncode != 0:
            return False, f"tesseract --list-langs failed", result.stderr[:200]
        langs = result.stdout
        has_rus = "rus" in langs
        has_ukr = "ukr" in langs
        if has_rus and has_ukr:
            return True, "tesseract recognizes rus+ukr", ""
        missing = []
        if not has_rus: missing.append("rus")
        if not has_ukr: missing.append("ukr")
        return False, f"Missing langs: {missing}", "Check TESSDATA_PREFIX and traineddata files"
    except subprocess.TimeoutExpired:
        return False, "tesseract --list-langs timeout", ""
    except Exception as e:
        return False, f"tesseract check crashed: {e}", ""


def check_ocr_pdf_imports() -> Tuple[bool, str, str]:
    """Check that ocr_pdf.py imports cleanly."""
    try:
        sys.path.insert(0, str(SCRIPTS_DIR))
        # Use importlib to avoid reloading issues
        import importlib
        if "ocr_pdf" in sys.modules:
            del sys.modules["ocr_pdf"]
        import ocr_pdf  # noqa: F401
        assert hasattr(ocr_pdf, "extract_pdf"), "ocr_pdf.extract_pdf missing"
        assert hasattr(ocr_pdf, "calculate_confidence"), "calculate_confidence missing (Task 9)"
        return True, "ocr_pdf imports cleanly", ""
    except Exception as e:
        return False, f"Import failed: {e}", ""


def check_digital_pdf_smoke() -> Tuple[bool, str, str]:
    """Generate a tiny digital PDF and verify extraction works."""
    try:
        # Use reportlab if available, else skip with warning
        try:
            from reportlab.pdfgen import canvas  # type: ignore
        except ImportError:
            return True, "reportlab not installed (smoke test skipped)", \
                   "Optional — install with: pip install reportlab"

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            c = canvas.Canvas(tmp_path)
            # Generate enough text per page to pass looks_like_scanned()
            # (needs >100 chars/page to avoid OCR fallback false-positive)
            text_lines = [
                "Hello pdf-ocr doctor smoke test for digital PDF extraction.",
                "Astronomy stars planets telescope orbit galaxy nebula cosmos.",
                "This document contains enough text to be classified as digital.",
                "The quick brown fox jumps over the lazy dog near the river bank.",
                "Biology cells organisms DNA proteins evolution species genetics.",
                "Navigation compass latitude longitude meridian equator polaris.",
                "Cultivation plants agriculture soil harvest irrigation fertilizer.",
                "Geography continents mountains rivers oceans climate population.",
            ]
            y = 750
            for line in text_lines:
                c.drawString(80, y, line)
                y -= 18
            c.save()

            # Import and call extract_pdf directly
            sys.path.insert(0, str(SCRIPTS_DIR))
            if "ocr_pdf" in sys.modules:
                del sys.modules["ocr_pdf"]
            import ocr_pdf
            text, meta = ocr_pdf.extract_pdf(tmp_path, max_pages=1)

            if "doctor smoke" not in text:
                return False, f"text mismatch: {text[:80]!r}", "Expected 'doctor smoke' in extracted text"
            if meta["ocr_used"]:
                return False, "OCR used on digital PDF (false positive)", \
                       "looks_like_scanned() may be too aggressive"

            conf = ocr_pdf.calculate_confidence(text, meta)
            return True, f"Extracted {len(text)} chars, confidence={conf}", ""
        finally:
            os.unlink(tmp_path)
    except Exception as e:
        return False, f"smoke test crashed: {e}", ""


def check_manifest_valid() -> Tuple[bool, str, str]:
    """Check that manifest.json is valid."""
    manifest_path = SKILL_DIR / "manifest.json"
    if not manifest_path.exists():
        return False, "manifest.json missing", ""
    try:
        m = json.loads(manifest_path.read_text(encoding="utf-8"))
        required = ["name", "version", "category", "cost", "triggers",
                    "inputs", "outputs", "requires", "dependencies"]
        missing = [f for f in required if f not in m]
        if missing:
            return False, f"manifest missing: {missing}", ""
        if m["name"] != "pdf-ocr":
            return False, f"manifest name={m['name']!r}", "Expected 'pdf-ocr'"
        return True, f"manifest OK (v{m['version']})", ""
    except json.JSONDecodeError as e:
        return False, f"invalid JSON: {e}", ""


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

CHECKS = [
    ("python_version", "Python version", check_python_version, True),
    ("binaries", "Binary deps (pdftotext/pdftoppm/tesseract)", check_binaries, True),
    ("tessdata", "Bundled tessdata files", check_tessdata, True),
    ("tesseract_langs", "Tesseract recognizes rus+ukr", check_tesseract_langs, True),
    ("ocr_pdf_imports", "ocr_pdf.py imports cleanly", check_ocr_pdf_imports, True),
    ("digital_pdf_smoke", "Digital PDF extraction smoke", check_digital_pdf_smoke, True),
    ("manifest_valid", "manifest.json valid", check_manifest_valid, True),
]


def run_all() -> Tuple[List[dict], bool]:
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
    ap = argparse.ArgumentParser(description="pdf-ocr self-diagnostic")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    results, all_ok = run_all()

    if args.json:
        print(json.dumps({
            "skill": "pdf-ocr",
            "all_critical_passed": all_ok,
            "checks": results,
        }, ensure_ascii=False, indent=2))
    else:
        print(f"\n  ═══ pdf-ocr doctor ═══\n")
        for r in results:
            icon = {"PASS": "✓", "FAIL": "✗", "WARN": "⚠"}[r["status"]]
            print(f"  {icon} [{r['status']}] {r['name']}: {r['summary']}")
        print(f"\n  {'✓ All critical checks passed' if all_ok else '✗ Some critical checks failed'}\n")

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
