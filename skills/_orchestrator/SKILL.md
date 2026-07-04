# _orchestrator — Skills Pipeline Orchestrator

## What this is

The `_orchestrator` skill is the **brain** of the skills ecosystem. It takes a user query in natural language, builds a Directed Acyclic Graph (DAG) of skills to execute based on their `manifest.json` declarations, runs them in dependency order, validates each output, and aggregates the final result.

This is the practical implementation of the architecture recommended by the three external audits (GPT, Gemini, DeepSeek):

- **GPT** → manifest-driven routing (no LLM needed for skill selection)
- **Gemini** → 5-phase POLER[Ψ] cycle (℘-O-L-ε-R[n]-Ψ)
- **DeepSeek** → concrete Orchestrator class on Python

## When to invoke this skill

Invoke `_orchestrator` when the user wants to:

- **Run a multi-step pipeline** ("analyze this PDF and make a report with charts")
- **Chain skills automatically** ("convert PDF → analyze → visualize → export")
- **List available skills** and see their priorities/categories
- **Dry-run a query** to see which skills would be selected
- **Aggregate results** from a previous multi-skill run

DO NOT invoke `_orchestrator` for single-skill requests — call the target skill directly (e.g., `poler-toolkit` for "analyze this text").

## Pipeline architecture (5 phases)

```
[User Query]
     │
     ▼
┌─────────────────────────────────────────────────┐
│ ℘ Percept  — Parse query, extract intent/file  │
│ O  Obraz   — Planner builds DAG from manifests │
│ ε  Energy  — Scheduler (currently sequential)  │
│ L  Logika  — Executor runs + Validator checks  │
│ Ψ  Intent  — Aggregator synthesizes final answer│
└─────────────────────────────────────────────────┘
     │
     ▼
[Final Answer]
```

## Components

### `scripts/registry.py` — Skill Registry

Loads all `manifest.json` files from sibling skill directories. Builds indexes for O(1) lookup by:
- file extension (`.pdf` → `[pdf-ocr]`)
- MIME type (`application/pdf` → `[pdf-ocr]`)
- content keyword (`"анализ"` → `[poler-toolkit]`)
- category (`text-analysis` → `[poler-toolkit, contentanalysis]`)

CLI: `python3 registry.py list | show NAME | find QUERY | categories | stats | doctor`

### `scripts/planner.py` — Planner

Takes a user query + optional input file path, returns a DAG:
1. Match intent rules (regex patterns → candidate skills)
2. Match file extension → candidate skills
3. Match content keywords → candidate skills
4. Union all candidates
5. Resolve transitive dependencies
6. Topologically sort (Kahn's algorithm)
7. Identify parallel branches

CLI: `python3 planner.py "QUERY" [--input FILE] [--json]`

### `scripts/executor.py` — Executor

Runs a single skill via subprocess:
1. Find entry point script (from manifest's `entry_points`)
2. Build CLI args based on skill conventions
3. Run via `subprocess.run`, capture stdout JSON
4. Parse output envelope `{status, confidence, data}`
5. Run skill's `validator.py` on output
6. Attach `_execution` and `_validation` metadata

CLI: `python3 executor.py SKILL_NAME --input '{"input": "x.pdf"}'`

### `scripts/aggregator.py` — Aggregator

Combines results from multiple skills. Strategies:
- **last** — return last successful skill's output (default for simple pipelines)
- **merge** — merge all `.data` dicts (later skills override earlier)
- **report** — build a Markdown report summarizing the whole pipeline (recommended)
- **files** — collect all output file paths

### `scripts/orchestrator.py` — Top-level Orchestrator

Ties everything together. End-to-end pipeline:
```python
from orchestrator import Orchestrator
orch = Orchestrator("/home/z/my-project/skills")
result = orch.process("analyze this PDF and make a report", input_path="paper.pdf")
```

CLI: `python3 orchestrator.py "QUERY" --input FILE [--strategy report] [--json] [--dry-run]`

## Usage examples

### List available skills

```bash
python3 orchestrator.py list
```

Output:
```
68 skills available:

  LLM                            v1.0.0   pri= 65  [ai-media]
  VLM                            v1.0.0   pri= 65  [ai-media]
  ...
  poler-toolkit                  v6.1.0   pri= 80  [text-analysis]
  ...
```

### Dry-run a query (see plan without executing)

```bash
python3 orchestrator.py "analyze this PDF and make a report" \
    --input paper.pdf --dry-run --json
```

### Full end-to-end pipeline

```bash
python3 orchestrator.py "проанализируй PDF" \
    --input book.pdf --strategy report -v
```

### With LLM-enhanced analysis

```bash
python3 orchestrator.py "analyze this PDF deeply" \
    --input paper.pdf --llm --strategy report
```

## Manifest contract

The Orchestrator expects every skill to have a `manifest.json` with at minimum:

```json
{
  "name": "skill-name",
  "version": "1.0.0",
  "category": "text-analysis",
  "priority": 80,
  "cost": {"cpu": "low", "time": "1s", "llm_calls": 0},
  "triggers": {
    "file_extensions": [".pdf"],
    "content_contains": ["analyze"]
  },
  "inputs":  {"schema": {...}},
  "outputs": {"schema": {...}},
  "requires": {"python": ">=3.10"},
  "dependencies": [],
  "entry_points": {"ingest": "scripts/main.py"},
  "validator": {"script": "validator.py", "min_confidence": 0.3},
  "self_test": {"doctor_script": "doctor.py"}
}
```

Skills WITHOUT a manifest.json are silently skipped by the registry.

## Confidence propagation

Each skill returns `confidence ∈ [0, 1]`. The orchestrator:

1. **Per-skill**: passes the output through `validator.py`. If confidence < `min_confidence` (default 0.3), warns but continues.
2. **Pipeline-level**: overall confidence = `min(all skill confidences)` — weakest-link principle.
3. **Fallback** (planned): if a skill returns `confidence < 0.6`, the Planner can try an alternative skill from the same category.

## Memory hooks (planned)

Each skill's manifest declares `memory.save` and `memory.load` fields. The Orchestrator will (in a future version):
- Cache OCR results by file hash (skip re-OCR on re-analysis)
- Cache ingest results by content hash
- Pass `last_result` as `previous_context` to the next skill

## Self-test

```bash
python3 _orchestrator/scripts/doctor.py
```

Checks:
- All 4 orchestrator scripts present and importable
- SkillRegistry can load all sibling manifests without errors
- Planner can build a DAG for a sample query
- Executor can run `poler-toolkit` smoke test
- Aggregator can produce a report from sample results

## See also

- `poler-toolkit` — the main analysis skill (highest priority, most triggers)
- `pdf-ocr` — depends on; produces text that poler-toolkit analyzes
- `charts`, `docx`, `pdf`, `pptx`, `xlsx` — output producers
- `/home/z/my-project/analyses/` — three external audits that motivated this design
- `/home/z/my-project/skills/ARCHITECTURE.md` — top-level architecture overview

## Author

Task 9 (manifest-based architecture), 2026-07-03. Implemented after studying the GPT, Gemini, and DeepSeek audits of the skills ecosystem.
