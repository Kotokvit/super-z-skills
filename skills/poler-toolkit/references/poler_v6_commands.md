# poler_v6.py — CLI Reference

## Subcommands

### `analyze` — TF-IDF analysis of a single file

```bash
poler_v6.py analyze FILE [options]
```

Options:
- `--theme THEME` — biology | astronomy | geography | cultivation | navigation | general (default: general)
- `--quiet` — suppress progress output
- `--format FORMAT` — text | json | markdown (default: text)
- `--keywords KW1,KW2,...` — override theme keywords

Output:
- Fragments with normalized epsilon (0-100)
- Cluster IDs
- Section detection
- Top keyword matches

### `grep` — regex search (grep-killer)

```bash
poler_v6.py grep PATTERN FILE [options]
```

Options:
- `-n` — show line numbers
- `-C N` — N lines of context before+after
- `-B N` — N lines before only
- `-A N` — N lines after only
- `-k KEYWORDS` — highlight these keywords in matches
- `--include GLOB` — only match files matching GLOB (for grep_dir)
- `--exclude GLOB` — skip files matching GLOB
- `--format FORMAT` — text | json

Works on: .txt, .md, .py, .js, .ts, .json, .yaml, .html, .css, .csv, .epub, .zip, .tar, .gz, .tgz, .png (metadata only), .log, any text-like file.

### `analyze_dir` — recursive directory analysis

```bash
poler_v6.py analyze_dir DIR [options]
```

Options:
- `--recursive` — recurse into subdirectories
- `--cross-resonance` — compute Jaccard similarity between all file pairs
- `--include-images` — include PNG metadata in analysis
- `--theme THEME` — same as analyze
- `--quiet`

### `diff` — keyword-aware file diff

```bash
poler_v6.py diff FILE1 FILE2 [-k KEYWORDS]
```

Output: structured diff with keyword overlap scores.

### `api` — start HTTP server

```bash
poler_v6.py api [--port 8000] [--host 0.0.0.0]
```

Endpoints:
- `POST /analyze` — body: `{"file": "/path", "theme": "biology"}`
- `POST /analyze_many` — body: `{"files": ["/path1", "/path2"]}`
- `POST /search` — body: `{"file": "/path", "query": "multi word query"}`
- `POST /grep` — body: `{"file": "/path", "pattern": "regex", "context": 3}`
- `POST /grep_dir` — body: `{"dir": "/path", "pattern": "regex", "include": "*.py"}`
- `GET /health`

## Backwards compatibility (v4 mode)

If the first argument is NOT a subcommand, poler_v6.py treats it as a file path and runs `analyze`:

```bash
poler_v6.py /path/to/file.md          # ≡ poler_v6.py analyze /path/to/file.md
poler_v6.py /path/to/file.md --quiet  # ≡ poler_v6.py analyze /path/to/file.md --quiet
```

This preserves v4 CLI behavior.

## Auto-chunk mode (v6.1+)

```bash
poler_v6.py analyze HUGE_FILE.epub --auto-chunk
```

POLER automatically:
1. Splits file into clusters
2. AI processes as many clusters as fit in context
3. Excess goes to swap-file (OPFS/tmpfile)
4. Continues until 100% coverage
5. Returns full result

Use `--read-cluster N` to read a specific cluster manually.

## Exit codes

- `0` — success
- `1` — file not found / unreadable
- `2` — invalid regex pattern
- `3` — unsupported file extension
- `4` — archive corruption
- `5` — HTTP API failed to start (port in use)

## Performance notes

- Files <100 KB: single-pass `analyze_text()` (~50ms typical)
- Files >100 KB: streaming 2-pass `analyze_large_file()` (~1s per MB)
- EPUB/ZIP: in-memory deflate, no disk extraction
- grep: line-by-line regex, O(N) where N = line count
- Full-text search: inverted index, O(1) lookup per query term

---

# topic_common / topic_local / topic_llm — Automatic Topic Detection

Three companion scripts in `scripts/` that **automatically determine the topic** of any document or code file. Two parallel implementations — one local-only, one with LLM integration — share a common file-reading/chunking/code-detection layer.

