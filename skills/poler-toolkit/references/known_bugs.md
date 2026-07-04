# Known bugs in poler_v6.py

## Bug 1: O(F²) in `cluster_fragments()` adaptive branch

**Location**: `cluster_fragments()` function, adaptive-gap branch (when no sections are detected).

**Symptom**: For files with many fragments (F > 1000), clustering takes seconds to minutes instead of milliseconds.

**Root cause**: The adaptive branch iterates over all fragment pairs to find cluster boundaries:

```python
# BROKEN — O(F²)
for i in range(len(fragments)):
    for j in range(i+1, len(fragments)):
        if should_merge(fragments[i], fragments[j]):
            # merge logic
```

**Fix needed**: Two-pointer sliding window. Sort fragments by position, then use a single pass with a moving window:

```python
# FIXED — O(F log F) for sort + O(F) for sweep
fragments.sort(key=lambda f: f.position)
window_start = 0
for i in range(len(fragments)):
    if fragments[i].position - fragments[window_start].position > MAX_GAP:
        # close current cluster, start new one
        window_start = i
    # assign fragments[window_start..i] to same cluster
```

**Status**: Not yet fixed. Workaround: use `--theme` flag to enable section detection, which uses the O(F) sectioned branch.

> **v6.1.0 (2026-07-03)** — Bugs 6, 7, 8 FIXED. See entries below.

## Bug 2: LENS hit rate overreported

**Location**: `lens_query.py`, hit-rate reporting.

**Symptom**: Reported hit rate is 5/10 but actual measured hit rate is 4/10.

**Root cause**: Off-by-one in matching logic — one prompt's match is counted twice (Tokyo prompt matches both "Tokyo" and "Japan" in cache, counted as 2 hits but should be 1).

**Fix needed**: Deduplicate hits by prompt index before reporting.

**Status**: Not yet fixed.

## Bug 3: EPUB large-file streaming (FIXED in v6)

**Location**: `analyze_large_file()`.

**Symptom**: Large EPUBs (>100 KB) used to hit the streaming path and be opened as binary ZIP in text mode → garbage output.

**Root cause**: `analyze_large_file()` checked file size BEFORE checking extension. ZIP/TAR/GZ files need `read_file()` (which handles archives), not `open()` in text mode.

**Fix applied in v6**: Extension check moved BEFORE streaming branch:

```python
def analyze_large_file(path, ...):
    ext = Path(path).suffix.lower()
    if ext in {'.epub', '.zip', '.tar', '.gz', '.tgz'}:
        text = read_file(path)  # handles archives in-memory
    else:
        # streaming 2-pass for plain text
        ...
```

**Status**: FIXED in v6.0.0.

## Bug 4: Section detection false positives on markdown headers

**Location**: `detect_sections()`.

**Symptom**: Markdown files with many `##` headers create too many tiny sections, fragmenting the analysis.

**Root cause**: `detect_sections()` matches `^#+\s` regex without considering header depth. `###` and `####` create sections just like `#` and `##`.

**Fix needed**: Add `--min-header-depth N` flag to filter shallow headers only.

**Status**: Not yet fixed. Workaround: preprocess markdown to flatten headers before analysis.

## Bug 5: HTTP API has no rate limiting

**Location**: HTTP server in `poler_v6.py api` mode.

**Symptom**: Under high request rate, the single-threaded HTTP server queues requests and becomes unresponsive.

**Root cause**: Uses `HTTPServer` (single-threaded), not `ThreadingHTTPServer`.

**Fix needed**: Switch to `ThreadingHTTPServer` and add basic rate limiting (max 10 concurrent requests per IP).

**Status**: Not yet fixed. Workaround: run behind a reverse proxy (nginx/caddy) for production use.

## Bug 6: diff does not parse comma-separated keywords

**Location**: `diff_files()` / CLI `diff` subcommand.

**Symptom**: `poler_v6.py diff FILE1 FILE2 -k "POLER,SCF,DM,DIIS"` returns 0 matches in both files, even though all 4 keywords clearly appear.

**Root cause**: The `-k` argument is treated as a single literal string. There is no `.split(",")` to break it into multiple keywords. So poler searches for the literal substring `"POLER,SCF,DM,DIIS"` (with commas), which obviously doesn't exist.

