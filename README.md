# Super-Z Skill Orchestrator

A self-regulating AI assistant that **runs skills automatically** based on your message — you don't have to choose which tool to use. The watcher layer detects signals (URLs, keywords, file types, intents) and dispatches matching skills in the background. Their outputs become pre-gathered context for the LLM, which then decides strategy and produces the final answer.

> "без воли" — skills fire without the agent's active decision. Data is fed to the model proactively. The agent decides strategy based on experience, not on what to gather.

> **v2.0**: Super-Z is now an **operating system for LLM tools** — capability-driven routing, persistent knowledge graph, runtime learning. See [LLM_INTEGRATION_SPEC.md](LLM_INTEGRATION_SPEC.md) for the stable LLM-facing API.

## What's inside

- **72 skills** registered, **all 72 executable** (100% coverage)
- **31 watcher signal patterns** mapped to skills (Russian + English)
- **Capability Registry** (v2.0) — `capability → [providers]` instead of `skill → manifest`
- **Knowledge Graph** (v2.0) — persistent entities + relations + timeline across sessions (SQLite)
- **Runtime Learning** (v2.0) — EMA weights per (skill, capability); planner improves with use
- **Standardized SkillOutput schema** (v2.0) — `{status, confidence, summary, entities, relations, sources, ...}`
- **Pattern 3 adaptive router** classifies queries as `simple_fact` / `synthesis` / `creative` / `undefined`
- **Pattern 1 source-grounded briefs** — every skill output cites its source
- **Pattern 2 gap-detector** — citation-or-decline Reasoner (identifies missing knowledge)
- **One-command installer** for both Linux/macOS and Windows

## Quick start

Pick the installer for your OS. Both do the same thing: check Python → check z-ai CLI → create venv → install deps → register 72 skills → install `super-z` CLI to PATH.

### 🐧 Linux / macOS

```bash
git clone https://github.com/Kotokvit/super-z-skills.git
cd super-z-skills
./install/linux.sh
```

**Flags:**
```bash
./install/linux.sh --quick        # skip optional deps (Playwright etc.)
./install/linux.sh --uninstall    # remove super-z CLI symlink
./install/linux.sh --help         # show help
```

**Backwards-compatible shortcut** (still works for v1.0.x users):
```bash
./bootstrap.sh    # delegates to install/linux.sh
```

### 🪟 Windows

**Option A — CMD (recommended, handles ExecutionPolicy automatically):**
```cmd
git clone https://github.com/Kotokvit/super-z-skills.git
cd super-z-skills
install\windows.bat
```

**Option B — PowerShell directly:**
```powershell
git clone https://github.com/Kotokvit/super-z-skills.git
cd super-z-skills
powershell -ExecutionPolicy Bypass -File install\windows.ps1
```

**Option C — PowerShell 7+ (`pwsh`):**
```powershell
pwsh -ExecutionPolicy Bypass -File install\windows.ps1
```

> 💡 `windows.bat` automatically tries `pwsh` first, then falls back to `powershell`. If you run `.ps1` directly and get "running scripts is disabled", use `windows.bat` instead — it bypasses the policy for the current session.

### ✅ Verify installation (both platforms)

```bash
super-z --skills       # should list 72 skills
super-z --signals      # should list 31 watcher patterns
super-z --help         # full CLI help
```

### First run

```bash
super-z "напиши пост про ИИ в медицине"
super-z "посмотри это https://youtube.com/watch?v=..."
super-z "что подарить маме на 60 лет"
super-z "сделай контент-план на месяц для SaaS"
```

### What the installers do

| Step | linux.sh | windows.ps1 |
|------|----------|-------------|
| 1. Check Python 3.10+ | ✓ | ✓ |
| 2. Check z-ai CLI | ✓ | ✓ |
| 3. Create `.venv` | ✓ | ✓ |
| 4. `pip install -r requirements.txt` | ✓ | ✓ |
| 5. Register 72 skills in registry | ✓ | ✓ |
| 6. Install `super-z` to PATH (`~/.local/bin` / `%USERPROFILE%\.local\bin`) | ✓ | ✓ |
| 7. Smoke test (optional) | `--verify` flag | `-Verify` flag |

