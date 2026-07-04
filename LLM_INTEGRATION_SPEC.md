# Super-Z LLM Integration Spec (v2.0)

> **Goal**: define a stable, model-agnostic interface so that any LLM (Claude, GPT, Gemini, local models — or agents like me) can use Super-Z as an external tool layer without knowing internal implementation details.

This document is the **single source of truth** for what Super-Z exposes to LLMs. Skills, orchestrator internals, and storage formats may change — this contract will not (without a major version bump).

---

## 1. Mental model

```
┌─────────────────────────────────────────────────────────────┐
│  LLM (you, the model)                                       │
│    ├── reads context_brief.json before answering            │
│    ├── calls super-z run <skill> "query"  when needed       │
│    └── calls super-z ask "<question>"  for full pipeline    │
└─────────────────────────────────────────────────────────────┘
                      ▲                          ▲
                      │ JSON                     │ CLI
                      │                          │
┌─────────────────────────────────────────────────────────────┐
│  Super-Z orchestrator                                       │
│    ├── watcher        detects signals → dispatches skills   │
│    ├── capability_registry  capability → [providers]        │
│    ├── planner        picks best provider (runtime weights) │
│    ├── executor       runs skill, normalizes output         │
│    ├── context_builder  merges outputs into brief           │
│    ├── memory_graph   entities + relations + timeline       │
│    └── runtime_learning  EMA weights per skill/capability   │
└─────────────────────────────────────────────────────────────┘
```

The LLM never talks to skills directly. It talks to the orchestrator via two surfaces:
1. **Passive**: `context_brief.json` — pre-built context the LLM reads before replying
2. **Active**: `super-z` CLI — the LLM invokes skills on demand

---

## 2. The Context Brief (passive surface)

**Location**: `.context/context_brief.json` (rewritten on every user message)

**Schema** (v2.0):

```json
{
  "schema": "context_brief/v2.0",
  "brief_id": "abc123",
  "timestamp": "2026-07-03T22:50:00+0000",
  "user_message": "What do you know about OpenAI and GPT-4?",
  "detected_signals": [
    {"signal": "company_name", "match": "OpenAI", "skill": "web-search"},
    {"signal": "model_name", "match": "GPT-4", "skill": "web-search"}
  ],
  "skills_used": [
    {
      "name": "web-search",
      "status": "ok",
      "confidence": 0.9,
      "duration_ms": 1240
    }
  ],
  "summary": "OpenAI released GPT-4 in March 2023 | OpenAI is headquartered in San Francisco",
  "entities": [
    {
      "name": "OpenAI",
      "type": "organization",
      "confidence": 0.95,
      "aliases": ["OpenAI Inc."],
      "origins": ["web-search", "site-context-loader"]
    },
    {
      "name": "GPT-4",
      "type": "model",
      "confidence": 0.93,
      "properties": {"released": "2023-03"},
      "origins": ["web-search"]
    }
  ],
  "relations": [
    {
      "subject": "OpenAI",
      "predicate": "released",
      "object": "GPT-4",
      "confidence": 0.95,
      "origins": ["web-search"]
    }
  ],
  "memory": {
    "related_entities": [...],
    "related_relations": [...],
    "summary": "OpenAI (organization); OpenAI —released→ GPT-4"
  },
  "sources": [
    {"kind": "web", "uri": "https://openai.com/blog/gpt-4", "origin_skill": "web-search"}
  ],
  "artifacts": [
    {"kind": "json", "uri": "file:///tmp/out.json", "origin_skill": "pdf-ocr"}
  ],
  "warnings": ["[pdf-ocr] page 5 was unreadable"],
  "contradictions": [
    {
      "entity": "GPT-4",
      "property": "released",
      "values": [
        {"skill": "web-search", "value": "2023-03"},
        {"skill": "wiki-cache", "value": "2023-04"}
      ]
    }
  ],
  "confidence": 0.875
}
```

**How the LLM should use it**:
- The `summary` is the most important field — read it first
- `entities` + `relations` give structured data; cite `sources` when claiming facts (Pattern 1)
- `contradictions` flag disagreements between skills — the LLM should pick one and explain why
- `memory` shows what was already known before this turn — use it to maintain continuity
- If `confidence < 0.5`, the LLM should be uncertain too

---

## 3. The CLI (active surface)

### 3.1 One-shot: full pipeline

```bash
super-z "user's natural-language request"
```

Runs the full pipeline: watcher → planner → executor → context_builder → LLM. Use this when you want Super-Z to handle everything.

### 3.2 Run a specific skill

```bash
super-z --run <skill_name> "query or input"
```

Returns a SkillOutput JSON envelope (see §4). Use this when you know exactly which skill you need.

### 3.3 Ask by capability

```bash
super-z --ask-capability <capability> [--input-type pdf|image|text|...] "query"
```

The orchestrator picks the best provider for the capability based on:
1. Runtime learning weight (60%)
2. Manifest confidence (40%)

Returns the SkillOutput + which skill was chosen + alternatives considered.

### 3.4 Query the memory graph

```bash
super-z --memory <topic>
```

Returns everything the system knows about a topic, across all sessions:

