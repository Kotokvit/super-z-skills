# Changelog

## 3.0.0 - 2026-07-21
- Replaced the broken packaging setup with a proper pyproject-based installable package.
- Added a Python CLI entry point via the super-z console script and module entry point.
- Introduced a configurable skills-directory resolution flow so the tool works from the repository checkout and installed environments.
- Added a lightweight LLM backend abstraction with a mock backend fallback so docs-only skills can still run without z-ai installed.
- Added regression coverage for the new CLI entry points.

All notable changes to this project will be documented in this file.

## [v2.0.2] — 2026-07-04 — Cross-platform Python CLI + CI/CD

Addresses GPT review points #2 (Windows native CLI) and #3 (no CI/CD).

### Added
- **`bin/super-z.py`** (290 lines) — cross-platform Python entry point. Same feature parity as `bin/super-z` (bash): one-shot, --brief, --skills, --signals, --watch, --run, --enqueue, --self-context, --daemon. Works on Linux, macOS, and Windows without any shell dependency. Uses `argparse` for proper arg parsing.
- **`.github/workflows/tests.yml`** — GitHub Actions CI/CD. Runs on every push and PR. Tests on `ubuntu-latest` and `windows-latest` with Python 3.10, 3.11, 3.12. Checks: Python syntax (py_compile), module imports (9 core modules), existing test_*.py files, and `super-z.py --help`.
- **`install/linux.sh`** — new `SUPER_Z_TARGET_DIR` env var for optional skills deployment. If set, copies skills/ to that directory (e.g. `~/.local/share/super-z/skills`). Symlink now points to `super-z.py` (cross-platform) instead of bash version.
- **`install/windows.ps1`** — `.cmd` wrapper now calls `bin/super-z.py` directly. Previously it called `orchestrator.py` (which lacked watcher/brief/daemon support). Now Windows users have full feature parity with Linux.

### Changed
- Linux install symlink: `super-z → super-z.py` (was: `super-z → super-z` bash script)
- Windows `.cmd` wrapper: now invokes `super-z.py` (was: `orchestrator.py` directly)

### Why
GPT review of v2.0.1 noted that Windows users had a degraded CLI experience (only `orchestrator.py` was called, missing watcher/daemon/brief commands). v2.0.2 fixes this by making `super-z.py` the canonical cross-platform entry point, with both bash (Linux/macOS) and `.cmd` (Windows) wrappers delegating to it.

---

## [v2.0.1] — 2026-07-04 — Watcher daemon + self-trigger ("без воли" pattern operational)

This patch makes the "without will" pattern actually work end-to-end. Skills now fire automatically whenever any message lands in `.context/inbox/` — no agent discipline required.

### Added
- **`skills/_orchestrator/scripts/watcher_daemon.py`** (320 lines) — long-running Linux daemon that polls `.context/inbox/` every 0.5s, feeds each message through `ConversationWatcher`, and moves processed files to `inbox/processed/`. Properly daemonizes via `os.fork()` + `os.setsid()`, writes PID + status files, heartbeat every 30s, log file at `.context/daemon.log`. CLI: `--start | --stop | --restart | --status | --foreground`.
- **`scripts/self_context.sh`** — agent self-trigger wrapper. Auto-starts daemon if down, then enqueues the message and waits up to 12s for the brief to be updated. This is the single hook an LLM needs to call at the start of every reply.
- **`bin/super-z`** three new commands:
  - `--enqueue "msg"` — async drop into inbox, return immediately
  - `--self-context "msg"` — enqueue + wait for brief + print new entries as compact text
  - `--daemon start|stop|restart|status|foreground` — manage the daemon

### How "без воли" works now
1. Any process (LLM agent, gateway, cron, web UI) writes a JSON file to `.context/inbox/`:
   ```json
   {"id": "msg-...", "ts": 1759650600.0, "message": "...", "wait_for_brief": true}
   ```
2. Daemon picks it up within 0.5s, detects signals, dispatches matching skills in parallel.
3. Skills write back to `.context/context_brief.json` (Pattern 1 briefs with entities/sources/claims).
4. Agent reads brief before answering — strategy decision, not data-gathering.
5. Processed file moved to `inbox/processed/`, failed to `inbox/failed/`.