## How it works

### v1.x pipeline (still works)

```
User message
    ↓
[Watcher] scans for signals (URLs, keywords, intents)
    ↓
[Executor] runs matching skills in background threads
    ↓
[Pattern 1 briefs] accumulate in .context/context_brief.json
    ↓
[LLM] reads briefs BEFORE composing reply
    ↓
[Pattern 3 router] decides: simple_fact / synthesis / creative / undefined
    ↓
Final answer grounded in pre-gathered data
```

### v2.0 architecture (new)

```
User message
    ↓
[Watcher] detects signals → dispatches skills in parallel
    ↓
[Executor] runs skills, normalizes output to SkillOutput schema
    ↓                                          ↓
    ↓                            [Runtime Learning] updates EMA weights
    ↓
[Context Builder] merges outputs, queries Memory Graph, detects contradictions
    ↓
[context_brief.json] — single LLM-facing document
    ↓
[LLM] reads brief, can call:
    ├── super-z --memory <topic>          (recall prior knowledge)
    ├── super-z --ask-capability <cap>    (pick best provider)
    ├── super-z --run <skill> "query"     (run specific skill)
    └── super-z "natural language"        (full auto pipeline)
    ↓
Final answer grounded in pre-gathered data + persistent memory
```

### Key v2.0 concepts

| Concept | What it means | Where it lives |
|---------|--------------|----------------|
| **Capability** | A verb: `extract_text`, `summarize`, `transcribe`. Multiple skills can provide the same capability. | `manifest.json` → `capabilities[]` |
| **Provider** | A skill that offers a capability. Ranked by confidence + runtime weight. | `capability_registry.py` |
| **SkillOutput** | Standardized envelope every skill returns. `{status, confidence, summary, entities, relations, sources, ...}` | `skill_schema.py` |
| **Memory Graph** | SQLite store of entities + relations + timeline. Persists across sessions. | `.context/memory_graph.db` |
| **Runtime Weight** | EMA of success score per (skill, capability). Picks best provider. | `.context/runtime_learning.db` |
| **Context Brief** | The single document the LLM reads before replying. | `.context/context_brief.json` |

### LLM Integration Spec

For the **stable API surface** that any LLM (Claude, GPT, Gemini, local models) can rely on, see [**LLM_INTEGRATION_SPEC.md**](LLM_INTEGRATION_SPEC.md). It defines:

- The SkillOutput schema (TypeScript-style)
- The Context Brief format (with example)
- CLI commands (`--run`, `--ask-capability`, `--memory`, `--brief`, etc.)
- Pattern 1 (source-grounding), Pattern 2 (gap detection), Pattern 3 (adaptive routing)
- Stability promise: v2.x → v2.y is backward-compatible; v2.x → v3.0 will be announced

## CLI commands

```bash
super-z "your request"           # one-shot: watcher + LLM with context
super-z --watch                  # interactive stdin loop
super-z --brief                  # show current context_brief.json
super-z --skills                 # list all registered skills
super-z --signals                # list watcher signal patterns
super-z --run <skill> "query"    # run a specific skill directly
super-z --help                   # full help
```

## Repository structure