**Verified on**: NotebookLM corpus, 2026-07-03. `diff -k "POLER"` (single word) works (33 vs 61 matches), but `diff -k "POLER,SCF"` returns 0.

**Fix needed**: In the diff subcommand handler, split the `-k` argument on commas:
```python
keywords = [k.strip() for k in args.keywords.split(",") if k.strip()]
```
Then iterate over keywords and sum ε-scores across all matches.

**Status**: ✅ FIXED in v6.1.0 (2026-07-03), extended in v6.1.1 (2026-07-03). `diff_files()` now accepts str OR list of keywords. `cmd_diff` and `_legacy_v4_cli` both split `-k` on commas. **v6.1.1**: same comma-split logic extended to `cmd_analyze` (single file) and `cmd_analyze_dir` (directory) subcommands, plus the legacy v4 single-file path. All four call paths now parse `-k "POLER,SCF,DM"` into 3 separate keywords. Output includes a per-keyword breakdown table when ≥2 keywords are given. **Verified 2026-07-03 on NotebookLM corpus**: `analyze_dir DIR -k "POLER,SCF"` correctly splits into 2 keywords (POLER: 1 file / 33 fragments, SCF: 2 files / 89 fragments). `analyze FILE -k "POLER,SCF"` splits into 2 keywords (POLER: 93 hits, SCF: 342 hits). `poler_v6.py FILE -k "POLER,SCF"` (legacy v4) splits into 2 keywords (POLER: 93/33, SCF: 342/89). Single-keyword backward compat preserved: `-k POLER` still works as before.

## Bug 7: grep on ZIP archives produces mixed output

**Location**: `grep_search_file()` when called on a `.zip` / `.tar` / `.gz` file.

**Symptom**: `poler_v6.py grep "POLER" archive.zip` returns matches, but the output is interleaved with raw ZIP metadata (PK headers, internal filenames, deflate bytes). Useful matches are present but buried in noise.

**Root cause**: `grep_search_file()` calls `read_file()` which for archives returns a concatenated text stream of all files. The grep then matches both legitimate text content AND raw zip structural bytes that happen to contain the pattern.

**Fix needed**: For archive files, iterate over each archive member separately, grep each member's content individually, and prefix output with `[member_name]:` so the user can distinguish which file inside the archive matched.

**Status**: ✅ FIXED in v6.1.0 (2026-07-03). `grep_search_file()` now dispatches on archive extensions to `_grep_search_archive()`, which uses `read_archive()` / `read_epub()` to get per-member text and runs regex per-member. Each `GrepResult.file` is set to `'{archive_path}::{member_name}'`. `cmd_grep` groups results by `r.file` so `grep_format_text` shows per-member prefixes. **Verified 2026-07-03 on full NotebookLM ZIP (32 MB, 314 .md files)**: `grep "POLER" notebooklm.zip` returns 3653 clean matches with per-member prefixes like `notebooklm.zip::🌀 POLER.../118-POLER Integration Analysis and Refinement.md-N-...` — no PK-header or deflate-byte garbage. `grep -c` returns just `3653`. `grep --max-matches 3` truncates correctly.

## Bug 8: --multi treats multi-word query as exact phrase

**Location**: `search_in_text()` / CLI `--multi` flag.

**Symptom**: `poler_v6.py FILE --multi "density matrix SCF"` returns 0 matches even though "density matrix" and "SCF" both appear many times in the file.

**Root cause**: `--multi` searches for the exact phrase "density matrix SCF" (three consecutive words), not for documents containing all three words independently.

**Fix needed**: Add a `--multi-mode {phrase,all,any}` flag:
- `phrase` (current behavior) — exact phrase match
- `all` — all words must appear (set semantics)
- `any` — any word appears (union semantics)

Default to `phrase` for backwards compat.

