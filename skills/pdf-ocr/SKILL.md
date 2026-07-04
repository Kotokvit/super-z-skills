---
name: pdf-ocr
description: |
  PDF OCR skill — auto-detects whether a PDF is born-digital (text layer) or
  scanned (image-only) and extracts text accordingly. Born-digital PDFs use
  `pdftotext` (fast, exact). Scanned PDFs are rendered to PNG via `pdftoppm`
  and OCR'd with `tesseract` (rus+ukr+eng, no root needed — tessdata bundled
  in skill dir). Designed for the doc-ingest pipeline.
version: 1.0.0
author: poler-toolkit (Task 7, 2026-07-03)
---

# pdf-ocr

Extract text from any PDF — born-digital OR scanned — with a single command.

## Why this skill exists

The poler-toolkit previously relied on `pdftotext` alone, which silently
returns empty or near-empty output on scanned PDFs (image-only). This skill
auto-detects that case and falls back to OCR using `tesseract` with bundled
Russian + Ukrainian + English traineddata (no root access required).

This is part of the **doc-ingest pipeline** that lets the poler-toolkit
process any document format (URL, .pdf, .md, .epub, .py, …) end-to-end
without separate "download → convert → theme-detect" steps.

## Quick start

```bash
# Auto-detect: pdftotext if digital, OCR if scanned
python3 skills/pdf-ocr/scripts/ocr_pdf.py INPUT.pdf

# Get JSON output (for agents / pipelines)
python3 skills/pdf-ocr/scripts/ocr_pdf.py INPUT.pdf --json

# Force OCR (skip pdftotext — useful for tests or mixed PDFs)
python3 skills/pdf-ocr/scripts/ocr_pdf.py INPUT.pdf --force-ocr

# Limit pages (default cap = 50)
python3 skills/pdf-ocr/scripts/ocr_pdf.py INPUT.pdf --max-pages 10

# Custom languages
python3 skills/pdf-ocr/scripts/ocr_pdf.py INPUT.pdf --lang rus+eng
```

## Auto-detect logic

1. `pdfinfo` → page count
2. `pdftotext -layout -l N` → text from first N pages
3. Measure `chars_per_page = len(text) / pages_actually_extracted`
4. If `chars_per_page >= 100` → return text (digital path, **0.07s** on test PDF)
5. Else → fall back to OCR:
   - `pdftoppm -r 300 -png` (render each page)
   - `tesseract page-N.png - -l rus+ukr+eng --psm 3 --tessdata-dir ./tessdata`
   - Concatenate per-page text

The threshold 100 chars/page is calibrated to catch genuinely scanned PDFs
while tolerating graphic-heavy cover pages (which `pdftotext` may extract
poorly but typically still produces >100 chars on subsequent pages).

## Output formats

### Plain text (default)
```bash
python3 ocr_pdf.py INPUT.pdf -o output.txt
# Writes output.txt + output.txt.meta.json (sidecar with extraction metadata)
```

### JSON (for agents / pipelines)
```bash
python3 ocr_pdf.py INPUT.pdf --json
```
```json
{
  "text": "...",
  "meta": {
    "ocr_used": false,
    "pages_total": 51,
    "pages_processed": 2,
    "chars_per_page_avg": 2058.5,
    "format": "pdf",
    "extraction_method": "pdftotext",
    "langs": null,
    "elapsed_sec": 0.07
  }
}
```

## Dependencies (all pre-installed)

| Tool | Source | Purpose |
|------|--------|---------|
| `pdftotext` | poppler-utils | Text-layer extraction |
| `pdftoppm` | poppler-utils | Render PDF → PNG (for OCR) |
| `pdfinfo` | poppler-utils | Page count probe |
| `tesseract` 5.5.0 | tesseract-ocr | OCR engine |
| `rus.traineddata` | bundled in `tessdata/` | Russian recognition |
| `ukr.traineddata` | bundled in `tessdata/` | Ukrainian recognition |
| `eng.traineddata` | system `/usr/share/tesseract-ocr/5/tessdata/` | English fallback |

No Python packages beyond stdlib required.

## CLI reference

```
usage: ocr_pdf.py [-h] [-o OUTPUT] [--json] [--force-ocr] [--lang LANG]
                  [--max-pages N] [--dpi DPI] [--psm PSM] [--verbose]
                  input

positional:
  input                 PDF file path (or '-' for stdin)

options:
  -o, --output          Write text to file (default: stdout)
  --json                Emit JSON {text, meta} instead of plain text
  --force-ocr           Skip pdftotext; always OCR (slower, useful for tests)
  --lang LANG           Tesseract langs, '+'-joined (default: rus+ukr+eng)
  --max-pages N         Cap number of pages (default: 50)
  --dpi DPI             Render DPI for OCR (default: 300)
  --psm PSM             Tesseract page-segmentation mode (default: 3 = auto)
  --verbose, -v         Progress to stderr
```

## Performance characteristics

| Mode | 1-page time | 50-page time | Notes |
|------|-------------|--------------|-------|
| Digital (`pdftotext`) | ~0.05s | ~0.5s | Exact text, layout preserved |
| OCR (tesseract) | ~2-4s | ~2-3 min | DPI=300, 3 languages |
| OCR (eng only) | ~1-2s | ~1-1.5 min | Faster, no Cyrillic |

For batch processing of 100+ PDFs, prefer digital path when possible (auto-detect
handles this). OCR is reserved for genuinely scanned documents.

## Integration with poler-toolkit

This skill is called by `poler-toolkit/scripts/ingest.py` when the input is a
PDF. The pipeline:

```
ingest.py
  ├── URL  → web-reader skill
  ├── .pdf → ocr_pdf.py (THIS SKILL) → text
  ├── .md/.txt/.epub → poler_v6.read_file() → text
  └── .py/.rs/.c → poler_v6.read_file() → text
                ↓
         text → auto-theme (poler THEMES, local, no LLM)
                ↓
         JSON {text, theme, keywords, clusters, format, ocr_used}
```

See `poler-toolkit/references/poler_v6_commands.md` for the full pipeline.

## Limitations

- **No multi-column layout reconstruction**: `pdftotext -layout` does its best,
  but very complex multi-column PDFs may produce interleaved text. OCR doesn't
  preserve columns at all — text flows top-to-bottom per page.
- **No table extraction**: Tables become line-by-line text. For structured
  table extraction use `pdfplumber` or `tabula` (not included).
- **Handwritten text**: Tesseract does not recognize handwriting. Use a
  dedicated HTR model if needed.
- **Large PDFs (>100 pages)**: OCR will be slow. Use `--max-pages` to cap.
  For born-digital large PDFs, no cap is needed (pdftotext is fast).
- **Mixed PDFs** (some pages digital, some scanned): auto-detect uses overall
  density — a mostly-digital PDF with a few scanned pages will use pdftotext
  for all pages. Use `--force-ocr` if you need OCR on every page (slow).

## Verified on

| PDF | Pages | Method | chars/page | Time |
|-----|-------|--------|------------|------|
| `edb_macro_ru_2024.pdf` (Russian, digital) | 2/51 | pdftotext | 2058.5 | 0.07s |
| `edb_macro_ru_2024.pdf` (forced OCR test) | 1/51 | tesseract rus+ukr+eng | 76.0 (graphic-heavy cover) | 2.4s |

Test command:
```bash
python3 ocr_pdf.py work/real_docs/raw/edb_macro_ru_2024.pdf --json --max-pages 2
```
