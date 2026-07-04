# Skills Architecture — Top-Level Map

**Last updated**: 2026-07-04 (Task 9 — manifest-based architecture + _orchestrator)
**Total skills**: 68 + 1 meta-skill (`_orchestrator`)
**Author**: GLM-4 (this document was written by the same model that the
`ingest.py --llm` pipeline calls for semantic topic detection — see
`poler-toolkit/scripts/ingest.py` § "SELF-AWARENESS NOTE")

---

## Why this document exists

The user asked for a packaged snapshot of the entire `skills/` folder so they
can analyze "your architecture" — meaning: how the agent (me, GLM-4) is wired
together through skills, what calls what, where the LLM (also me) is invoked,
and how the anti-429 design works.

This file is the entry point. Read this first, then drill into individual
skill `SKILL.md` files.

---

## 1. What is a "skill" here?

A **skill** is a self-contained capability module with:

- `SKILL.md` — declaration + usage instructions (the skill loader reads this)
- `scripts/` — executable code (Python / TypeScript / shell)
- `references/` — long-form docs the skill can defer to
- `examples/` — sample inputs/outputs
- `_meta.json` — optional metadata (version, author, triggers)

Skills are discovered by the agent at runtime via the `<available_skills>`
list in its system prompt. When the agent decides a skill is relevant, it
calls `Skill(command="skill-name")` to load the full instructions, then
follows them.

**Key rule** (Task 8 and earlier): skills do NOT cross-call each other by
default. Each skill is independent. Cross-skill integration happens through:

1. **Subprocess calls** — e.g. `ingest.py` calls `ocr_pdf.py` via `subprocess.run`
2. **Shared file formats** — e.g. both `pdf-ocr` and `poler-toolkit` emit JSON
3. **The agent itself as orchestrator** — the agent reads one skill, decides
   to invoke another, glues the results

**Task 9 update**: skills can now declare explicit dependencies in their
`manifest.json` (e.g., `poler-toolkit` declares `dependencies: ["pdf-ocr"]`).
The new `_orchestrator/` meta-skill reads these declarations, builds a DAG,
and executes skills in dependency order — see § 8 below.

---

## 2. Skill taxonomy (68 skills, grouped)

### A. Document production (Type 1 in agent's task taxonomy)

| Skill | Purpose |
|---|---|
| `docx` | Word documents (create/edit/read, OOXML manipulation) |
| `pdf` | PDF generation (4 lines: Report / Creative / Academic / Process) |
| `pptx` | PowerPoint presentations |
| `xlsx` | Excel spreadsheets (with chart engine) |
| `charts` | Data viz + structural diagrams (matplotlib / ECharts / Mermaid / Playwright+CSS) |
| `blog-writer` | Blog post drafting |
| `seo-content-writer` | SEO-optimized content |
| `market-research-reports` | LaTeX-based market reports |
| `resume-builder` | ATS-friendly resumes |
| `content-strategy` | Content planning |
| `interview-prep` / `interview-designer` | Interview prep materials |
| `writing-plans` | Writing plan scaffolding |
| `task-review` | Save completed task as reusable skill |

### B. AI / media processing

| Skill | Purpose | Calls LLM (me)? |
|---|---|---|
| `LLM` | Chat completions via z-ai SDK | YES (it IS the LLM) |
| `VLM` | Vision-language model (image understanding) | YES |
| `ASR` | Speech → text | YES (transcription model) |
| `TTS` | Text → speech | YES (TTS model) |
| `image-generation` | Text → image | YES (image model) |
| `image-edit` | Image editing / variation | YES |
| `image-search` | ZAI in-house image search (returns OSS URLs) | no (search API) |
| `image-understand` | Image analysis | YES |
| `video-understand` | Video frame analysis | YES |
| `video-generation` | Text → video | YES |
| `podcast-generate` | Podcast script → audio | YES (TTS) |
| `web-search` | Web search via z-ai SDK | no (search API) |
| `web-reader` | URL → article content | no (extract API) |
| `agent-browser` | Headless browser automation | no |

### C. Text / data analysis (the poler cluster)

