# POLER Toolkit — Standalone Text-Data Instrument

## What this is

`poler_v6.py` is a **standalone text-data analysis instrument** — part of the broader POLER ecosystem but **fully independent**: zero external dependencies, Python 3.8+ standard library only.

It is NOT the quantum-chemical POLER[Ψ] architecture (that lives in the separate `poler-psi` skill). This skill is purely about **text processing** — grep, search, analyze, diff, EPUB/archive reading, TF-IDF scoring, section detection, cross-file resonance.

## When to invoke this skill

Invoke `poler-toolkit` when the user wants to:

- **Grep through files** with regex, line numbers, context lines, include/exclude filters
- **Search inside large files** (EPUB, ZIP, TAR, GZ) without unpacking to disk
- **Analyze text** with TF-IDF-based epsilon scoring (ε ∈ 0-100), section detection, keyword clustering
- **Diff two files** and get structured difference output
- **Build a search index** over a corpus and run multi-word queries
- **Stream-analyze very large files** (auto-chunk, 2-pass)
- **Read EPUB/ZIP/TAR/GZ** archives in-memory
- **Run HTTP API** for /analyze, /analyze_many, /search, /grep, /grep_dir, /health
- **Ingest any document** (PDF/URL/MD/EPUB/code) end-to-end: extract text + detect theme + extract keywords + cluster — in ONE call, no LLM rate-limit storm (see `scripts/ingest.py` below)

## What's inside

### `scripts/poler_v6.py` (140 KB, 3077 lines, v6.0.0)

The unified build combining v4 (base) + v5 (grep killer) + v6 (auto-chunk + bugfixes):

**Reading layer** (zero-unpack):
- `read_epub()` — EPUB → text
- `read_archive()` — ZIP / TAR / GZ / TGZ → text stream
- `read_file()` — universal dispatch by extension
- `read_png_metadata()`, `json_to_text()`, `scan_directory()`

**Analysis layer**:
- `analyze_text()` — TF-IDF ε normalization 0-100, section detection, clustering
- `analyze_large_file()` — streaming 2-pass for files >100 KB
- `compute_cross_resonance()` — Jaccard similarity between files
- `cluster_fragments()` — adaptive gap clustering (⚠ has O(F²) bug in adaptive branch — see references)
- `cluster_cross_file()` — cross-file Jaccard clustering

**Search layer (grep-killer)**:
- `grep_search_file()` / `grep_search_directory()` — regex with `-n` line numbers, `-C` context, `--include`/`--exclude` filters
- `build_search_index()` / `search_in_text()` — multi-word full-text search
- `grep_format_text()` / `grep_format_json()` — grep-compatible output

**Diff layer**:
- `diff_files()` — structured diff with keyword-aware comparison

**Themes**:
- THEMES dict — biology, astronomy, geography, cultivation, navigation, general
- Each theme has its own keyword vocabulary

**HTTP API** (when run as server):
- `POST /analyze` — analyze single file
- `POST /analyze_many` — batch analyze
- `POST /search` — full-text search
- `POST /grep` — regex search in file
- `POST /grep_dir` — regex search in directory
- `GET /health` — health check

**CLI subcommands** (with v4 backwards compatibility):
```bash
poler_v6.py analyze FILE [--theme biology] [--quiet] [--format json|text|markdown]
poler_v6.py grep PATTERN FILE [-k KEYWORD] [-n] [-C N] [--include GLOB] [--exclude GLOB]
poler_v6.py analyze_dir DIR [--recursive] [--cross-resonance] [--include-images]
poler_v6.py diff FILE1 FILE2 [-k KEYWORD]
poler_v6.py api [--port 8000]
# Backwards compat: `poler_v6.py FILE` → analyze FILE
```

### `scripts/lens_query.py` (16 KB)

LENS — Latent Encoder for Noun Selection. TF-IDF + cosine similarity over a cached corpus. Used to pick the most relevant noun from a knowledge cache given a query prompt.

