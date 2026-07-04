# POLER[Ψ] — Quantum-Chemical Residual Rendering for LLMs

## What this is

POLER[Ψ] is the **quantum-chemical research architecture** layer of the POLER project — distinct from the standalone text-data toolkit (see `poler-toolkit` skill).

The core idea: treat a frozen LLM's weight space as a quantum-chemical basis, and solve a **self-consistent field (SCF)** problem per prompt to produce a density matrix `DM_final` that sculpts the prompt's hidden state:

```
x_final = x_rich + α · DM_final @ x_rich
```

This is NOT a text processing tool. It's a research experiment asking: *can quantum-chemical metaphors (Hamiltonian, density matrix, SCF, McWeeny purification) produce meaningful operators over LLM weight space?*

## When to invoke this skill

Invoke `poler-psi` when the user asks about:

- **POLER[Ψ] architecture** — SCF, DIIS, McWeeny purification, POLER Kick, residual rendering
- **Phase D / G0 experiments** — head sweep, alpha sweep, dimensionality fix, benchmarks
- **The 896×896 → 128×128 dimensionality bug** — what was wrong, the fix, the 1552× speedup
- **Comparison vs PyTorch baseline** — 40% HEAD=24 accuracy, best-of-27 methodology critique
- **Qwen 0.5B weight introspection** — safetensors address resolution, weight vocab index
- **Phase G0 step 2 plan** — honest benchmark with 50-100 prompts, train/test split, fixed α

**Do NOT invoke for**: text grep, file analysis, EPUB reading, TF-IDF scoring. That's `poler-toolkit`.

## Dependencies

Unlike `poler-toolkit` (zero deps), this skill requires:

- `numpy` — matrix ops, eigh
- `torch` — Qwen 0.5B forward passes
- `safetensors` — weight loading
- `tokenizers` — Qwen tokenizer

Plus the Qwen 0.5B model weights (~1.5 GB) at `/home/z/my-project/work/qwen-text/model.safetensors`.

## What's inside

### `scripts/phase_d4_head_sweep.py` (32 KB)

**ORIGINAL Phase D v4** — contains the dimensionality bug. 1620 experiments: 10 prompts × 9 alphas × 3 renderers × 6 head layers.

Key functions (BUGGY — D=896 space):
- `build_scf_operators()` — builds H_core, D_mat, DM_init, J_asym all in D=896 space
- `run_scf()` — eigendecomposes D×D matrix (O(D³) = 896³ = 718M ops per iter)
- `pi_lambda()` — McWeeny Π_Λ purification

Outputs: `/home/z/my-project/work/phase_d/results_v4.json`

### `scripts/phase_g0_dim_fix.py` (28 KB)

**Phase G0 step 1 — K-subspace fix.** Verifies the bug fix on the same 10 prompts using cached `x_rich` from `phase_d4_head_sweep.py`.

Key functions (FIXED — K=128 subspace):
- `build_scf_operators_K()` — projects all operators via `A.T @ · @ A` to K×K space
- `run_scf_K()` — eigendecomposes K×K matrix (O(K³) = 128³ = 2M ops per iter)
- Lifts DM_final_K back to D-space ONCE via `A @ DM_K @ A.T`

Outputs: `/home/z/my-project/work/phase_d/results_v4_dim_fix.json`

### `scripts/phase_g0_compare.py` (12 KB)

Line-by-line diff of `results_v4.json` (original) vs `results_v4_dim_fix.json` (G0 fix). Reports per-head accuracy delta, per-prompt flips, speed comparison, SCF convergence comparison.

Outputs: `/home/z/my-project/work/phase_d/g0_compare_report.txt`

### `scripts/address_resolver.py` (20 KB)

Qwen 0.5B safetensors weight address resolver. Maps logical names (e.g., `layers.0.attention.q_proj.weight`) to actual safetensors tensor keys. Used for weight introspection without loading the full model into memory.

### `scripts/weight_address_index.py` (16 KB)

Builds a vocabulary index over Qwen weight addresses. Used to find which weights are most relevant to a given prompt (via TF-IDF over weight address names).

## Architecture summary

For the full architecture document, see `references/architecture.md`. Brief summary:

1. **Frozen LLM** (Qwen 0.5B, D=896, 24 layers) provides hidden states
2. **Subspace extraction**: take K=128 columns from weight matrices as basis vectors A ∈ R^{D×K}
3. **Build operators in K-space**: H_core, D_mat, DM_init, J_asym — all K×K
4. **SCF loop** (K-space, post-G0): eigh → DM_target → McWeeny Π_Λ → DIIS mixing → POLER Kick
5. **Lift to D-space**: DM_final_D = A @ DM_final_K @ A.T (once, for rendering)
6. **Render**: x_final = x_rich + α · DM_final_D @ x_rich
7. **Decode**: feed x_final back into Qwen decoder at chosen head layer (4, 8, 12, 16, 20, 24)

## Phase G0 results

| Metric | Original (D=896) | G0 fix (K=128) | Improvement |
|---|---|---|---|
| Mean time per prompt | 15892ms | 10.2ms | **1552×** |
| HEAD=24 accuracy | 40% | 40% | IDENTICAL (zero per-prompt flips) |
| HEAD=20 accuracy | 30% | 20% | REGRESSED (Paris lost — D-space artifact) |
| SCF residual | 1e-4 (stuck) | 1e-15 (machine precision) | Converged |
| Eigenvalue degeneracy | All 10 prompts = 0.0 | Non-zero | Resolved |

**Key robustness claim**: HEAD=24 40% is preserved exactly. The K-subspace fix doesn't change the answer for the working config — it just makes it 1552× faster and actually converged.

See `references/g0_dim_bug.md` for full bug analysis and `references/benchmarks.md` for complete results.

## Pending work — Phase G0 step 2

Per Claude's variant C recommendation (fix bug first, then expand benchmark honestly):

1. **Expand to 50-100 prompts** with balanced categories (geography, science, math, common sense, language)
2. **Train/test split** (70/30) — fix α on train, evaluate on test (no per-prompt α tuning)
3. **Single-config reporting** — pick ONE (α, renderer, head) on train, report test accuracy
4. **Matched compute budget** vs PyTorch baseline
5. **95% Wilson confidence intervals** — 10 prompts gives ±30% CI, useless for statistical claims
6. **Reproducibility** — random seeds, versioned prompts, versioned weights

## External data (NOT in skill — lives in /home/z/my-project/work/)

- `/home/z/my-project/work/qwen-text/` — Qwen 0.5B safetensors (~1.5 GB)
- `/home/z/my-project/work/phase_d/results_v4.json` — Phase D v4 raw results
- `/home/z/my-project/work/phase_d/results_v4_dim_fix.json` — G0 step 1 results
- `/home/z/my-project/work/phase_d/g0_compare_report.txt` — comparison report
- `/home/z/my-project/work/phase_d/lens_rag_cache.json` — 4052 NotebookLM tokens
- `/home/z/my-project/work/phase_d/lens_wiki_cache.json` — Wikipedia LENS cache
- `/home/z/my-project/work/wiki_corpus/` — 40 Wikipedia pages

If these are missing after env reset, restore from `/home/z/my-project/upload/` archives.

## How to run

### Quick verification (G0 step 1)
```bash
python /home/z/my-project/skills/poler-psi/scripts/phase_g0_dim_fix.py
# Output: /home/z/my-project/work/phase_d/results_v4_dim_fix.json
# Expected: 1552× speedup, HEAD=24 = 40%, SCF residual ~1e-15
```

### Compare against original
```bash
python /home/z/my-project/skills/poler-psi/scripts/phase_g0_compare.py
# Output: /home/z/my-project/work/phase_d/g0_compare_report.txt
```

### Full Phase D v4 sweep (slow, ~6 hours)
```bash
python /home/z/my-project/skills/poler-psi/scripts/phase_d4_head_sweep.py
# Output: /home/z/my-project/work/phase_d/results_v4.json
# WARNING: contains the bug, run only for reproducibility
```

## Philosophical framing

> *"Веса хранят не ответы, а способность строить ответы."*

POLER[Ψ]'s claim: the SCF iteration extracts a *contextual projection operator* (DM_final) from frozen weights, not a lookup. The density matrix is computed per-prompt via diagonalization of a prompt-conditioned Hamiltonian — the LLM weights provide the basis, the prompt sculpts the operator.

Whether this is genuinely different from attention is an open empirical question. Phase G0 step 1 (the bug fix) was the first honest step — Phase G0 step 2 (the expanded benchmark) will be the real test.

## Related skill

For the **standalone text-data instrument** (grep, search, EPUB reading, TF-IDF analysis, file diff — zero dependencies), see the separate `poler-toolkit` skill. That's the everyday text processing tool, independent of the quantum-chemical experiments here.