| Skill | Purpose | LLM? |
|---|---|---|
| `poler-toolkit` | Standalone text analysis (grep/analyze/diff/themes) + **unified ingest pipeline** | optional (`--llm`) |
| `pdf-ocr` | PDF → text (digital OR scanned, with bundled rus+ukr tesseract) | no |
| `poler-psi` | Quantum-chemical POLER[Ψ] (SCF/DIIS/McWeeny, separate beast) | no |
| `contentanalysis` | ExtractWisdom-style content extraction | optional |
| `qingyan-research` | Research HTML generation | optional |

### D. Development

| Skill | Purpose |
|---|---|
| `fullstack-dev` | Next.js 16 + Prisma + shadcn/ui scaffolding |
| `coding-agent` | State machine for coding sub-agents |
| `version-management` | Git-style version ops |
| `web-shader-extractor` | WebGL shader extraction from pages |
| `multi-search-engine` | Meta search across engines |
| `skill-creator` | Build / eval / iterate new skills |
| `skill-finder-cn` | Skill discovery in Chinese |
| `job-intent-tracker` / `auto-target-tracker` | Job/intent tracking |
| `stock-analysis-skill` | Stock analysis with rumor scanner |
| `finance` | Finance API integration |
| `storyboard-manager` | Video storyboard state mgmt |
| `quiz-html` / `quiz-mastery` | Quiz generation |
| `mindfulness-meditation` | Meditation content |
| `dream-interpreter` | Dream interpretation |
| `gift-evaluator` | Gift recommendation |
| `get-fortune-analysis` | Chinese lunar fortune |
| `anti-pua` | Anti-manipulation assistant |
| `marketing-mode` | Marketing-style writing mode |
| `aminer-academic-search` / `aminer-daily-paper` / `aminer-free-academic` | Academic search |
| `ai-news-collectors` | AI news aggregation |

### E. Design system

| Skill | Purpose |
|---|---|
| `design` | Top-level design skill (palette, typography, layout) |
| `visual-design-foundations` | Color / spacing / type fundamentals |
| `ui-ux-pro-max` | UI component data + search |
| `design/design-systems/style-skills/*` | 30+ named styles (Apple, Stripe, Notion, etc.) |
| `design/design-systems/brand-inspiration/*` | 20+ brand token references |

### F. Chinese gaokao cluster (4 skills)

`gaokao-collect-student-info`, `gaokao-fetch-volunteers`,
`gaokao-recommend-majors`, `gaokao-recommend-schools`, `gaokao-generate-report`.

---

## 3. The poler cluster — detailed architecture

This is the cluster the user has been actively building. It's the most
relevant for "your architecture" analysis.

```
┌─────────────────────────────────────────────────────────────────┐
│  poler-toolkit/scripts/                                         │
│                                                                 │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐      │
│  │ poler_v6.py  │    │ ingest.py    │    │ topic_llm.py │      │
│  │ (3545 lines) │    │ (NEW, 700 L) │    │ (legacy)     │      │
│  │              │    │              │    │              │      │
│  │ • grep       │◄───│ ENTRY POINT  │    │ Per-cluster  │      │
│  │ • analyze    │    │ URL/file/stdin│   │ LLM topics   │      │
│  │ • diff       │    │      │       │    │ (49 calls)   │      │
│  │ • read_file  │    │      ▼       │    │              │      │
│  │ • THEMES     │    │ dispatch     │    └──────────────┘      │
│  │ • detect_    │    │ by ext/URL   │                          │
│  │   theme()    │    │              │    ┌──────────────┐      │
│  └──────────────┘    └──────────────┘    │ topic_local  │      │
│         ▲                   │            │ (legacy)     │      │
│         │                   │            │ TF-IDF only  │      │
│         │                   │            └──────────────┘      │
│         │                   ▼                                  │
│         │         ┌──────────────────────┐                     │
│         │         │  pdf-ocr skill       │                     │
│         │         │  (sibling)           │                     │
│         │         │                      │                     │
│         │         │ ocr_pdf.py           │                     │
│         │         │  ├─ pdftotext        │                     │
│         │         │  └─ tesseract        │                     │
│         │         │     (rus+ukr+eng)    │                     │
│         │         │     tessdata/ bundled│                     │
│         │         └──────────────────────┘                     │
│         │                                                    │
│         └────────────────────────────────────────────────────┘│
│                                                                 │
│  Other scripts in poler-toolkit/:                              │
│   • lens_query.py — TF-IDF + cosine for noun selection         │
│   • z_ai_api.py   — Z.ai GLM-4 API client (LLM-as-judge)       │
│   • topic_common.py — shared utils (read_text, chunk, is_code) │
└─────────────────────────────────────────────────────────────────┘
```