```
super-z-skills/
├── README.md                    # this file
├── LICENSE                      # MIT
├── CHANGELOG.md                 # version history
├── bootstrap.sh                 # legacy shortcut → install/linux.sh
├── requirements.txt             # Python deps (pymorphy3, yt-dlp, bs4, etc.)
├── setup.py                     # installable package metadata
│
├── install/                     # platform installers (NEW in v1.3.0)
│   ├── linux.sh                 # Linux/macOS installer (bash)
│   ├── windows.ps1              # Windows installer (PowerShell)
│   └── windows.bat              # Windows launcher (handles ExecutionPolicy)
│
├── bin/
│   └── super-z                  # CLI entry point (bash, Linux/macOS)
│
├── scripts/                     # setup utilities
│   ├── register_remaining_skills.py
│   ├── create_missing_wrappers.py
│   ├── create_wrappers_v2.py
│   ├── create_wrappers_v3.py
│   ├── fix_manifests_for_wrappers.py
│   ├── debug_classifier.py
│   ├── test_pattern3_routing.py
│   ├── test_final.py
│   ├── package_super_z.py
│   └── ask_llm.py
│
└── skills/                      # 72 skills + orchestrator
    ├── _orchestrator/
    │   └── scripts/
    │       ├── orchestrator.py  # explicit pipeline: DAG → execute → report
    │       ├── watcher.py       # passive: detect signals → dispatch skills
    │       ├── planner.py       # Pattern 3 adaptive router
    │       ├── registry.py      # manifest.json loader
    │       ├── executor.py      # subprocess runner + validator
    │       └── aggregator.py    # merge skill outputs
    ├── _shared/
    │   └── llm_wrapper.py       # universal LLM wrapper for docs_only skills
    ├── blog-writer/             # executable
    ├── resume-builder/          # executable
    ├── LLM/                     # executable (z-ai chat dispatch)
    ├── TTS/                     # executable (z-ai tts dispatch)
    ├── ASR/                     # executable (z-ai asr dispatch)
    ├── VLM/                     # executable (z-ai vision dispatch)
    ├── media-triage/            # executable (yt-dlp + ASR)
    ├── site-context-loader/     # executable (pymorphy3 + Nominatim)
    ├── gap-detector/            # executable (Pattern 2 Reasoner)
    └── ... (63 more, all executable)
```

## Skill types

| Type | Count | Description |
|------|-------|-------------|
| **Native executable** | 6 | Has bespoke Python script (media-triage, site-context-loader, gap-detector, blog-writer, etc.) |
| **Z-AI CLI dispatch** | 11 | Wraps `z-ai chat/tts/asr/vision/image/...` directly (LLM, TTS, ASR, VLM, image-*) |
| **LLM wrapper** | 55 | Uses `_shared/llm_wrapper.py` — reads SKILL.md as system prompt, calls LLM, returns Pattern 1 brief |
| **Total** | **72** | 100% executable coverage |

To turn a docs_only skill into executable, add `scripts/run.py`:

```python
#!/usr/bin/env python3
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_shared"))
from llm_wrapper import run_skill
if __name__ == "__main__":
    query = sys.argv[1] if len(sys.argv) > 1 else None
    result = run_skill("YOUR-SKILL-NAME", user_query=query)
    sys.exit(0 if result.get("status") == "success" else 1)
```

## Watcher signal patterns

31 patterns including:

- **Media URLs** → `media-triage` (YouTube, SoundCloud, Vimeo, direct media)
- **Attachments** → `doc-triage` (PDF, DOCX), `image-understand` (PNG/JPG)
- **Geography** → `site-context-loader` (toponyms, geocoords)
- **Content creation** → `blog-writer`, `seo-content-writer`, `resume-builder`, `interview-prep`, `quiz-mastery`, `storyboard-manager`, `dream-interpreter`
- **Analysis** → `contentanalysis`, `cheat-sheet`, `finance`, `market-research-reports`
- **Design** → `ui-ux-pro-max`, `design`
- **Code** → `coding-agent`
- **Web** → `agent-browser`
- **Safety** → `anti-pua`
- **Strategy** → `content-strategy`, `marketing-mode`
- **Gifts** → `gift-evaluator`

## Requirements

- **Python 3.10+** (both platforms)
- **z-ai CLI** — install via `npm install -g z-ai-web-dev-sdk` (both platforms)
- **Node.js 18+** — required for z-ai CLI
- Linux, macOS, or Windows 10/11