Currently uses NotebookLM RAG cache (`/home/z/my-project/work/phase_d/lens_rag_cache.json`, 4052 tokens) and Wikipedia LENS cache (`lens_wiki_cache.json`).

### `scripts/z_ai_api.py` (12 KB)

Z.ai GLM-4.6 API client. Used as LLM-as-judge when comparing POLER output against expected target tokens. Not strictly part of the toolkit but commonly used alongside.

### `scripts/ingest.py` (NEW — unified ingestion pipeline, ~440 lines)

End-to-end document ingestion in ONE call — replaces the multi-step
"find → download → convert → theme-detect" workflow that was hitting
HTTP 429 rate limits (7 docs × 7 clusters = 49 LLM calls).

**Input types** (auto-detected):
- Local file: `.pdf`, `.md`, `.txt`, `.epub`, `.zip`, `.tar.gz`, `.py`, `.rs`, …
- URL: `http(s)://...` → curl + HTML strip → text (for JS-rendered pages, use browser extension upstream)
- stdin: pipe raw bytes via `-`

**Pipeline** (all local by default — zero LLM calls):
```
INPUT
  ├─ URL            → curl + HTML strip              → text
  ├─ .pdf           → pdf-ocr skill (auto-detect)    → text
  │                   ├─ digital  → pdftotext (0.07s)
  │                   └─ scanned  → tesseract rus+ukr+eng (2-4s/page)
  ├─ .html/.htm     → HTML strip                     → text
  └─ other          → poler_v6.read_file()           → text
                       ↓
                  text → poler THEMES scoring (LOCAL, <1ms)
                       → TF-IDF keyword extraction (LOCAL)
                       → chunking + per-chunk keywords (LOCAL)
                       ↓
                  JSON {text, meta, theme, keywords, clusters}
```

**Output JSON schema**:
```json
{
  "text": "...",
  "meta": {
    "source": "/path or URL",
    "source_type": "file|url|stdin",
    "format": "pdf|md|html|epub|...",
    "ocr_used": false,
    "chars": 12345,
    "is_code": false,
    "lang": "python|rust|...",
    "n_chunks": 8,
    "total_elapsed_sec": 0.1
  },
  "theme": {
    "name": "биология|астрономия|география|культивация|навигация|general",
    "scores": {"биология": 12, ...},
    "distinct_words": {"биология": 4, ...},
    "method": "poler_themes (local, no LLM)"
  },
  "keywords": ["...", "...", "..."],          // top TF bigrams
  "clusters": [                                // per-chunk keywords
    {"id": 0, "chars": 1500, "keywords": [...]}
  ]
}
```

**Optional `--llm` flag**: adds ONE LLM call (via `z-ai chat`) to summarize
the document's overall topic into a single semantic phrase. This replaces
the 49-LLM-call pattern with 1 LLM call per document — no more 429 storm.

```bash
# Default — fully local, no LLM
python3 ingest.py paper.pdf --json

# With semantic topic (1 LLM call)
python3 ingest.py paper.pdf --llm --json

# URL ingestion
python3 ingest.py https://example.com/article.html --json

# From stdin
cat notes.md | python3 ingest.py - --json

# Smaller payload (omit text)
python3 ingest.py big.pdf --no-text --json
```

**Performance** (verified 2026-07-03):

| Input | Type | chars | ocr | elapsed | LLM? |
|-------|------|-------|-----|---------|------|
| `edb_macro_ru_2024.pdf` (3 pages) | PDF digital | 7456 | no | 0.10s | no |
| `scanned_simulated.pdf` (1 page, no text layer) | PDF scanned | 57 | yes (2.58s OCR) | 2.58s | no |
| `https://example.com` | URL | 127 | no | 0.07s | no |
| `Kohn-Sham DFT.md` (29 KB) | MD | 29581 | no | 0.01s | no |
| `Kohn-Sham DFT.md` + `--llm` | MD | 29581 | no | 1.90s | yes (1 call) → "Теория функционала плотности Кона-Шэма" |