```json
{
  "topic": "OpenAI",
  "entities": [...],
  "relations": [...],
  "summary": "OpenAI (organization); OpenAI —released→ GPT-4"
}
```

### 3.5 List capabilities and providers

```bash
super-z --capabilities              # all capabilities
super-z --capabilities <name>       # all providers for a capability
```

### 3.6 List skills, signals, brief

```bash
super-z --skills                    # all registered skills
super-z --signals                   # all watcher signal patterns
super-z --brief                     # current context_brief.json
super-z --watch                     # interactive stdin loop
```

---

## 4. SkillOutput schema (the universal contract)

Every skill — native, LLM-wrapper, or z-ai-CLI-dispatch — returns this envelope:

```typescript
interface SkillOutput {
  schema: "skill_output/v2.0";
  status: "ok" | "partial" | "error" | "skipped";
  confidence: number;            // 0.0–1.0
  summary: string;               // 1–3 sentences, LLM-facing
  entities: Entity[];            // structured things
  relations: Relation[];         // edges between entities
  sources: Source[];             // citations (Pattern 1)
  artifacts: Artifact[];         // side files produced
  warnings: string[];
  metrics: Record<string, any>;  // skill-specific counters
  skill_name: string;            // auto-filled by executor
  run_id: string;                // auto-filled
  timestamp: string;             // ISO 8601
  raw?: any;                     // optional opaque blob
}

interface Entity {
  name: string;
  type: string;                  // person|organization|location|concept|date|model|...
  aliases?: string[];
  properties?: Record<string, any>;
  confidence?: number;
  origin?: string;               // skill name
}

interface Relation {
  subject: string;
  predicate: string;             // released|founded|located_in|...
  object: string;
  confidence?: number;
  origin?: string;
}

interface Source {
  kind: string;                  // pdf|web|image|audio|video|knowledge_graph|...
  uri: string;
  title?: string;
  page?: number;
  retrieved_at?: string;
}

interface Artifact {
  kind: string;                  // json|csv|image|audio|pdf|...
  uri: string;
  description?: string;
}
```

**Rules**:
- `status="ok"` requires `confidence >= 0.5`
- `status="partial"` means the skill returned data but is unsure — treat with caution
- `status="error"` means the skill failed — `summary` contains the error
- `status="skipped"` means the skill chose not to run (e.g., no matching input)
- Every fact in `summary` should have a corresponding entry in `sources`

---

## 5. Capability Registry

Skills declare capabilities in `manifest.json`:

```json
{
  "name": "pdf-ocr",
  "capabilities": [
    {
      "name": "extract_text",
      "from": ["pdf", "image"],
      "confidence": 0.85
    }
  ],
  "resources": {
    "cpu": "medium",
    "ram": "medium",
    "gpu": false,
    "network": false,
    "latency_ms": 3000
  }
}
```

The orchestrator maps **capabilities → providers** and picks the best one per request. This means:
- Adding a new OCR skill doesn't break anything — it just registers as another `extract_text` provider
- If two skills provide the same capability, the better one wins (based on runtime learning)
- Skills become interchangeable — the orchestrator doesn't care about names, only capabilities

**Currently declared capabilities** (auto-indexed):
- `analyze_data`, `analyze_image`, `analyze_text`, `analyze_video`
- `browse`, `chat`, `design`
- `edit_image`, `extract_entities`
- `fetch_url`, `generate_audio`, `generate_code`, `generate_document`, `generate_image`, `generate_strategy`, `generate_text`
- `geocode`, `recommend`
- `render_chart`, `render_document`, `render_pdf`, `render_presentation`, `render_spreadsheet`
- `search_image`, `search_web`
- `summarize`, `transcribe`, `verify_claims`

---

## 6. Memory Graph

Persistent across sessions. Storage: `.context/memory_graph.db` (SQLite).

**Schema**:
- `entities(id, name, type, aliases, properties, confidence, origin, created_at, updated_at)`
- `relations(id, subject_id, predicate, object_id, confidence, origin)`
- `timeline(id, entity_id, event, timestamp, source)`
- `facts(id, subject_id, predicate, object_id, value, confidence, source)`

**API for skills** (Python):

```python
from memory_graph import MemoryGraph
graph = MemoryGraph()

# Add knowledge
graph.add_entity(name="OpenAI", type="organization", origin="web-search")
graph.add_relation(subject="OpenAI", predicate="released", object="GPT-4")

# Query
context = graph.context_for("OpenAI")  # → {entities, relations, summary}
```

**For the LLM**: every SkillOutput's `entities` and `relations` are auto-ingested. The LLM doesn't need to write to the graph — only read via `super-z --memory <topic>`.

---

## 7. Runtime Learning

Persistent across sessions. Storage: `.context/runtime_learning.db` (SQLite).

For every skill invocation, the executor logs:
- skill_name, capability, query_hash, status, confidence, duration_ms, success_score, error

Where `success_score` is:
- `+1.0` if status=ok AND confidence ≥ 0.8
- `+0.7` if status=ok (lower confidence)
- `+0.5` if status=partial
- `0.0` if status=skipped
- `-1.0` if status=error