## Adding a new skill

1. Create `skills/your-skill/SKILL.md` with methodology
2. Create `skills/your-skill/manifest.json` (copy from existing skill)
3. Add `skills/your-skill/scripts/run.py` using the wrapper template above
4. Add a trigger pattern in `skills/_orchestrator/scripts/watcher.py` → `SIGNAL_PATTERNS` + `SIGNAL_TO_SKILL`
5. Run `python3 scripts/register_remaining_skills.py` to refresh registry

## Changelog

- **v2.0.0** — Capability-driven architecture: capability_registry, memory_graph, context_builder, runtime_learning, skill_schema, LLM_INTEGRATION_SPEC.md
- **v1.3.0** — Unified Linux + Windows installer structure (`install/` directory), 100% executable coverage, GPL v3 license
- **v1.2.0** — 100% executable coverage (72/72 skills)
- **v1.1.0** — Fixed media-triage + site-context-loader bugs, +30 executable wrappers
- **v1.0.0** — Initial release: 23 executable skills, one-command installer

## License

**GNU GPL v3** — see [LICENSE](LICENSE).

Why GPL v3 (not MIT):
- ✅ Anyone can use, study, modify, distribute — including commercially
- ✅ **Copyleft**: anyone who distributes modified versions must publish their changes under the same GPL v3
- ✅ Protects from "take, close, sell as proprietary" scenarios
- ✅ Allows dual-licensing: you can sell a proprietary license to companies that want to embed super-z in closed-source products
- ✅ Compatible with z-ai-web-dev-sdk (which is MIT-licensed)

If you want to use super-z in a closed-source commercial product, contact the author for a separate commercial license.

Donations / sponsorships welcome — see the Sponsor button on GitHub.

## Acknowledgements

### Roles

| Role | Who | Contribution |
|---|---|---|
| **Architect & copyright holder** | Vitalij Kotok | System design, requirements, architectural decisions, license choice, all final calls |
| **Implementation assistant** | Super-Z (GLM by Z.ai) | Code generation, refactoring, testing, documentation — under direction of the architect |
| **Technical reviewer** | GPT (OpenAI) | Independent 10-point architecture review incorporated into v2.0 |
| **Foundation SDK** | z-ai-web-dev-sdk team | CLI tooling that powers most media/analysis skills |

### History

- **v1.0–1.2 (2026)** — Architect defined the goal: a self-regulating orchestrator where skills fire "без воли" (without the agent's active decision). Implementation assistant wrote 72 skill wrappers and the first watcher.
- **v1.3 (2026-07-04)** — Architect requested unified Linux+Windows installer structure, GPL v3 license, single archive distribution.
- **v2.0 (2026-07-04)** — Architect brought in GPT's 10-point architecture review. Implementation assistant incorporated: capability registry, memory graph, runtime learning, context builder, SkillOutput schema, LLM Integration Spec.
- **v2.0.1 (2026-07-04)** — Architect identified that watcher could run as middleware on the same Linux host as the LLM itself. Implementation assistant built `watcher_daemon.py` (background process polling `.context/inbox/`) and `--self-context` hook. The "без воли" pattern became operational end-to-end.

### Contact

- **Author**: Vitalij Kotok
- **Email**: [vitalijkotok18@gmail.com](mailto:vitalijkotok18@gmail.com)
- **GitHub**: [github.com/Kotokvit/super-z-skills](https://github.com/Kotokvit/super-z-skills)
- **Phone**: +380 93 798 1708

For commercial licensing inquiries (closed-source embedding, dual-licensing), please contact the author directly via email.

## Author

**Vitalij Kotok** — [vitalijkotok18@gmail.com](mailto:vitalijkotok18@gmail.com)

Copyright (C) 2026 Vitalij Kotok. Released under GNU GPL v3.

Implementation assistance: Super-Z (GLM by Z.ai).