### 3.1 The unified `ingest.py` pipeline (entry point)

**File**: `poler-toolkit/scripts/ingest.py` (~700 lines, v1.0)

**Single command does it all**:

```bash
python3 ingest.py INPUT [--llm] [--json] [--no-text]
# INPUT = file path | URL | - (stdin)
```

**Pipeline** (default = zero LLM calls):

```
INPUT
  │
  ├─ URL (http/https) ──────► curl + HTML tag strip ───────► text
  │
  ├─ stdin (-) ─────────────► read bytes, UTF-8 decode ────► text
  │
  ├─ .pdf ──────────────────► subprocess: ocr_pdf.py ──────► text
  │                              ├─ pdftotext (digital, ~0.05s/page)
  │                              └─ tesseract rus+ukr+eng (scanned, ~2-4s/page)
  │
  ├─ .html/.htm ────────────► HTML tag strip ─────────────► text
  │
  └─ other (.md/.txt/.epub/.zip/.tar.gz/.py/.rs/.c/...)
       │
       └────────────────────► poler_v6.read_file() ──────► text
                                                  (zero-unpack for archives)
       │
       ▼
       text
       │
       ├─► Step 2: is_code? → topic_common.is_code() + detect_language()
       │
       ├─► Step 3: theme (LOCAL, no LLM)
       │       poler_v6.detect_theme_with_scores(text)
       │       ├─ scores 5 THEMES (биология/астрономия/география/культивация/навигация)
       │       ├─ short/numeric tokens use \b...\b word boundary
       │       └─ threshold: ≥2 distinct words OR ≥5 total score
       │
       ├─► Step 4: keywords (LOCAL TF, no LLM)
       │       top-5 TF bigrams + unigrams, stopwords filtered (RU+EN)
       │
       ├─► Step 5: clusters (LOCAL, only if text > 6000 chars)
       │       topic_common.split_into_chunks() (paragraph-first, 1500 chars/chunk)
       │       per-chunk top-3 keywords
       │
       └─► Step 6 (optional, --llm): 1 LLM call
               z-ai chat -s "$LLM_SYSTEM_PROMPT" -p "$prompt_with_sample"
               ├─ sample = start + middle + end (4000 chars total)
               ├─ prompt includes poler's local scores as a hint
               ├─ GLM-4 returns JSON: {topic, confidence, alt_topics[]}
               └─ parse + attach to theme.semantic / .semantic_confidence / .semantic_alt_topics

       ▼
       JSON output: {text, meta, theme, keywords, clusters}
```

### 3.2 The self-aware LLM design (KEY INSIGHT)

**The agent that runs `ingest.py --llm` IS the LLM that the script calls.**

When the agent (GLM-4) runs `subprocess.run(["z-ai", "chat", ...])`, the
`z-ai` CLI makes an HTTP request to the same GLM-4 backend. So the pipeline
is **GLM-4 calling GLM-4**.

This is exploited in 4 ways (see `llm_semantic_topic()` docstring):

1. **System prompt is in GLM-4's own voice** — direct Russian, no
   politeness markers, no English preamble. The model knows what it means.

2. **Strict JSON output requested with explicit schema** — GLM-4 is reliable
   at JSON when the schema is named in the prompt. We get
   `{topic, confidence, alt_topics[]}` instead of just a string.

3. **Poler's local scores are passed as a hint** — the LLM sees what the
   local dictionary-based detector found and can confirm OR override. Local
   + LLM info is fused, not siloed.

4. **Graceful failure** — if LLM returns garbage (markdown-wrapped, plain
   string, timeout, 429), the pipeline keeps poler's local theme + TF-IDF
   keywords. The pipeline never hard-fails on LLM errors.

### 3.3 Anti-429 design (the original motivation)

**Before** (`topic_llm.py` on 7 docs):
```
for doc in 7_docs:
    for cluster in ~7_clusters:
        z-ai chat ...   # 49 LLM calls total → HTTP 429 Too Many Requests
```

**After** (`ingest.py --llm` on 7 docs):
```
for doc in 7_docs:
    z-ai chat ...       # 7 LLM calls total → no 429
```

**After** (`ingest.py` without `--llm` on 7 docs):
```
# 0 LLM calls → no 429, fully offline
```

