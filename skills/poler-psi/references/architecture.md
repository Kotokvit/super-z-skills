# POLER[Ψ] Architecture

## Core equation

```
x_final = x_rich + α · DM_final @ x_rich
```

Where:
- `x_rich ∈ R^D` (D=896 for Qwen 0.5B) — prompt's hidden state from a frozen LLM
- `DM_final ∈ R^{D×D}` — density matrix from SCF iteration
- `α ∈ [0, 3]` — coupling strength (swept)

## Pipeline

### Step 1 — Build operators

Given:
- `A ∈ R^{D×K}` (D=896, K=128) — column subspace from Qwen weights (basis vectors)
- `ε ∈ R^K` — eigenvalues from weight diagonalization
- `V_prompt_norm ∈ R^{D×V}` (V=vocab=151936) — prompt's per-token hidden states, L2-normalized
- `tfidf_weights ∈ R^V` — TF-IDF weights over the prompt

**Operators:**

```
H_core_D = V_prompt_norm @ diag(tfidf_weights) @ V_prompt_norm.T   # (D, D) Hamiltonian
D_mat_D  = tanh(A @ A.T) - I                                        # (D, D) metric (asymmetric)
DM_init_D = (A @ diag(ε) @ A.T) / ε.max()                           # (D, D) initial DM
J_asym_D  = asymmetric part of H_core_D                             # (D, D)
```

**⚠ Phase G0 fix**: All operators must be projected to K-subspace via `A.T @ · @ A` BEFORE the SCF loop:

```
H_core_K = A.T @ H_core_D @ A      # (K, K) = 128×128
D_mat_K  = A.T @ D_mat_D  @ A
DM_init_K = A.T @ DM_init_D @ A
J_asym_K = already K×K
```

See `g0_dim_bug.md` for why this matters.

### Step 2 — SCF loop (in K-space, post-G0)

For iteration `t`:

1. **Build Fock matrix**:
   ```
   F_sym_K = H_core_K + J(DM_K) - K(DM_K)
   ```
   Where `J` (Coulomb) and `K` (exchange) are contractions over DM.

2. **Eigendecompose**:
   ```
   eigvals, eigvecs = eigh(F_sym_K)   # K×K, fast (was D×D before G0!)
   occ = eigvecs[:, :K_eff]           # K_eff = number of occupied states
   DM_target_K = occ @ occ.T
   ```

3. **McWeeny Π_Λ purification** (idempotency):
   ```
   DM_pure_K = 3·DM²·(I-DM) + DM³
   ```

4. **DIIS mixing** (Pulay acceleration):
   - Keep last `m=6` DM iterates and residuals
   - Solve small LS problem for mixing coefficients
   - Fallback to simple mixing `DM_new = (1-η)·DM_old + η·DM_target` if DIIS fails

5. **POLER Kick** (asymmetric perturbation, once per iteration):
   ```
   F_used = F_sym_K + α_kick · J_asym_K
   ```
   This breaks the symmetric eigenspectrum and produces non-trivial DM structure.

6. **Convergence check**:
   ```
   residual = ||DM_new - DM_old||_F
   if residual < threshold (1e-3 orig, 1e-15 post-G0): break
   ```

Max 20 iterations.

### Step 3 — Lift to D-space and render

**Post-G0**: lift DM_final_K back to D-space ONCE for rendering:
```
DM_final_D = A @ DM_final_K @ A.T   # (D, D) — single matmul
```

### Step 4 — Render

```
x_final = x_rich + α · DM_final_D @ x_rich
```

Three renderers were tested (Phase D v4):
- **R1** — direct: `x_rich + α · DM @ x_rich`
- **R2** — normalized: `x_rich + α · DM @ x_rich / ||DM @ x_rich||`
- **R3** — gated: `x_rich + α · sigmoid(||DM @ x_rich||) · DM @ x_rich`

### Step 5 — Decode

Feed `x_final` back into Qwen's decoder heads at a chosen layer (HEAD=4,8,12,16,20,24). Compare argmax token against expected target token.

## Hyperparameters

| Param | Value | Notes |
|---|---|---|
| D | 896 | Qwen 0.5B hidden dim |
| K | 128 | Subspace dim (number of basis vectors from weights) |
| N_LAYERS | 24 | Qwen 0.5B |
| VOCAB | 151936 | Qwen tokenizer |
| α sweep | [0.0, 0.1, 0.3, 0.5, 0.7, 1.0, 1.5, 2.0, 3.0] | 9 values |
| HEAD sweep | [4, 8, 12, 16, 20, 24] | 6 layers |
| Renderers | R1, R2, R3 | 3 |
| SCF max_iter | 20 | |
| SCF threshold | 1e-3 (orig), 1e-15 (post-G0) | |
| DIIS m | 6 | Pulay history size |
| POLER Kick α_kick | 0.002 (η_r) | |

## Phase D v4 results (10 prompts)

| HEAD | Best α | Renderer | Accuracy |
|---|---|---|---|
| 4 | — | — | 0% |
| 8 | — | — | 0% |
| 12 | — | — | 0% |
| 16 | — | — | 0% |
| 20 | 3.0 | R1 | 30% |
| 24 | 3.0 | R1 | **40%** |
| baseline | — | — | 30% (Qwen forward, no POLER) |

**Best-of-27 caveat** (per Claude's methodological critique): the 40% is the max over 9α × 3 renderers = 27 configs. This is NOT a single-config accuracy and should not be compared to a 1-shot baseline without disclaimers.

## Phase G0 step 1 results (same 10 prompts, K-subspace fix)

| HEAD | Original | G0 fix | Delta | Verdict |
|---|---|---|---|---|
| 20 | 30% | 20% | -10% | REGRESSED (Paris lost — D-space artifact) |
| 24 | 40% | 40% | 0% | IDENTICAL (zero per-prompt flips) |
| Speed | 15892ms | 10.2ms | **1552×** | FIXED |
| SCF residual | 1e-4 | 1e-15 | machine precision | FIXED |

The HEAD=24 40% preservation is the key robustness claim: the K-subspace fix doesn't change the mathematical answer for the working config, just makes it 1552× faster and actually converged.

## Phase G0 step 2 (pending)

Per Claude's variant C recommendation:

1. Expand from 10 prompts to 50-100 prompts (with held-out test set)
2. Fix α on train split, evaluate on test split (no per-prompt α tuning)
3. Compare against PyTorch baseline at matched compute budget
4. Add 95% confidence intervals (Wilson score)
5. Report mean accuracy + std across seeds

## Why "POLER[Ψ]"

- **POLER** = Projective Operator Learning via Eigendecomposition Refinement (backronym)
- **Ψ** = wavefunction symbol — the density matrix DM plays the role of `|Ψ⟩⟨Ψ|` in quantum chemistry
- The metaphor: weights are basis functions, prompt sculpts the Hamiltonian, SCF finds the ground-state density