### `scripts/topic_common.py` + `topic_local.py` + `topic_llm.py` (Topic detection suite)

Older separate topic-detection scripts, still useful for batch processing:

- **`topic_common.py`** — shared utilities (file reading, chunking, code detection, language detection, output formatting)
- **`topic_local.py`** — pure-stdlib TF-IDF + agglomerative clustering (no LLM, no sklearn)
- **`topic_llm.py`** — LLM-integrated version via `z-ai chat` (semantic per-cluster + overall topic)

For most use cases prefer the new `ingest.py` (it's `topic_local` + `poler_v6.THEMES` + OCR + URL in one call). Use `topic_llm.py` only when you need per-cluster LLM topics and can afford multiple LLM calls.

## Integration with `pdf-ocr` skill

PDFs in `ingest.py` are routed to the sibling `pdf-ocr` skill at
`/home/z/my-project/skills/pdf-ocr/scripts/ocr_pdf.py`. That skill:
- Bundles `rus.traineddata` + `ukr.traineddata` in `tessdata/` (no root needed)
- Auto-detects digital vs scanned PDFs (threshold: 100 chars/page)
- Falls back to tesseract OCR (rus+ukr+eng, 300 dpi) on scanned PDFs

See `pdf-ocr/SKILL.md` for details.

## Key data structures

```python
@dataclass
class Fragment:
    text: str
    position: int
    normalized_epsilon: float  # 0-100
    cluster_id: int
    section: str
    keyword_count: int

@dataclass
class GrepResult:
    file: str
    line_number: int
    line: str
    context_before: list[str]
    context_after: list[str]
    match_span: tuple[int, int]
```

## Zero dependencies — important

`poler_v6.py` works on **vanilla Python 3.8+**. No `pip install` needed. This is a design constraint — the toolkit must run anywhere, including fresh sandboxes after env reset.

If you find yourself wanting to add `numpy`, `transformers`, `torch` — STOP. That belongs in the `poler-psi` skill (which has those dependencies for SCF experiments). The toolkit stays pure stdlib.

## Known limitations

1. **O(F²) bug in `cluster_fragments()`** — adaptive branch (no sections) does nested loop over all fragments. Needs two-pointer sliding window fix. See `references/known_bugs.md`.

2. **No streaming output** — `analyze_large_file()` reads in chunks but emits the full result at the end. For very large files (>1 GB), memory is a concern.

3. **EPUB reading bug fixed in v6** — large EPUBs (>100 KB) used to hit the streaming path and be opened as binary ZIP in text mode → garbage. v6 checks extension BEFORE streaming.

4. **LENS hit rate is 4/10** — not the claimed 5/10. NotebookLM cache is small (4052 tokens). Wikipedia cache helps but coverage is limited.

## How to run

### Quick grep
```bash
python /home/z/my-project/skills/poler-toolkit/scripts/poler_v6.py grep "TODO|FIXME" /path/to/file.py -n -C 2
```

### Analyze a file
```bash
python /home/z/my-project/skills/poler-toolkit/scripts/poler_v6.py analyze /path/to/file.md --theme biology
```

### Diff two files
```bash
python /home/z/my-project/skills/poler-toolkit/scripts/poler_v6.py diff file1.md file2.md -k "alpha,beta,gamma"
```

### Start HTTP API
```bash
python /home/z/my-project/skills/poler-toolkit/scripts/poler_v6.py api --port 8000
```

## References

- `references/poler_v6_commands.md` — full CLI reference
- `references/known_bugs.md` — known issues including O(F²) cluster bug

## Related skill

For the **quantum-chemical POLER[Ψ] architecture** (SCF + DIIS + McWeeny + POLER Kick over LLM weight space), see the separate `poler-psi` skill. That's a different beast — research code with `numpy`, `torch`, `safetensors` dependencies.
