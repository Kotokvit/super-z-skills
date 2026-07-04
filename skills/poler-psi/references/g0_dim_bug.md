# Phase G0 — Dimensionality Bug & Fix

## TL;DR

`build_scf_operators` in `phase_d4_head_sweep.py` built H_core, D_mat, DM_init, J_asym in **D=896 space** instead of the **K=128 column subspace**. This caused 1552× slowdown AND prevented SCF convergence. Fixed by projecting all operators to K-subspace via `A.T @ · @ A` before SCF, then lifting DM_final back via `A @ DM_K @ A.T` once for rendering.

## Discovery timeline

1. **June 2026** — Phase D v4 sweep completed. 1620 experiments. HEAD=24 best-of-27 = 40%. Reported as "POLER beats PyTorch baseline (40% vs 30%)".
2. **July 2, 2026** — Source code packaged as `poler_source_code.tar.gz` for Grok (xAI) review. Grok couldn't load 4.4 MB archive.
3. **July 2, 2026** — User reran analysis through Claude (Anthropic). Claude found:
   - **Bug**: 896×896 operators instead of 128×128
   - **Methodological issue**: best-of-27 vs 1-shot baseline, no train/test split, α tuned on test
4. **July 3, 2026** — User chose Claude's variant C: fix bug first, then expand benchmark honestly.
5. **July 3, 2026** — Phase G0 step 1 verified: 1552× speedup, HEAD=24 40% preserved.

## The bug

### Original code (broken)

```python
def build_scf_operators(A, epsilon, V_prompt_norm, tfidf_weights):
    """Build SCF operators — ORIGINAL, BUGGY"""
    H_core_D = V_prompt_norm @ np.diag(tfidf_weights) @ V_prompt_norm.T  # (D, D) = (896, 896)
    D_mat_D = np.tanh(A @ A.T) - np.eye(D)                              # (D, D) = (896, 896)
    DM_init_D = (A @ np.diag(epsilon) @ A.T) / (epsilon.max() + 1e-12)  # (D, D) = (896, 896)
    J_asym_D = ...                                                      # (D, D) = (896, 896)
    return H_core_D, D_mat_D, J_asym_D, DM_init_D  # All D×D!
```

```python
def run_scf(DM0, build_F_sym, J_asym, D, max_iter=20, ...):
    for t in range(max_iter):
        F_used = build_F_sym(DM_current) + α_kick * J_asym   # (D, D)
        eigvals, eigvecs = np.linalg.eigh(F_used)            # ⚠ O(D³) = 896³ = 718M ops
        occ = eigvecs[:, :K_eff]
        DM_target = occ @ occ.T                              # (D, D)
        # ... DIIS, McWeeny, ...
```

### Why it's wrong

`A ∈ R^{D×K}` is column-normalized (each column has unit L2 norm), but **not orthonormal** — columns aren't mutually orthogonal in general. So `A.T @ X @ A` does NOT preserve X as an operator; it projects X onto the K-dimensional column subspace.

The SCF iteration's meaningful dynamics happen in this K-subspace (the "occupied" eigenstates are exactly K_eff ≤ K vectors). Building F in D-space and then taking eigvecs[:, :K_eff] is:

1. **Wasteful** — eigendecomposing a D×D matrix when only K eigenvalues matter
2. **Numerically different** — the D-space eigenstructure includes K=128 meaningful modes + 768 noise modes (orthogonal complement of A's column space), which contaminate the iteration via finite-precision arithmetic
3. **Unconvergable** — the noise modes have ~0 eigenvalues, causing McWeeny purification to amplify numerical garbage → SCF gets stuck at 1e-4 residual

### The fix

```python
def build_scf_operators_K(A, epsilon, V_prompt_norm, tfidf_weights):
    """Build SCF operators in K×K subspace — FIXED"""
    K = A.shape[1]
    H_core_D = V_prompt_norm @ np.diag(tfidf_weights) @ V_prompt_norm.T
    H_core_K = A.T @ H_core_D @ A    # (K, K) = (128, 128)
    H_core_K = (H_core_K + H_core_K.T) / 2  # symmetrize
    
    cos_sim_K = A.T @ A              # (K, K)
    W_D = np.tanh(cos_sim_K) - np.eye(K)
    D_mat_K = (W_D + W_D.T) / 2
    
    DM_init_D = (A @ np.diag(epsilon) @ A.T) / (epsilon.max() + 1e-12)
    DM_init_K = A.T @ DM_init_D @ A  # (K, K)
    
    J_asym_K = J_small  # already K×K
    
    return H_core_K, D_mat_K, J_asym_K, DM_init_K
```

```python
def run_scf_K(DM0_K, build_F_sym_K, J_asym_K, K, max_iter=20, ...):
    for t in range(max_iter):
        F_used = build_F_sym_K(DM_current_K) + α_kick * J_asym_K  # (K, K)
        eigvals, eigvecs = np.linalg.eigh(F_used)                  # O(K³) = 128³ = 2M ops ✓
        occ = eigvecs[:, :K_eff]
        DM_target_K = occ @ occ.T                                  # (K, K)
        # ... DIIS, McWeeny, POLER Kick in K-space ...
    return DM_final_K, ...
```

After SCF: lift DM_final_K to D-space ONCE for rendering:
```python
DM_final_D = A @ DM_final_K @ A.T   # (D, D) — single matmul
x_final = x_rich + α * DM_final_D @ x_rich
```

## Speed math

| Operation | Original (D=896) | Fixed (K=128) | Speedup |
|---|---|---|---|
| `eigh(F)` per SCF iter | O(D³) = 718M | O(K³) = 2M | 343× |
| # full eigenvecs stored | D² = 802K | K² = 16K | 50× |
| DM_target = occ @ occ.T | D² · K_eff = 7.2M | K² · K_eff = 16K | 450× |
| McWeeny DM³ | D³ = 718M | K³ = 2M | 343× |
| DIIS residual matrix | m · D² | m · K² | 50× |

Across 20 SCF iterations × 10 prompts × 9α × 3 renderers × 6 heads = 32400 SCF runs:
- Original: 32400 × ~490ms = 15892ms mean per prompt (parallelized weirdly, some configs slow)
- Fixed: 32400 × ~0.31ms = 10.2ms mean per prompt
- **Effective speedup: 1552×**

## Accuracy impact

### HEAD=24 (best config)

**Zero per-prompt flips.** The same 4 prompts won (Paris, sun, two-plus-two, fastest-animal), the same 6 lost. Accuracy preserved at 40%.

This is the key robustness result: the K-subspace fix doesn't change the answer for the working config — it just makes it 1552× faster and actually converged.

### HEAD=20 (regressed)

| Prompt | Original | G0 fix | Reason |
|---|---|---|---|
| Paris is the capital of | ✓ | ✗ | "France" was a D-space artifact (won via noise-mode coupling) |

HEAD=20 dropped 30% → 20%. Claude's interpretation: the 30% was never "real" POLER behavior — it was the D-space noise modes accidentally producing the right token. The K-subspace fix removes that artifact.

### Eigenvalue degeneracy

All 10 prompts had `min_eig_gap = 0.0` in the original (structural issue — A's columns aren't orthogonal). Post-G0, the gap is non-zero and meaningful (the SCF has a real ground state to converge to).

## What this means for the broader claim

**Before G0**: "POLER beats PyTorch 40% vs 30%" — but with 1552× overhead and SCF not converging.

**After G0**: "POLER matches its own 40% (HEAD=24) at 1552× speed, SCF converges to machine precision" — but the 40% is still best-of-27, not single-config.

**Next step (G0 step 2)**: expand benchmark honestly:
- 50-100 prompts (not 10)
- Fixed α (tuned on train split, not test)
- 95% confidence intervals
- Matched compute budget vs PyTorch baseline

Then we'll know if POLER is genuinely competitive or just an interesting architectural curiosity.

## File references

- Original buggy code: `scripts/phase_d4_head_sweep.py` (lines ~180-220, `build_scf_operators`)
- Fixed code: `scripts/phase_g0_dim_fix.py` (lines ~185-260, `build_scf_operators_K` + `run_scf_K`)
- Comparison report: `/home/z/my-project/work/phase_d/g0_compare_report.txt`
- Raw results: `/home/z/my-project/work/phase_d/results_v4.json` (orig), `results_v4_dim_fix.json` (G0)
