# POLER[Ψ] Benchmarks

## Phase D v4 (original, buggy)

**Setup**: 10 prompts × 9 alphas × 3 renderers × 6 head layers = 1620 experiments.

**Caveats** (per Claude):
1. Best-of-27 reporting (9α × 3 renderers per head) inflates accuracy
2. No train/test split — α is tuned on test
3. PyTorch baseline is 1-shot, not best-of-anything
4. SCF doesn't converge (residual stuck at 1e-4)

### Per-head accuracy (best-of-27)

| HEAD | Accuracy | Notes |
|---|---|---|
| 4 | 0% | No prompt solved |
| 8 | 0% | No prompt solved |
| 12 | 0% | No prompt solved |
| 16 | 0% | No prompt solved |
| 20 | 30% | Paris, sun, fastest-animal |
| 24 | **40%** | Paris, sun, two-plus-two, fastest-animal |
| baseline | 30% | Qwen forward, no POLER (1-shot) |

### Per-prompt winners (HEAD=24, 40% config)

| # | Prompt | Target | Won? | Winning config |
|---|---|---|---|---|
| 0 | Paris is the capital of | France | ✓ | R1 @ α=3.0 |
| 1 | The capital of Japan is | Tokyo | ✗ | — |
| 2 | The largest planet is | Jupiter | ✗ | — |
| 3 | The sun rises in the | east | ✓ | R1 @ α=0.0 (= baseline) |
| 4 | Two plus two equals | four | ✓ | R1 @ α=1.0 |
| 5 | The opposite of hot is | cold | ✗ | — |
| 6 | The color of grass is | green | ✗ | — |
| 7 | The sky is | blue | ✗ | — |
| 8 | The fastest land animal is | cheetah | ✓ | R1 @ α=3.0 |
| 9 | Birds can | fly | ✗ | — |

### Methodology issues (per Claude)

> **"POLER beats PyTorch 40% vs 30%"** — not a fair comparison.
> 
> - POLER reports best-of-27 (9α × 3 renderers) per head
> - PyTorch baseline is 1-shot forward pass
> - α is tuned on the test set (no held-out split)
> - 10 prompts is too small for statistical significance (Wilson 95% CI at 40% is ±30%, so 40% could be anywhere from 10% to 70%)
> 
> Recommend: fix bug first, then expand to 50-100 prompts with train/test split.

## Phase G0 step 1 (K-subspace fix)

**Setup**: Same 10 prompts, same cached `x_rich` from PyTorch baseline. Only difference: SCF operators in K=128 space instead of D=896.

### Per-head accuracy comparison

| HEAD | Original | G0 fix | Delta | Verdict |
|---|---|---|---|---|
| 4 | 0% | 0% | 0% | IDENTICAL |
| 8 | 0% | 0% | 0% | IDENTICAL |
| 12 | 0% | 0% | 0% | IDENTICAL |
| 16 | 0% | 0% | 0% | IDENTICAL |
| 20 | 30% | 20% | -10% | REGRESSED (Paris lost) |
| 24 | **40%** | **40%** | 0% | IDENTICAL (zero per-prompt flips) |
| baseline | 30% | 30% | 0% | Same Qwen forward |

### Per-prompt @ HEAD=24 (identical to original)

Zero flips. Same 4 wins, same 6 losses. This is the robustness claim.

### Per-prompt @ HEAD=20 (Paris lost)

| # | Prompt | Original | G0 fix | Why |
|---|---|---|---|---|
| 0 | Paris is the capital of | ✓ | ✗ | D-space artifact removed |

### Speed & convergence

| Metric | Original | G0 fix | Improvement |
|---|---|---|---|
| Mean time per prompt | 15892ms | 10.2ms | **1552×** |
| SCF residual (final) | 1e-4 (stuck) | 1e-15 (machine precision) | Converged |
| SCF iterations | 20 (max, never converged) | ~5-8 (converged) | 3× fewer |
| Eigenvalue gap (min) | 0.0 (degenerate) | non-zero | Resolved |

## Pending — Phase G0 step 2 (honest benchmark)

Per Claude's variant C, AFTER fixing the bug:

### Goals

1. **50-100 prompts** (not 10) — drawn from a balanced mix of:
   - Geography (capitals, rivers, mountains)
   - Science (planets, elements, biology)
   - Math (arithmetic, sequences)
   - Common sense (colors, opposites, time)
   - Language (synonyms, idioms)

2. **Train/test split**: 70/30, with α fixed on train (no test-set tuning)

3. **Single-config reporting**: pick ONE (α, renderer, head) on train, report test accuracy

4. **Matched compute budget**: PyTorch baseline gets same FLOPs as POLER (e.g., multi-shot or larger model)

5. **Statistical reporting**:
   - Mean accuracy + 95% Wilson CI
   - Per-category breakdown
   - Per-prompt results table (not just aggregate)

6. **Reproducibility**: random seeds, versioned prompts, versioned weights

### Expected outcomes

Three possible outcomes from G0 step 2:

**A. POLER significantly beats baseline** (>5% absolute improvement with CI not crossing zero)
→ Genuine architectural contribution. Write up. Integrate Grok's BGE-M3 + constrained decoding suggestions.

**B. POLER matches baseline** (within CI)
→ Architecturally interesting but no practical advantage. Pivot to "POLER as analysis tool" (interpretability, not generation).

**C. POLER underperforms baseline**
→ The 40% was a small-sample artifact. Honest write-up, archive the project.

## File references

- `/home/z/my-project/work/phase_d/results_v4.json` — Phase D v4 raw (1620 experiments)
- `/home/z/my-project/work/phase_d/results_v4_dim_fix.json` — G0 step 1 raw
- `/home/z/my-project/work/phase_d/g0_compare_report.txt` — Line-by-line comparison report
- `/home/z/my-project/work/phase_d/lens_rag_cache.json` — 4052 NotebookLM tokens (LENS RAG cache)
- `/home/z/my-project/work/phase_d/lens_wiki_cache.json` — Wikipedia LENS cache
- `/home/z/my-project/work/wiki_corpus/` — 40 Wikipedia pages (corpus.txt + manifest.json)