## Architecture

```
topic_common.py  ← shared: read_file (epub/zip/tar.gz via poler_v6),
                   is_code, detect_language, split_into_chunks,
                   extract_code_entities, format_output_{human,json}
        ↑
        ├── topic_local.py  — pure stdlib TF-IDF + KMeans/agglomerative clustering
        │                     (optional: sklearn for better clustering)
        │                     NO LLM. Topic = list of top-3 TF-IDF bigrams per cluster.
        │
        └── topic_llm.py    — uses z-ai CLI (my internal weights via LLM API)
                              Topic = one human-readable phrase per cluster.
                              Falls back to topic_local if z-ai unavailable.
```

## Common usage (both versions)

```bash
python topic_local.py FILE [--format text|json] [--max-chunk-size 1500] [--max-clusters 10]
python topic_llm.py   FILE [--format text|json] [--max-chunk-size 1500] [--max-clusters 10] [--verbose]
```

Both accept: `.txt`, `.md`, `.markdown`, `.epub`, `.zip`, `.tar.gz`, `.tgz`, `.gz`, `.py`, `.rs`, `.js`, `.ts`, `.go`, `.c`, `.cpp`, `.java`, `.html`, `.json`, `.yaml`, `.sh`, and ~30 other code extensions.

Both produce the same output structure (`is_code`, `overall_topic`, `clusters[]`, `method`) so an agent can swap them transparently.

## Output: text mode (human / agent reads)

```
📄 ТЕКСТ — разбит на 4 кластера
🎯 Общая тема: Архитектура литературного движка POLER[Ψ]

  Кластер 1 (12 фрагм.):
    Тема: SCF метод в pyscf
    Превью: mo_coeff : 2D array ...

  Кластер 2 (11 фрагм.):
    Тема: Квантово-химические вычисления
    ...
```

For code:
```
📦 КОД — язык: Python
🎯 Назначение: Python — Многофункциональный анализатор текста с автоматической кластеризацией

Структура:
  Классы:     Fragment, TextWindow, GrepResult, ...
  Функции:    detect_theme, analyze_text, grep_search_file, ...
  Импорты:    argparse, fnmatch, gzip, json, math, ...
```

## Output: JSON mode (agent parses)

```json
{
  "is_code": false,
  "path": "/path/to/file.md",
  "overall_topic": "Архитектура литературного движка POLER[Ψ]",
  "num_clusters": 4,
  "clusters": [
    {"cluster_id": 0, "size": 12, "topic": "SCF метод в pyscf", "preview": "..."},
    ...
  ],
  "method": "llm-zai; clustering=tfidf+kmeans+silhouette",
  "total_chunks": 42
}
```

## Which one to use?

| Aspect | topic_local.py | topic_llm.py |
|---|---|---|
| Dependencies | None (sklearn optional) | z-ai CLI must be installed + configured |
| Speed | ~50ms-2s per file | 3-30s per file (1 LLM call per cluster) |
| Topic quality | "princess mary, she said, nat sha" (keywords) | "Политические взгляды Анны Павловны" (semantic) |
| Code topic | "Python — классы: Fragment, GrepResult" (entity list) | "Python — Многофункциональный анализатор текста..." (purpose) |
| Offline | Yes | No |
| Cost | Free | LLM API calls |
| Backward compatible | Yes (same output schema) | Yes (falls back to topic_local if z-ai missing) |

## Algorithm (both versions)

1. `read_text(path)` — uses `poler_v6.read_file()` so EPUB/ZIP/TAR are supported
2. `is_code(text, path)` — by file extension OR content heuristic (regex of `def`/`fn`/`func`/`#include`/etc.)
3. If code → **Mode B**: detect language by extension (or content regex), extract entities (classes/functions/imports/constants via regex), LLM gives one-phrase purpose
4. If prose → **Mode A**:
   - Split into chunks (~1500 chars each, by paragraph then by sentence)
   - Cluster: sklearn KMeans + TF-IDF + silhouette (if sklearn installed) OR agglomerative Jaccard fallback
   - For each cluster: extract topic (TF-IDF bigrams for local; LLM phrase for LLM version)
   - For whole document: extract overall topic (TF-IDF bigrams for local; LLM phrase for LLM version)