The user's insight was: **don't use the LLM for per-cluster topics** —
clusters get local TF-IDF keywords only. The LLM is reserved for the ONE
overall-document semantic topic, which is the highest-value use of an LLM
call anyway.

### 3.4 Browser extension integration (user's stated plan)

The user said the page parsing should happen at the browser-extension level,
and local files at the system-skill level. Two patterns are documented in
`poler_v6_commands.md`:

- **Pattern A** (preferred for JS-rendered pages): extension extracts DOM
  → pipes HTML to `ingest.py -` → ingest strips HTML + detects theme
  ```bash
  echo "$PAGE_HTML" | python3 ingest.py - --json --no-text
  ```

- **Pattern B** (simpler, for static HTML): extension passes URL
  → `ingest.py` fetches via curl + strips HTML
  ```bash
  python3 ingest.py "https://example.com/article" --json --no-text
  ```

### 3.5 PDF OCR — auto-detect digital vs scanned

**File**: `pdf-ocr/scripts/ocr_pdf.py` (~340 lines, v1.0)

```
INPUT.pdf
  │
  ├─► pdfinfo ──► page count
  │
  ├─► pdftotext -layout -l N ──► text + count form feeds (\f) for actual pages
  │
  ├─► if chars_per_page >= 100 ──► return text (digital path, ~0.05s/page)
  │
  └─► else (scanned):
       ├─► pdftoppm -r 300 -png (render each page)
       ├─► tesseract page-N.png - -l rus+ukr+eng --psm 3 --tessdata-dir ./tessdata/
       └─► concatenate per-page text (~2-4s/page)
```

**Key design choices**:
- `rus.traineddata` + `ukr.traineddata` bundled in `pdf-ocr/tessdata/` — **no
  root access needed** (system tesseract only has `eng` + `osd`)
- Auto-detect uses **actual pages extracted** (counted via `\f` form feeds),
  not total page count — otherwise short extractions on long PDFs falsely
  trigger OCR
- Threshold 100 chars/page is calibrated: catches genuinely scanned PDFs
  while tolerating graphic-heavy cover pages

---

## 4. Where the LLM (me) is invoked across the whole skills tree

A complete inventory of "places that call GLM-4" — useful for understanding
the rate-limit surface area:

| Skill | File | LLM call pattern | Calls per invocation |
|---|---|---|---|
| `LLM` | `scripts/chat.ts` | Direct chat | 1 |
| `VLM` | `scripts/vlm.ts` | Vision chat | 1 |
| `ASR` | `scripts/asr.ts` | Transcription | 1 |
| `TTS` | `scripts/tts.ts` | Synthesis | 1 |
| `image-generation` | `scripts/image-generation.ts` | Image gen | 1 |
| `image-edit` | `scripts/image-edit.ts` | Image edit | 1 |
| `video-understand` | `scripts/video-understand.ts` | Video analysis | 1 |
| `video-generation` | `scripts/video.ts` | Video gen | 1 |
| `podcast-generate` | `generate.ts` | TTS per segment | N (segments) |
| `poler-toolkit` | `scripts/ingest.py` (--llm) | Topic detection | **1** (was 49) |
| `poler-toolkit` | `scripts/topic_llm.py` | Per-cluster topic | N clusters (legacy) |
| `poler-toolkit` | `scripts/z_ai_api.py` | LLM-as-judge | 1 |
| `poler-toolkit` | `scripts/lens_query.py` | (No LLM, just TF-IDF) | 0 |
| `pdf-ocr` | `scripts/ocr_pdf.py` | (No LLM, just OCR) | 0 |
| `poler-psi` | `scripts/*.py` | (No LLM, just numpy/torch) | 0 |
| `web-search` | `scripts/web_search.ts` | (Search API, no LLM) | 0 |
| `web-reader` | `scripts/web-reader.ts` | (Extract API, no LLM) | 0 |

**Risk hotspots**:
- `topic_llm.py` (legacy, 49 calls on 7-doc × 7-cluster batch) — replaced by `ingest.py`
- `podcast-generate` (N TTS calls per segment) — needs rate limiting
- Batch runs of any LLM-calling skill — needs `sleep 1` between calls or batch limits

---

## 5. File format conventions