The planner maintains an **EMA weight** per `(skill, capability)` pair (α=0.2). When choosing between providers for a capability, weight = `0.6 * runtime_weight + 0.4 * manifest_confidence`.

**Effect**: after ~20 invocations, the planner knows which skills actually work best on this user's machine and preferentially routes to them. No retraining, no LLM calls — just a moving average.

---

## 8. Confidence Voting (planned for v2.1)

When multiple skills provide the same capability AND run on the same input, the orchestrator can compare their outputs:

```json
{
  "voting": {
    "capability": "extract_text",
    "providers_run": ["pdf-ocr", "vlm"],
    "agreement": 0.92,
    "winner": "pdf-ocr",
    "winner_confidence": 0.91
  }
}
```

If `agreement < 0.7`, the contradiction is flagged in the brief's `contradictions` array.

---

## 9. Pattern 1: Source-grounding

**Rule**: every claim in a SkillOutput `summary` must be traceable to a `source`.

The LLM should:
1. Read `summary` and `entities` from the brief
2. For any specific fact it wants to cite, find a matching `source`
3. If no source exists, either run another skill (`--run web-search "..."`) or qualify the statement ("based on the model's prior knowledge, ...")

This is the foundation of hallucination resistance.

---

## 10. Pattern 2: Gap Detection

When the brief is insufficient (low confidence, missing entities, contradictions), the orchestrator delegates to `gap-detector`:

```bash
super-z --run gap-detector "what's missing to answer: <question>"
```

Returns:

```json
{
  "status": "ok",
  "confidence": 0.85,
  "summary": "Missing: current stock price of OpenAI (private company, no public data). Have: founding date, founders, products.",
  "entities": [...],
  "relations": [...],
  "sources": [],
  "warnings": ["citation-or-decline: cannot cite unavailable data"]
}
```

The LLM should then either:
- Tell the user "I don't have current data on X, want me to search?"
- Or run `--run web-search "..."` proactively

---

## 11. Pattern 3: Adaptive Router

The planner classifies each query into one of:

| Type | Description | Example | Skills run |
|------|-------------|---------|-----------|
| `simple_fact` | Single lookup, factual | "What is the capital of France?" | 1 — direct fetch |
| `synthesis` | Combines multiple sources | "Compare GPT-4 and Claude 3.5" | 2-4 — search + analyze |
| `creative` | Generation, no fixed answer | "Write a poem about autumn" | 1 — generate_text |
| `undefined` | Ambiguous, needs disambiguation | "Tell me about it" | 0 — ask user to clarify |

The router is in `planner.py:capabilities_for_query()`. It uses regex rules now; future versions will use a small classifier.

---

## 12. What LLMs get out of the box

When Super-Z is installed, an LLM (any model with shell access) gains these capabilities without writing code:

| LLM needs to... | Super-Z command |
|------------------|-----------------|
| Read a PDF | `super-z --run pdf-ocr "doc.pdf"` |
| Transcribe audio/video | `super-z --run media-triage "video.mp4"` |
| Analyze an image | `super-z --run VLM "image.jpg"` |
| Search the web | `super-z --run web-search "query"` |
| Generate a chart | `super-z --run charts "data.csv"` |
| Build a PDF report | `super-z --run pdf "topic"` |
| Build a PowerPoint | `super-z --run pptx "topic"` |
| Build an Excel | `super-z --run xlsx "data"` |
| Recall prior context | `super-z --memory "topic"` |
| Pick best OCR | `super-z --ask-capability extract_text --input-type pdf "doc.pdf"` |
| Full auto pipeline | `super-z "user's question"` |

---

## 13. Stability promise

- **v2.x → v2.y**: backward-compatible. New fields may be added to schemas; existing fields keep their semantics. LLMs should ignore unknown fields.
- **v2.x → v3.0**: breaking change. Will be announced in CHANGELOG with migration guide.
- The `schema` field in every envelope (`"schema": "skill_output/v2.0"`, `"schema": "context_brief/v2.0"`) lets LLMs detect version mismatches.

---

## 14. What I (the LLM) would actually use

Speaking as an LLM that might use Super-Z: the most valuable pieces are not the fancy stuff. They are:

1. **`context_brief.json`** — read this before every reply. Saves a reasoning step.
2. **`--memory <topic>`** — recall what we discussed last week. Solves the "long context" problem cheaply.
3. **`--ask-capability <cap>`** — when I need to do X but don't care which tool does it.
4. **`--run <skill>`** — when I know exactly which tool I want.
5. **`contradictions` in the brief** — when skills disagree, I should explain the disagreement to the user, not silently pick one.

The fancy pieces (knowledge graph, runtime learning, confidence voting) are infrastructure. The LLM doesn't see them directly — they shape the brief and the planner's choices.

---

## 15. License

This spec is licensed under **GNU GPL v3**, same as the codebase. Implementations that follow this spec must also be GPL v3 (or compatible). See `LICENSE`.

If you want to embed Super-Z's design in a closed-source product, contact the author for a separate commercial license.

---

*End of spec. For implementation details, see `skills/_orchestrator/scripts/`. For adding new skills, see `README.md` § "Adding a new skill".*