### Verified end-to-end
- YouTube URL signal → `media-triage` auto-fired (confidence 0.95, transcript 1805 chars)
- "что подарить маме" → `gift-evaluator` auto-fired
- "напиши пост про ИИ" → `blog-writer` auto-fired (async via `--enqueue`)
- All test messages correctly routed inbox/ → inbox/processed/

---

## [v2.0.0] — 2026-07-04 — Architecture upgrade: capability-driven + knowledge graph

This is a major architectural upgrade based on the GPT technical review. Super-Z stops being "a collection of 72 skills" and becomes "an operating system for LLM tools": capabilities, memory graph, runtime learning, and a stable LLM-facing API.

### Added — Core infrastructure
- **`LLM_INTEGRATION_SPEC.md`** — single source of truth for what Super-Z exposes to any LLM (Claude, GPT, Gemini, local models, agents). Defines SkillOutput schema, Context Brief format, CLI surface, capability registry semantics, and stability promise.
- **`skills/_shared/skill_schema.py`** — standardized output envelope (`SkillOutput`, `Entity`, `Relation`, `Source`, `Artifact`). Every skill output now follows `{status, confidence, summary, entities, relations, sources, artifacts, warnings, metrics}`. Includes `from_dict()` tolerant constructor and `validate_output()` for executor use.
- **`skills/_orchestrator/scripts/capability_registry.py`** — shifts the orchestrator from "pick a skill" to "pick a capability". Maps `capability → [providers]` and `skill → [capabilities]`. Skills declare capabilities in `manifest.json`; the registry auto-indexes them. Picks best provider by confidence.
- **`skills/_orchestrator/scripts/memory_graph.py`** — persistent Knowledge Graph on SQLite (`.context/memory_graph.db`). Tables: `entities`, `relations`, `timeline`, `facts`. Skills auto-ingest entities/relations; LLM queries via `super-z --memory <topic>`. Survives across sessions.
- **`skills/_orchestrator/scripts/context_builder.py`** — merges multiple skill outputs into a unified `context_brief.json` (Pattern 1 brief). Deduplicates entities/relations by name+type, merges sources, queries memory graph for related context, detects contradictions when skills disagree on entity properties.
- **`skills/_orchestrator/scripts/runtime_learning.py`** — logs every skill invocation to `.context/runtime_learning.db`. Maintains EMA weight (α=0.2) per `(skill, capability)` pair. Planner uses `0.6 * runtime_weight + 0.4 * manifest_confidence` to pick best provider. "Through a month of usage, the planner becomes much better."

### Changed
- **`executor.py`** — every skill output is now normalized to `SkillOutput` schema; invocations auto-logged to runtime_learning; degrades gracefully if schema/tracker unavailable.
- **`planner.py`** — adds two new entry points: `plan_by_capability(cap, input_type, query)` and `capabilities_for_query(query)`. Legacy `plan()` unchanged for backward compatibility.
- **`README.md`** — added v2.0 architecture diagram, LLM Integration Spec reference, and explanation of capability-driven routing.

### Architecture shift
- **Before v2.0**: orchestrator picks a skill by name from watcher signals.
- **After v2.0**: orchestrator picks a capability (verb), then picks the best provider of that capability based on historical performance.
- Multiple skills can provide the same capability (`extract_text` ← `pdf-ocr`, `vlm`, `tesseract`). The best one wins per user's environment.

### Skill output contract
Every skill must return a dict (or JSON) with at minimum:
```json
{
  "status": "ok|partial|error|skipped",
  "confidence": 0.0-1.0,
  "summary": "1-3 sentence LLM-facing summary"
}
```
Optional but recommended: `entities`, `relations`, `sources`, `artifacts`, `warnings`, `metrics`.

### Backward compatibility
- All v1.x skills continue to work — `SkillOutput.from_dict()` is tolerant of missing fields.
- All v1.x CLI commands continue to work unchanged.
- The v1.x `context_brief.json` format is preserved; v2.0 fields are additive.