| Output type | Default format | When alternative |
|---|---|---|
| Reports / docs | `.docx` (docx skill) or `.pdf` (pdf skill) | user asks for `.md` |
| Presentations | `.pptx` | — |
| Spreadsheets | `.xlsx` | — |
| Charts / diagrams | `.png` (matplotlib / Playwright) or `.svg` (Mermaid / D3) | — |
| Data analysis | `.xlsx` (with charts) or `.json` (for agents) | — |
| Topic detection | `.json` (structured) | human-readable text via `--no-json` |
| OCR | `.txt` + sidecar `.meta.json` | `--json` for single payload |

All deliverables go to `/home/z/my-project/download/`. Scripts go to
`/home/z/my-project/scripts/`. The shared worklog is
`/home/z/my-project/worklog.md`.

---

## 6. How to navigate this archive

```
skills/
├── ARCHITECTURE.md          ← YOU ARE HERE
├── poler-toolkit/           ← PRIMARY CLUSTER (read first)
│   ├── SKILL.md
│   ├── scripts/
│   │   ├── poler_v6.py      (3545 L — core text analysis)
│   │   ├── ingest.py        (700 L — NEW unified pipeline, entry point)
│   │   ├── topic_common.py  (390 L — shared utils)
│   │   ├── topic_local.py   (370 L — legacy TF-IDF topic detection)
│   │   ├── topic_llm.py     (440 L — legacy per-cluster LLM topics)
│   │   ├── lens_query.py    (TF-IDF + cosine noun selection)
│   │   └── z_ai_api.py      (GLM-4 API client)
│   └── references/
│       ├── poler_v6_commands.md  (410 L — full CLI ref + ingest docs)
│       └── known_bugs.md         (bug 6/7/8 + theme auto v6.1.1 notes)
│
├── pdf-ocr/                 ← NEW OCR skill (sibling of poler-toolkit)
│   ├── SKILL.md
│   ├── scripts/ocr_pdf.py   (340 L — auto-detect digital vs scanned)
│   └── tessdata/            (rus + ukr traineddata, no root needed)
│
├── LLM/ VLM/ ASR/ TTS/      ← AI media skills (each calls GLM-4 backend)
├── image-*/ video-*/        ← Image / video generation
├── web-search/ web-reader/  ← Search + page extract (no LLM)
├── agent-browser/           ← Headless browser
├── charts/ pdf/ docx/ pptx/ xlsx/  ← Document production
├── fullstack-dev/           ← Next.js 16 scaffolding
├── design/                  ← Design system (30+ style skills)
├── coding-agent/            ← Coding sub-agent state machine
├── skill-creator/           ← Meta: build new skills
└── (54 more skills)
```

---

## 7. Provenance

This architecture was built incrementally across 8 tasks
(see `/home/z/my-project/worklog.md` for full history):