## Verification (2026-07-03)

Tested on 4 corpora. Comparison:

| File | topic_local | topic_llm |
|---|---|---|
| POLER-ERI-v3.2.0.epub (9 KB) | "vrr-c cross-terms, archetype equation, merkle tree" | "Квантово-вдохновлённый мета-компилятор для химии" |
| poler_v6.py (152 KB Python) | "Python — классы: Fragment, GrepResult, ..." | "Python — Многофункциональный анализатор текста с автоматической кластеризацией и поиском по файлам" |
| 118-POLER Integration.md (151 KB) | "POLER, SCF, mf, dm" (keyword list) | "Архитектура литературного движка POLER[Ψ]" (semantic) |
| Архів.tar.gz (7 MB, mixed) | "princess mary, she said, nat sha" | "poler_toolkit AI Agent Usage Guide" + per-cluster Tolstoy "War and Peace" chapter topics |

---

# `ingest.py` — Unified Document Ingestion Pipeline (v1.0, 2026-07-03)

**Replaces**: the multi-step "find → download → convert → theme-detect" workflow
that was hitting HTTP 429 rate limits (7 docs × 7 clusters = 49 LLM calls).

**Location**: `scripts/ingest.py`

**One command does it all**: extract text (from any source) + detect theme
(local, no LLM) + extract keywords + chunk clusters — in a single JSON output.

## Usage

```bash
# Local file (PDF/MD/EPUB/ZIP/code/...)
python3 scripts/ingest.py INPUT.pdf --json

# URL (curl + HTML strip)
python3 scripts/ingest.py https://example.com/article.html --json

# stdin
cat notes.md | python3 scripts/ingest.py - --json

# With 1 LLM call for semantic topic (optional)
python3 scripts/ingest.py INPUT.pdf --llm --json

# Smaller payload (omit text field)
python3 scripts/ingest.py big.pdf --no-text --json
```

## Source-type auto-detection

| Input format | Detection | Extraction method |
|---|---|---|
| `https://...` / `http://...` | URL scheme | curl + HTML tag strip (noise tags removed first) |
| `-` | stdin literal | read bytes from stdin, UTF-8 decode |
| `.pdf` | extension | → `pdf-ocr` skill (auto-detect digital vs scanned) |
| `.html` / `.htm` | extension | HTML tag strip (same as URL) |
| `.md` `.txt` `.epub` `.zip` `.tar.gz` `.py` `.rs` `.c` ... | extension | `poler_v6.read_file()` (zero-unpack for archives) |

## PDF → pdf-ocr skill

PDFs are routed to `skills/pdf-ocr/scripts/ocr_pdf.py`:

1. `pdfinfo` → page count
2. `pdftotext -layout -l N` → text from first N pages
3. If `chars_per_page >= 100` → digital path (return text, ~0.05s/page)
4. Else → OCR path:
   - `pdftoppm -r 300 -png` (render each page)
   - `tesseract page-N.png - -l rus+ukr+eng --psm 3` (with bundled tessdata)
   - Concatenate per-page text (~2-4s/page)

The `pdf-ocr` skill bundles `rus.traineddata` + `ukr.traineddata` in its
`tessdata/` directory — **no root access needed** for Russian/Ukrainian OCR.

## Theme detection (LOCAL, no LLM by default)

Uses `poler_v6.detect_theme_with_scores(text)` which:
1. Scores text against 5 THEMES vocabularies (биология, астрономия, география, культивация, навигация)
2. Short/numeric vocab tokens use `\b...\b` word-boundary match (avoids false positives like `33` inside `16333.7`)
3. Accepts theme only if `≥2 distinct vocab words matched OR ≥5 total score`
4. Returns `(theme_name, scores_dict, distinct_counts_dict)`

Result stored in JSON as:
```json
"theme": {
  "name": "география",
  "scores": {"биология": 0, "астрономия": 0, "география": 3, ...},
  "distinct_words": {"биология": 0, ..., "география": 2, ...},
  "method": "poler_themes (local, no LLM)"
}
```

