#!/usr/bin/env python3
"""doctor.py — self-test for doc-triage skill."""
import shutil
import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
PDF_OCR = SKILL_DIR.parent / "pdf-ocr" / "scripts" / "ocr_pdf.py"
POLER_INGEST = SKILL_DIR.parent / "poler-toolkit" / "scripts" / "ingest.py"
_ORCH = SKILL_DIR.parent / "_orchestrator" / "scripts"


def check(label, ok, detail=""):
    icon = "✓" if ok else "✗"
    print(f"  {icon} {label}: {detail if not ok else 'OK'}")
    return ok


def main() -> int:
    print("doc-triage doctor")
    all_ok = True

    all_ok &= check("Python 3.8+", sys.version_info >= (3, 8), str(sys.version_info))

    pdftotext = shutil.which("pdftotext")
    all_ok &= check("pdftotext available", bool(pdftotext), "install poppler-utils")

    all_ok &= check("pdf-ocr script exists", PDF_OCR.exists(), str(PDF_OCR))

    all_ok &= check("poler-toolkit ingest.py exists", POLER_INGEST.exists(), str(POLER_INGEST))

    all_ok &= check("_orchestrator scripts dir exists", _ORCH.exists(), str(_ORCH))

    # python-docx optional
    try:
        import docx  # noqa
        check("python-docx available", True)
    except ImportError:
        check("python-docx available", False, "optional — .docx will use xml fallback")

    # Pattern 1 helper
    try:
        sys.path.insert(0, str(_ORCH))
        from patterns.source_grounded_brief import build_brief, Claim  # noqa
        check("Pattern 1 (source_grounded_brief) importable", True)
    except Exception as e:
        check("Pattern 1 (source_grounded_brief) importable", False, str(e))
        all_ok = False

    # Cache dir
    cache = Path("/tmp/doc_triage_cache")
    try:
        cache.mkdir(parents=True, exist_ok=True)
        check("cache dir writable", True)
    except Exception as e:
        check("cache dir writable", False, str(e))
        all_ok = False

    # Smoke test: create a small .txt and triage it
    print("\n  Smoke test (triage a small .txt)...")
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".txt", mode="w", delete=False, encoding="utf-8") as f:
        f.write("This is a test document. It discusses artificial intelligence and machine learning. "
                "The document covers topics such as neural networks, deep learning, and natural language processing. "
                "These technologies are transforming how we interact with computers and information.")
        smoke_path = f.name
    try:
        r = subprocess.run(
            [sys.executable, str(SKILL_DIR / "scripts" / "doc_triage.py"), smoke_path, "--json"],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode == 0:
            import json
            env = json.loads(r.stdout)
            status = env.get("status")
            conf = env.get("confidence", 0)
            has_claims = isinstance(env.get("data", {}).get("claims"), list) and len(env["data"]["claims"]) > 0
            check("smoke test status=success", status == "success", f"got {status}")
            check(f"smoke test confidence≥0.3", conf >= 0.3, f"got {conf}")
            check("smoke test has claims[]", has_claims, "Pattern 1 missing?")
            if status == "success" and conf >= 0.3 and has_claims:
                print("  ✓ smoke test PASS")
            else:
                print("  ✗ smoke test FAIL")
                all_ok = False
        else:
            print(f"  ✗ smoke test FAIL: rc={r.returncode}")
            print(f"    stderr: {r.stderr[:300]}")
            all_ok = False
    finally:
        Path(smoke_path).unlink(missing_ok=True)

    print(f"\n  Overall: {'PASS' if all_ok else 'FAIL'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