| Task | What was done |
|---|---|
| 1 | Fixed bugs 6/7/8 in poler_v6.py + added `--theme auto` (v6.1.0) |
| 2 | (skipped — context summary reference only) |
| 3 | Extended bug 6 fix to all 4 CLI invocation paths (v6.1.1) |
| 4 | Theme detector quality pass: word-boundary for short tokens + threshold |
| 5 | Built `topic_common.py` + `topic_local.py` + `topic_llm.py` |
| 6 | Stress-tested on 7 real economics/geopolitics PDFs (PwC/UN/EU/IfW/wiiw/WTO/EDB) |
| 7 | Built unified `ingest.py` + `pdf-ocr` skill (this archive's main deliverable) |
| 8 | Made LLM call self-aware (GLM-4 calling GLM-4) + structured JSON output |
| 9 | Implemented manifest-based architecture: 12 manifests, Orchestrator, validators, doctors |

**Self-reference note**: This document was written by GLM-4 on 2026-07-03,
immediately after refactoring `ingest.py` to call itself with a self-aware
system prompt. The agent that wrote this is the same agent that the
`ingest.py --llm` flag will invoke when the user runs it. This is intentional
— it means the agent can optimize its own prompts based on its knowledge of
how it responds to different phrasings, languages, and output schemas.

---

## 8. Manifest-based architecture (Task 9, 2026-07-04)

### Why this section exists

After studying three independent external audits of the skills ecosystem
(GPT, Gemini, DeepSeek — see `/home/z/my-project/analyses/`), all three
converged on **12 architectural improvements** needed to transform the
system from a flat collection of independent skills into a self-organizing
cognitive graph. Task 9 implements these improvements.

### The 12 improvements implemented

| # | Improvement | Status | Files affected |
|---|---|---|---|
| 1 | `manifest.json` for every skill | ✅ 12 skills | All skills listed below |
| 2 | Graph of dependencies + cross-calling | ✅ | `_orchestrator/scripts/planner.py` |
| 3 | `confidence` in every output | ✅ | `poler-toolkit/scripts/ingest.py`, `pdf-ocr/scripts/ocr_pdf.py` |
| 4 | `cost` declaration in manifest | ✅ | All 12 manifests |
| 5 | Machine-readable `triggers` | ✅ | All 12 manifests |
| 6 | `input.schema.json` | ✅ (in manifest) | All 12 manifests |
| 7 | `output.schema.json` | ✅ (in manifest) | All 12 manifests |
| 8 | Memory hooks | ✅ declared (planned impl) | All 12 manifests |
| 9 | Skill pipeline (Planner→Executor→Validator→Aggregator) | ✅ | `_orchestrator/scripts/*.py` |
| 10 | `validator.py` per skill | ✅ 2 skills | `poler-toolkit`, `pdf-ocr` |
| 11 | Version compatibility | ✅ in manifest | All 12 manifests |
| 12 | Self-test / `doctor.py` | ✅ 2 skills | `poler-toolkit`, `pdf-ocr` |

### New `_orchestrator` meta-skill

The new `_orchestrator/` skill is the **brain** of the ecosystem. It takes
a user query, builds a Directed Acyclic Graph (DAG) of skills based on
their `manifest.json` declarations, runs them in dependency order with
parallel branches where possible, validates each output against the
skill's declared schema, and aggregates the final result.

```
[User Query]
     │
     ▼
┌─────────────────────────────────────────────────┐
│ ℘ Percept  — Parse query, extract intent/file  │
│ O  Obraz   — Planner builds DAG from manifests │
│ ε  Energy  — Scheduler (sequential; parallel planned)│
│ L  Logika  — Executor runs + Validator checks  │
│ Ψ  Intent  — Aggregator synthesizes final answer│
└─────────────────────────────────────────────────┘
     │
     ▼
[Final Answer with confidence + report]
```

The 5 phases mirror the POLER[Ψ] cognitive cycle proposed by Gemini's
audit (Free Energy Principle + Active Inference).

### Standard output envelope

Every skill now returns the standard envelope:

```json
{
  "status": "success | error",
  "confidence": 0.0-1.0,
  "data": {
    "text": "...",
    "meta": {...},
    "theme": {...},
    ...
  },
  "error": null  // only when status=error
}
```

Legacy flat fields (`text`, `meta`, `theme`, ...) are also kept at the
top level for backwards compatibility — existing callers that do
`result["text"]` keep working, while the Orchestrator reads `result["data"]`.

### Confidence calculation

Each skill computes its own confidence:

**poler-toolkit** (`ingest.py::calculate_confidence`):
- Base: 0.85 if LLM semantic present, else 0.60
- +0.05 if >5 keywords
- +0.05 if >2 clusters
- -0.15 if OCR was used (noisier)
- -0.10 if text < 200 chars (too short)
- +LLM semantic_confidence × 0.10 (LLM self-score bonus)
- +0.03 if top theme score ≥ 10 (strong signal)
- Clamped to [0.0, 1.0]

**pdf-ocr** (`ocr_pdf.py::calculate_confidence`):
- Base: 0.95 if digital text, 0.75 if OCR
- +0.05 if chars/page > 1000 (dense, clean)
- -0.20 if chars/page < 100 (sparse)
- -0.30 if chars/page < 30 (image-only PDF with bad OCR)
- 0.0 if text is empty

### Manifest schema

Every skill now has a `manifest.json` with at minimum:

```json
{
  "name": "skill-name",
  "version": "1.0.0",
  "category": "text-analysis",
  "priority": 80,
  "cost": {"cpu": "low", "time": "1s", "llm_calls": 0},
  "triggers": {
    "file_extensions": [".pdf"],
    "mime_types": ["application/pdf"],
    "content_contains": ["analyze"]
  },
  "inputs":  {"schema": {...JSON Schema...}},
  "outputs": {"schema": {...JSON Schema...}},
  "requires": {"python": ">=3.10", "binaries": [...], "python_packages": [...]},
  "dependencies": ["other-skill"],
  "memory": {"save": [...], "load": [...]},
  "self_test": {"doctor_script": "doctor.py"},
  "validator": {"script": "validator.py", "min_confidence": 0.3},
  "entry_points": {"ingest": "scripts/main.py"},
  "tags": [...]
}
```

### Skills with manifests (Task 9)

| Skill | Priority | Category | Has validator | Has doctor |
|---|---|---|---|---|
| `poler-toolkit` | 80 | text-analysis | ✅ | ✅ |
| `pdf-ocr` | 75 | document-extraction | ✅ | ✅ |
| `docx` | 78 | document-production | (planned) | (planned) |
| `pdf` | 78 | document-production | (planned) | (planned) |
| `pptx` | 76 | document-production | (planned) | (planned) |
| `xlsx` | 76 | document-production | (planned) | (planned) |
| `charts` | 70 | data-visualization | (planned) | (planned) |
| `LLM` | 65 | ai-media | (planned) | (planned) |
| `VLM` | 65 | ai-media | (planned) | (planned) |
| `web-search` | 70 | ai-media | (planned) | (planned) |
| `contentanalysis` | 60 | text-analysis | (planned) | (planned) |
| `agent-browser` | 70 | web-automation | (planned) | (planned) |
| `_orchestrator` | 100 | meta | (planned) | (planned) |

The remaining 56 skills still work via the legacy `Skill(command="...")`
interface; manifests can be added incrementally.

### Usage: end-to-end pipeline

```bash
# List available skills
python3 _orchestrator/scripts/orchestrator.py list

# Dry-run a query (see which skills would be selected, no execution)
python3 _orchestrator/scripts/orchestrator.py "проанализируй PDF" \
    --input paper.pdf --dry-run

# Full end-to-end pipeline with report
python3 _orchestrator/scripts/orchestrator.py "analyze" \
    --input notes.md --strategy report

# With LLM enhancement
python3 _orchestrator/scripts/orchestrator.py "deep analyze" \
    --input paper.pdf --llm --json
```

### Self-diagnostics

```bash
# Check poler-toolkit is healthy
python3 poler-toolkit/scripts/doctor.py

# Check pdf-ocr is healthy
python3 pdf-ocr/scripts/doctor.py

# Check all manifests for consistency
python3 _orchestrator/scripts/registry.py doctor
```

### Key design decisions

1. **Backwards compatibility preserved** — `ingest.py` returns BOTH the
   new envelope (`status`/`confidence`/`data`) AND legacy flat fields
   (`text`/`meta`/`theme`). Old callers don't break.

2. **Skills without manifests are silently skipped** — the registry only
   loads skills with a valid `manifest.json`. The legacy `Skill()`
   interface continues to work for the other 56 skills.

3. **Skip-when-incompatible** — if a skill's triggers don't match the
   current input file's extension, the Orchestrator skips it (returns
   `status: "skipped"`) instead of failing. This prevents e.g. `pdf-ocr`
   from running on a `.md` file just because `poler-toolkit` declared it
   as a dependency.