## Optional `--llm` flag (1 LLM call max)

When passed, makes ONE `z-ai chat` call with a 4000-char sample of the text
(start + middle + end) and the prompt:

> Назови ОБЩУЮ ТЕМУ этого документа одной фразой (2-7 слов).

The response is stored in `theme.semantic`:
```json
"theme": {
  "name": "general",                    // poler THEMES result
  "scores": {...},
  "semantic": "Теория функционала плотности Кона-Шэма",  // LLM result
  "method": "poler_themes + 1 LLM call (--llm)"
}
```

**This is the key anti-429 design**: instead of 7 docs × 7 clusters = 49 LLM
calls (which triggers rate limit), each document makes at most 1 LLM call.
For 7 docs that's 7 calls (well under the limit), and you can space them out
with `sleep 1` between invocations if needed.

## Output JSON schema

```json
{
  "text": "...",                          // omitted if --no-text
  "meta": {
    "source": "/path or URL",
    "source_type": "file|url|stdin",
    "format": "pdf|md|html|epub|...",
    "ocr_used": false,
    "extraction_method": "pdftotext|curl+html_strip|poler_v6.read_file|tesseract(...)",
    "chars": 12345,
    "is_code": false,
    "lang": "python|rust|...|null",
    "n_chunks": 8,
    "total_elapsed_sec": 0.1,
    // PDF-specific:
    "pages_processed": 3,
    "chars_per_page_avg": 2058.5,
    // URL-specific:
    "url_title": "...",
    "html_bytes": 12583,
    "fetch_sec": 0.07
  },
  "theme": {
    "name": "...",
    "scores": {...},
    "distinct_words": {...},
    "method": "poler_themes (local, no LLM)",
    "semantic": "..."                     // only if --llm
  },
  "keywords": ["...", "..."],             // top-5 TF bigrams (filtered by stopwords)
  "clusters": [                           // empty if text < 6000 chars
    {"id": 0, "chars": 1500, "keywords": ["...", "..."]}
  ]
}
```

## Verified performance (2026-07-03)

| Input | Type | chars | ocr | elapsed | LLM? | Notes |
|---|---|---|---|---|---|---|
| `edb_macro_ru_2024.pdf` (3 pp) | PDF digital | 7456 | no | 0.10s | no | pdftotext path, theme=география (3) |
| `scanned_simulated.pdf` (1 pp, no text layer) | PDF scanned | 57 | yes | 2.58s | no | auto-fallback to tesseract rus+ukr+eng |
| `https://example.com` | URL | 127 | no | 0.07s | no | curl + HTML strip |
| `Kohn-Sham DFT.md` (29 KB) | MD | 29581 | no | 0.01s | no | poler_v6.read_file |
| `Kohn-Sham DFT.md` + `--llm` | MD | 29581 | no | 1.90s | yes (1 call) | semantic="Теория функционала плотности Кона-Шэма" |

## Anti-429 architecture

Before (`topic_llm.py` on 7 docs):
```
for doc in 7_docs:
    for cluster in ~7_clusters:
        z-ai chat ...   # 49 LLM calls total → HTTP 429
```

After (`ingest.py --llm` on 7 docs):
```
for doc in 7_docs:
    z-ai chat ...       # 7 LLM calls total → no 429
```

For batch processing where even 7 calls might be too many, drop `--llm` and
use only the local poler THEMES + TF-IDF keywords (zero LLM calls). Use `--llm`
selectively on the most important documents.

## Integration with browser extension

The user's browser extension can call `ingest.py` via subprocess on text it
has already parsed from the page. Two integration patterns:

**Pattern A — extension does parsing, ingest does theme**:
```bash
# Browser extension extracts page HTML, pipes to ingest
echo "$PAGE_HTML" | python3 ingest.py - --json --no-text
```

**Pattern B — ingest does everything (simpler)**:
```bash
# Extension just passes the URL
python3 ingest.py "https://example.com/article" --json --no-text
```

Pattern A is preferred for JS-rendered pages (extension has the DOM after JS
execution); Pattern B is fine for static HTML.