---

## [v1.3.0] — 2026-07-04 — Unified Linux + Windows, GPL v3

### Added
- `install/` directory with platform-specific installers:
  - `linux.sh` — bash installer for Linux/macOS
  - `windows.ps1` — PowerShell installer for Windows
  - `windows.bat` — launcher that handles ExecutionPolicy automatically
- `bootstrap.sh` retained in root as backwards-compatible entry point
- Dual-platform instructions in README

### Changed
- Repository restructured: ONE branch (`main`) supports both Linux and Windows
- No more separate Windows branch — both installers live in `install/`
- Package archive now creates both `.tar.gz` and `.zip` from the same source
- **License changed from MIT to GNU GPL v3** — protects against proprietary take-and-close scenarios, enables future dual-licensing (open GPL + paid proprietary for commercial embedding)

### Fixed
- 100% executable coverage: all 72 skills now have `scripts/run.py`
- `media-triage` — added missing `run.py` (delegates to `media_triage.py`)
- `site-context-loader` — added missing `run.py` (delegates to `site_context_loader.py`)
- `video-generation` — added `run.py` (z-ai video dispatch)
- `executor.py` — fixed validator unpack crash (returns "skipped" instead of error)

## [v1.2.0] — 2026-07-04 — 100% Coverage

### Added
- 16 new wrappers for remaining docs_only skills:
  - `gap-detector` (delegates to native `gap_detector.py`)
  - `aminer-academic-search`, `aminer-daily-paper`, `aminer-free-academic`
  - `gaokao-collect-student-info`, `gaokao-fetch-volunteers`, `gaokao-generate-report`, `gaokao-recommend-majors`, `gaokao-recommend-schools`
  - `ai-news-collectors`, `qingyan-research`, `poler-psi`, `poler-toolkit`, `web-shader-extractor`, `skill-finder-cn`, `fullstack-dev`

### Changed
- Executable skills: 53 → 72 (100% coverage)

## [v1.1.0] — 2026-07-04 — Bug fixes + 30 wrappers

### Added
- 30 new wrappers via `create_wrappers_v2.py`:
  - Z-AI CLI dispatch (11): `LLM`, `TTS`, `ASR`, `VLM`, `image-generation`, `image-edit`, `image-search`, `web-search`, `web-reader`, `image-understand`, `video-understand`
  - LLM wrapper (19): `pdf`, `docx`, `xlsx`, `pptx`, `charts`, `multi-search-engine`, `pdf-ocr`, `podcast-generate`, `writing-plans`, `skill-creator`, `task-review`, `interview-designer`, `get-fortune-analysis`, `mindfulness-meditation`, `visual-design-foundations`, `version-management`, `stock-analysis-skill`, `auto-target-tracker`, `job-intent-tracker`

### Fixed
- `executor.py` — added `(ValueError, TypeError)` handler around `ok, msg = module.validate(output)` so non-tuple return becomes "skipped" instead of crash
- `media-triage` — verified working (yt-dlp multi-strategy anti-bot bypass)
- `site-context-loader` — verified working (pymorphy3 lemmatization)

### Changed
- Executable skills: 23 → 53

## [v1.0.0] — 2026-07-04 — Initial release

### Added
- 23 executable skills with `scripts/run.py` wrappers
- `_shared/llm_wrapper.py` — universal LLM wrapper for docs_only skills
- `_orchestrator/` — orchestrator, watcher, planner, registry, executor, aggregator
- 31 watcher signal patterns (RU + EN)
- Pattern 1 source-grounded briefs
- Pattern 2 gap-detector (citation-or-decline Reasoner)
- Pattern 3 adaptive router (simple_fact / synthesis / creative / undefined)
- Pattern 5 transient memory
- `bootstrap.sh` one-command installer
- `super-z` CLI with subcommands: `--skills`, `--signals`, `--brief`, `--watch`, `--run`, `--help`
- `requirements.txt` with 16 Python dependencies
- `setup.py` as installable package
- README, LICENSE (GPL v3), .gitignore