**Status**: ✅ FIXED in v6.1.0 (2026-07-03). Added `--multi-mode {phrase,all,any}` flag to both `analyze_dir` subcommand and legacy v4 CLI. In `phrase` mode (default) — old behavior, splits `--multi` on commas, each item is one literal phrase. In `all`/`any` mode — splits `--multi` on whitespace into individual words; `all` keeps only files where every word appears (intersection via `_filter_all_mode()`); `any` runs each word separately and shows union. **Verified 2026-07-03 on 3-file NotebookLM subset** (POLER/SCF-containing files): `--multi "POLER SCF" --multi-mode phrase` → 0 hits (correct — phrase doesn't exist); `--multi-mode any` → 8 fragments for POLER + 9 fragments for SCF (union); `--multi-mode all` → 1 file with both POLER + SCF (33 fragments, intersection).

---

## v6.1.0 NEW FEATURE: --theme auto

**Location**: `detect_theme()`, `detect_theme_with_scores()`, `detect_theme_for_file()`, `detect_theme_for_directory()`.

**Usage**:
```bash
poler_v6.py analyze FILE --theme auto
poler_v6.py analyze_dir DIR --theme auto
poler_v6.py DIR/ -r --theme auto
```

**How it works**:
1. For a single file: reads file content (incl. EPUB/archive), samples first 20 KB, counts substring matches for every theme's vocabulary, picks theme with highest score.
2. For a directory: scans first 30 text files, runs `detect_theme()` on each (5 KB sample, threshold disabled), votes by file count (not raw word count — so one huge file doesn't dominate). Aggregate filter: theme must win ≥2 file votes.
3. If no theme passes confidence threshold (single-file mode) or aggregate vote threshold (directory mode), falls back to `general` and runs without a theme vocabulary.
4. Prints detection result + top-5 scoreboard (with both score and distinct-words count) + threshold info to stderr for transparency.

**Why substring match (not word-boundary) for long stems**: theme vocabularies contain stems like `'культив'`, `'резофаз'`, `'фредерит'` — these are designed to match any inflected form (`культивация`, `культивируя`, etc.). Word-boundary regex would miss these.

**v6.1.1 — word-boundary for short/numeric tokens**: tokens ≤3 chars or containing digits (`'33'`, `'M1'`, `'M2'`, `'T-0'`, `'W=0'`, `'P³'`, `'σ_e'`) now use `\b<word>\b` regex instead of substring count. This prevents `'33'` from matching inside `16333.7`, `'M1'` from matching inside `M16`, etc. Long stems (`культив`, `Кеплер`, `фредерит`) keep substring matching for inflection tolerance.

**v6.1.1 — confidence threshold**: in single-file mode, theme is accepted only if `distinct_words_matched ≥ 2` OR `total_score ≥ 5`. Prevents false positives where 1 stray match (e.g. a single "33" in a year) flips the whole file to a theme. Directory mode disables the per-file threshold (each file might have only 1-2 thematic words but the aggregate vote is the signal) but requires ≥2 file votes for a theme to win.

**Limitations**:
- Sample size (20 KB for file, 5 KB per file in dir) is a speed/accuracy tradeoff. For very short documents (< 1 KB), may misclassify.
- The `general` fallback is intentional: if no theme matches confidently, the tool shouldn't pretend it found one.
- Borderline case: a file may contain 2 theme vocab words by coincidence (e.g. `Хаос` + `Порядок` in a philosophy/physics text will match the `культивация` theme). Threshold reduces but does not eliminate these — post-filter by scoreboard if needed.
- Vocab design is the dominant quality factor: if a vocab word is too generic (like `Хаос`, `окно`, `33`), it will cause false positives regardless of threshold.

**Verified**: 2026-07-03 across 4 corpora after v6.1.1 fix:
- `POLER-ERI-v3.2.0.epub` (Rust crypto ERI README, 9 KB) → `general` (correct — no thematic content).
- `POLER_Project_Memory_G0.epub` (POLER project memory, 24 KB) → `general` (correct — was previously false-positive `астрономия` due to substring `33` matching inside `16333.7`).
- `Архів.tar.gz` (mixed POLER source archive, 7 MB) → `культивация` (score=4, 2 distinct words: Хаос + Порядок). Borderline match — vocab words Хаос/Порядок are philosophy terms that overlap with cultivation theme by design; threshold allows this because 2 distinct words matched.
- `notebooklm-bulk-export-1782991484384.zip` (NotebookLM corpus, 314 .md files) → `биология` (changed from previously incorrect `астрономия` which was driven by `33` substring matches across many files). Biology won plurality vote across 30 sampled files — corpus has biochemistry (Kohn-Sham DFT on biomolecules, NIST thermochem for biomolecules). Result is meaningful, not noise-driven.