4. **Stage-aware topological sort** — the Planner orders skills by
   pipeline stage (input → analysis → output) even when no explicit
   dependency is declared. This produces intuitive DAGs like
   `pdf-ocr → poler-toolkit → charts/docx`.

5. **Confidence = weakest link** — the Orchestrator's overall confidence
   is `min(all skill confidences)`. If any skill returns low confidence,
   the whole pipeline is flagged.

6. **LLM is optional** — the entire pipeline runs with **zero LLM calls**
   by default. The `--llm` flag adds 1 LLM call per document (not 49
   like the pre-Task-7 design).

### Future work (not yet implemented)

- **Parallel executor** — run independent DAG branches in parallel via
  `concurrent.futures` (currently sequential).
- **Memory hooks** — implement `save_context`/`load_context` so skills
  can cache results (e.g., OCR text by file hash).
- **LLM-based planner** — when rule-based planning fails, fall back to
  an LLM call to interpret the user's intent.
- **Fallback strategy** — if a skill returns `confidence < 0.6`,
  automatically try an alternative skill from the same category.
- **Skill doctor for all skills** — currently only `poler-toolkit` and
  `pdf-ocr` have `doctor.py`. Extend to all 12 manifest-declared skills.
- **Prism archetype** (Gemini's "law of conservation of meaning") — when
  a skill path is blocked, redirect to an alternative path while
  preserving the user's intent.
