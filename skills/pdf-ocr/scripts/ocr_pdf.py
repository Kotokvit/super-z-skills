#!/usr/bin/env python3
"""
ocr_pdf.py — PDF OCR skill for poler-toolkit / doc-ingest pipeline.

Strategy (auto-detect):
  1. pdfinfo → check page count
  2. pdftotext (fast, no OCR) → measure text density per page
  3. If density >= MIN_CHARS_PER_PAGE → return text (PDF was born-digital)
  4. Else → OCR pipeline:
     - pdftoppm -r 300 -png (render each page to image)
     - tesseract per image with rus+ukr+eng (config in tessdata/)
     - Concatenate text from all pages
  5. Return text + metadata {ocr_used, pages, chars_per_page_avg, format}

Usage:
  python3 ocr_pdf.py INPUT.pdf [-o OUT.txt] [--json] [--force-ocr] [--lang rus+ukr+eng]
                                [--max-pages N] [--dpi 300] [--verbose]

Inputs:
  INPUT.pdf           local PDF file path
  (or stdin stream if INPUT = -)

Outputs:
  - default: writes extracted text to stdout (or -o file)
  - --json: prints JSON {text, meta:{ocr_used, pages, chars_per_page_avg, format, langs, elapsed_sec}}

Dependencies (all pre-installed on this system):
  - pdftotext (poppler-utils)  — text extraction
  - pdftoppm  (poppler-utils)  — page → PNG renderer
  - pdfinfo   (poppler-utils)  — page count
  - tesseract 5.5.0            — OCR engine
  - tessdata/ next to script   — rus + ukr + eng traineddata (no root needed)

No Python packages required beyond stdlib.

Author: poler-toolkit skill (Task 7, 2026-07-03)
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
TESSDATA_DIR = SCRIPT_DIR.parent / "tessdata"  # ../tessdata/

# Below this many chars/page on average, PDF is likely scanned images → OCR.
MIN_CHARS_PER_PAGE = 100

# Default OCR settings.
DEFAULT_LANGS = "rus+ukr+eng"
DEFAULT_DPI = 300
DEFAULT_MAX_PAGES = 50  # safety cap; override with --max-pages

# Tesseract config flags:
#   -c tessedit_char_whitelist=... — would harm cyrillic, so we don't set it
#   --psm 6 — assume a single uniform block of text (good default for documents)
#   --psm 3 — fully automatic page segmentation (default, more flexible)
DEFAULT_PSM = 3


# ---------------------------------------------------------------------------
# Helpers — tool discovery
# ---------------------------------------------------------------------------

def find_tool(name):
    p = shutil.which(name)
    if not p:
        sys.stderr.write(f"[ocr_pdf] ERROR: required tool '{name}' not found in PATH\n")
        sys.exit(2)
    return p


PDFTOTEXT = find_tool("pdftotext")
PDFTOPPM  = find_tool("pdftoppm")
PDFINFO   = find_tool("pdfinfo")
TESSERACT = find_tool("tesseract")


# ---------------------------------------------------------------------------
# Step 1 — probe PDF metadata
# ---------------------------------------------------------------------------

def pdf_page_count(pdf_path):
    """Return number of pages, or 0 on error."""
    try:
        out = subprocess.run(
            [PDFINFO, str(pdf_path)],
            capture_output=True, text=True, timeout=20,
        )
        if out.returncode != 0:
            return 0
        for line in out.stdout.splitlines():
            m = re.match(r"Pages:\s+(\d+)", line)
            if m:
                return int(m.group(1))
    except Exception:
        pass
    return 0


# ---------------------------------------------------------------------------
# Step 2 — fast text extraction (no OCR)
# ---------------------------------------------------------------------------

def extract_text_digital(pdf_path, max_pages=None):
    """Run pdftotext on the PDF. Returns (text, n_pages_actually_extracted)."""
    cmd = [PDFTOTEXT]
    if max_pages:
        cmd += ["-l", str(max_pages)]  # last page to convert
    cmd += ["-layout", str(pdf_path), "-"]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=120)
        if out.returncode != 0:
            return "", 0
        # pdftotext outputs in encoding declared in PDF; poppler defaults to UTF-8
        text = out.stdout.decode("utf-8", errors="replace")
        # Count form feeds (\f) — pdftotext separates pages with them.
        n_extracted = text.count("\f") or max_pages or 1
        return text, n_extracted
    except subprocess.TimeoutExpired:
        return "", 0
    except Exception as e:
        sys.stderr.write(f"[ocr_pdf] pdftotext failed: {e}\n")
        return "", 0


def looks_like_scanned(text, n_pages_extracted):
    """Return True if extracted text is too sparse to be born-digital.

    NOTE: n_pages_extracted is the number of pages pdftotext actually processed
    (capped by --max-pages), NOT the total page count of the PDF.
    """
    if n_pages_extracted == 0:
        n_pages_extracted = max(1, len(text) // 500)
    chars_per_page = len(text) / max(1, n_pages_extracted)
    return chars_per_page < MIN_CHARS_PER_PAGE


# ---------------------------------------------------------------------------
# Step 3 — OCR pipeline
# ---------------------------------------------------------------------------

def render_pages_to_images(pdf_path, out_dir, dpi=DEFAULT_DPI, max_pages=None, first_page=1):
    """Render PDF pages to PNGs via pdftoppm. Returns list of PNG paths in order."""
    prefix = out_dir / "page"
    cmd = [PDFTOPPM, "-r", str(dpi), "-png"]
    if max_pages:
        cmd += ["-l", str(first_page + max_pages - 1)]
    cmd += ["-f", str(first_page), str(pdf_path), str(prefix)]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=300)
        if out.returncode != 0:
            sys.stderr.write(
                f"[ocr_pdf] pdftoppm failed: {out.stderr.decode('utf-8', errors='replace')}\n"
            )
            return []
    except subprocess.TimeoutExpired:
        sys.stderr.write("[ocr_pdf] pdftoppm timed out\n")
        return []
    # pdftoppm names files like prefix-1.png, prefix-2.png, ... or prefix-01.png if 10+ pages
    pngs = sorted(out_dir.glob(f"{prefix.name}-*.png"))
    return pngs


def ocr_image(img_path, langs=DEFAULT_LANGS, psm=DEFAULT_PSM):
    """Run tesseract on a single image. Returns extracted text."""
    if not TESSDATA_DIR.exists():
        sys.stderr.write(f"[ocr_pdf] WARNING: tessdata dir not found at {TESSDATA_DIR}\n")
        tessdata_arg = []
    else:
        tessdata_arg = ["--tessdata-dir", str(TESSDATA_DIR)]
    cmd = [
        TESSERACT, str(img_path), "-",  # output to stdout
        "-l", langs,
        "--psm", str(psm),
    ] + tessdata_arg
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=120)
        if out.returncode != 0:
            sys.stderr.write(
                f"[ocr_pdf] tesseract failed on {img_path.name}: "
                f"{out.stderr.decode('utf-8', errors='replace')[:200]}\n"
            )
            return ""
        return out.stdout.decode("utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        sys.stderr.write(f"[ocr_pdf] tesseract timed out on {img_path.name}\n")
        return ""


def ocr_pdf(pdf_path, langs=DEFAULT_LANGS, dpi=DEFAULT_DPI,
            max_pages=DEFAULT_MAX_PAGES, verbose=False):
    """Full OCR pipeline on a PDF. Returns concatenated text from all pages."""
    n_pages = pdf_page_count(pdf_path)
    if n_pages == 0:
        n_pages = max_pages  # assume cap if probe failed

    pages_to_ocr = min(n_pages, max_pages)
    if verbose:
        sys.stderr.write(
            f"[ocr_pdf] OCR: {pages_to_ocr} pages, dpi={dpi}, langs={langs}\n"
        )

    with tempfile.TemporaryDirectory(prefix="ocr_pdf_") as tmp:
        tmp_dir = Path(tmp)
        pngs = render_pages_to_images(pdf_path, tmp_dir, dpi=dpi, max_pages=pages_to_ocr)
        if not pngs:
            return ""

        if verbose:
            sys.stderr.write(f"[ocr_pdf] rendered {len(pngs)} page images\n")

        chunks = []
        for i, png in enumerate(pngs, 1):
            if verbose:
                sys.stderr.write(f"[ocr_pdf] OCR page {i}/{len(pngs)} ...\n")
            txt = ocr_image(png, langs=langs)
            chunks.append(txt)

    return "\n\n".join(chunks)


# ---------------------------------------------------------------------------
# Confidence calculation (Task 9 — manifest-based architecture)
# ---------------------------------------------------------------------------

def calculate_confidence(text: str, meta: dict) -> float:
    """Compute 0..1 confidence for OCR/extraction result.

    Heuristic:
      - Digital text (pdftotext): base 0.95
      - OCR (tesseract): base 0.75
      - chars_per_page > 1000: +0.05 (dense text, likely clean)
      - chars_per_page < 100: -0.20 (suspiciously sparse)
      - chars_per_page < 30: -0.30 more (likely image-only PDF with bad OCR)
    Clamped to [0.0, 1.0].
    """
    if meta.get("ocr_used"):
        base = 0.75
    else:
        base = 0.95

    cpp = meta.get("chars_per_page_avg", 0)
    if cpp > 1000:
        base += 0.05
    if cpp < 100:
        base -= 0.20
    if cpp < 30:
        base -= 0.30
    if not text.strip():
        return 0.0

    return max(0.0, min(1.0, round(base, 3)))


# ---------------------------------------------------------------------------
# Top-level driver — auto-detect strategy
# ---------------------------------------------------------------------------

def extract_pdf(pdf_path, force_ocr=False, langs=DEFAULT_LANGS,
                dpi=DEFAULT_DPI, max_pages=DEFAULT_MAX_PAGES, verbose=False):
    """
    Auto-detect: try pdftotext first; if too sparse, fall back to OCR.
    Returns (text, meta_dict).
    """
    t0 = time.time()
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(pdf_path)

    n_pages = pdf_page_count(pdf_path)
    pages_capped = min(n_pages, max_pages) if n_pages else max_pages

    # --- Path A: digital text ---
    if not force_ocr:
        text, n_extracted = extract_text_digital(pdf_path, max_pages=pages_capped)
        if not looks_like_scanned(text, n_extracted):
            elapsed = time.time() - t0
            return text, {
                "ocr_used": False,
                "pages_total": n_pages,
                "pages_processed": n_extracted,
                "chars_per_page_avg": round(len(text) / max(1, n_extracted), 1),
                "format": "pdf",
                "extraction_method": "pdftotext",
                "langs": None,
                "elapsed_sec": round(elapsed, 2),
            }
        if verbose:
            sys.stderr.write(
                f"[ocr_pdf] pdftotext got only {len(text)} chars on "
                f"{n_extracted} extracted pages "
                f"({len(text)/max(1,n_extracted):.0f}/page) — falling back to OCR\n"
            )

    # --- Path B: OCR ---
    text = ocr_pdf(pdf_path, langs=langs, dpi=dpi, max_pages=max_pages, verbose=verbose)
    elapsed = time.time() - t0
    return text, {
        "ocr_used": True,
        "pages_total": n_pages,
        "pages_processed": pages_capped,
        "chars_per_page_avg": round(len(text) / max(1, pages_capped), 1),
        "format": "pdf",
        "extraction_method": f"tesseract({langs}, dpi={dpi})",
        "langs": langs,
        "elapsed_sec": round(elapsed, 2),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="PDF OCR skill — auto-detect digital vs scanned, extract text.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("input", help="PDF file path (or '-' for stdin)")
    ap.add_argument("-o", "--output", help="Write text to file (default: stdout)")
    ap.add_argument("--json", action="store_true",
                    help="Emit JSON {text, meta} instead of plain text")
    ap.add_argument("--force-ocr", action="store_true",
                    help="Skip pdftotext; always OCR (slower, useful for tests)")
    ap.add_argument("--lang", default=DEFAULT_LANGS,
                    help=f"Tesseract langs, '+'-joined (default: {DEFAULT_LANGS})")
    ap.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES,
                    help=f"Cap number of pages (default: {DEFAULT_MAX_PAGES})")
    ap.add_argument("--dpi", type=int, default=DEFAULT_DPI,
                    help=f"Render DPI for OCR (default: {DEFAULT_DPI})")
    ap.add_argument("--psm", type=int, default=DEFAULT_PSM,
                    help=f"Tesseract page-segmentation mode (default: {DEFAULT_PSM})")
    ap.add_argument("--verbose", "-v", action="store_true",
                    help="Progress to stderr")
    args = ap.parse_args()

    if args.input == "-":
        # Read PDF bytes from stdin → write to temp file
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(sys.stdin.buffer.read())
            tmp_path = tmp.name
        try:
            text, meta = extract_pdf(
                tmp_path, force_ocr=args.force_ocr, langs=args.lang,
                dpi=args.dpi, max_pages=args.max_pages, verbose=args.verbose,
            )
        finally:
            os.unlink(tmp_path)
    else:
        try:
            text, meta = extract_pdf(
                args.input, force_ocr=args.force_ocr, langs=args.lang,
                dpi=args.dpi, max_pages=args.max_pages, verbose=args.verbose,
            )
        except Exception as e:
            if args.json:
                err = {
                    "status": "error",
                    "confidence": 0.0,
                    "data": None,
                    "error": str(e),
                }
                print(json.dumps(err, ensure_ascii=False, indent=2))
            else:
                sys.stderr.write(f"[ocr_pdf] ERROR: {e}\n")
            return 1

    if args.json:
        # Standard envelope {status, confidence, data:{text, meta}}
        # for the Orchestrator/manifest-based architecture (Task 9).
        # Legacy callers reading {text, meta} directly can use .data field.
        confidence = calculate_confidence(text, meta)
        payload = {
            "status": "success",
            "confidence": confidence,
            "data": {
                "text": text,
                "meta": meta,
            },
            # Legacy flat fields (backwards compatibility)
            "text": text,
            "meta": meta,
        }
        out = json.dumps(payload, ensure_ascii=False, indent=2)
        if args.output:
            Path(args.output).write_text(out, encoding="utf-8")
        else:
            print(out)
    else:
        if args.output:
            Path(args.output).write_text(text, encoding="utf-8")
            # Also write a sidecar .meta.json
            meta_path = Path(args.output).with_suffix(".meta.json")
            meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
            if args.verbose:
                sys.stderr.write(f"[ocr_pdf] wrote {args.output} + {meta_path}\n")
        else:
            sys.stdout.write(text)

    # Always print meta summary to stderr (unless --json which prints to stdout)
    if not args.json:
        sys.stderr.write(
            f"[ocr_pdf] ocr_used={meta['ocr_used']} "
            f"pages={meta['pages_processed']}/{meta['pages_total']} "
            f"chars/page={meta['chars_per_page_avg']} "
            f"method={meta['extraction_method']} "
            f"elapsed={meta['elapsed_sec']}s\n"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
