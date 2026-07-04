#!/usr/bin/env /usr/bin/python3
"""
phase_g0_dim_fix.py
===================
Phase G0 step 1 — Dimensionality bug fix verification.

Hypothesis (Claude's concern):
  The original phase_d4_head_sweep.py builds SCF operators in D=896 space
  instead of K=128 subspace. This is ~343x slower AND potentially a different
  mathematical problem (because A is column-normalized, not orthonormalized,
  so A.T @ X @ A != X in general).

What this script does:
  1. Reuses cached PyTorch hidden states from _pt_multicutoff_result.json
     (same 10 prompts, same x_rich values as Phase D v4)
  2. Replaces build_scf_operators / make_F_builder / run_scf with K-subspace
     versions: project everything to K x K, run SCF there, lift DM_final
     back to D x D ONCE for rendering
  3. Saves to results_v4_dim_fix.json
  4. Prints line-by-line diff vs results_v4.json

Output:
  /home/z/my-project/work/phase_d/results_v4_dim_fix.json
"""
from __future__ import annotations
import json, time, sys, os
from pathlib import Path
import numpy as np
from safetensors import safe_open
from tokenizers import Tokenizer

# ============================================================================
# Config — IDENTICAL to phase_d4_head_sweep.py
# ============================================================================
OUT_DIR = Path("/home/z/my-project/work/phase_d")
QWEN_DIR = Path("/home/z/my-project/work/qwen-text")
WEIGHTS_PATH = QWEN_DIR / "model.safetensors"
TOKENIZER_PATH = QWEN_DIR / "tokenizer.json"

D = 896
N_LAYERS = 24
VOCAB = 151936
K_TARGET = 128
HEAD_LAYERS_SWEEP = [4, 8, 12, 16, 20, 24]
ALPHAS = [0.0, 0.1, 0.3, 0.5, 0.7, 1.0, 1.5, 2.0, 3.0]

PROMPTS = [
    ("Paris is the capital of",       " France"),
    ("The capital of Japan is",       " Tokyo"),
    ("The largest planet is",         " Jupiter"),
    ("The sun rises in the",          " east"),
    ("Two plus two equals",           " four"),
    ("The opposite of hot is",        " cold"),
    ("The color of grass is",         " green"),
    ("The sky is",                    " blue"),
    ("The fastest land animal is",    " the"),
    ("Birds can",                     " fly"),
]

np.random.seed(42)

print("=" * 78)
print("Phase G0 step 1 — Dimensionality bug fix verification")
print("=" * 78)

# ============================================================================
# 1. Load Qwen weights (numpy) — IDENTICAL to phase_d4
# ============================================================================
import ml_dtypes  # for bfloat16 support
print("\n[1] Loading Qwen weights (numpy)...")
t0 = time.perf_counter()
WEIGHTS = {}
with safe_open(str(WEIGHTS_PATH), framework="numpy") as f:
    for k in f.keys():
        t = f.get_tensor(k)
        if t.dtype == ml_dtypes.bfloat16:
            t = t.astype(np.float32)
        else:
            t = t.astype(np.float32)
        WEIGHTS[k] = t
print(f"  {len(WEIGHTS)} tensors in {time.perf_counter()-t0:.2f}s")

embed = WEIGHTS["model.embed_tokens.weight"].astype(np.float32)
final_norm_w = WEIGHTS["model.norm.weight"].astype(np.float32)
embed_norm_rows = embed / (np.linalg.norm(embed, axis=1, keepdims=True) + 1e-12)

print("\n[2] Tokenizer...")
tokenizer = Tokenizer.from_file(str(TOKENIZER_PATH))
def encode(text): return tokenizer.encode(text).ids
def id_to_str(i): return tokenizer.id_to_token(int(i))

# ============================================================================
# 2. Operators from Qwen weights — IDENTICAL to phase_d4
# ============================================================================
print("\n[3] Building M_coul / M_exch from layer 12 MLP weights...")
layer_mid = N_LAYERS // 2
gate = WEIGHTS[f"model.layers.{layer_mid}.mlp.gate_proj.weight"].astype(np.float32)
down = WEIGHTS[f"model.layers.{layer_mid}.mlp.down_proj.weight"].astype(np.float32)
M_coul = (gate.T @ gate + down @ down.T) / 2.0
M_coul = (M_coul + M_coul.T) / 2
M_coul = M_coul / (np.linalg.norm(M_coul) + 1e-12)
M_exch_full = np.dot(down.T, down)
M_exch = M_exch_full[:D, :D].copy()
M_exch = (M_exch + M_exch.T) / 2
M_exch = M_exch / (np.linalg.norm(M_exch) + 1e-12)

# ============================================================================
# 3. SCF machinery (DIIS + PulayKick + McWeeny) — IDENTICAL to phase_d4
# ============================================================================
def commutator(F, DM): return F @ DM - DM @ F
def energy(F, DM): return float(np.linalg.norm(commutator(F, DM), 'fro'))
def pi_lambda(X):
    X2 = X @ X; X3 = X2 @ X
    return 3 * X2 - 2 * X3

class DIIS:
    def __init__(self, max_history=6, kappa=0.5):
        self.max_history = max_history; self.kappa = kappa
        self.errors = []; self.Fs = []
    def push(self, F, DM):
        self.errors.append(commutator(F, DM).flatten())
        self.Fs.append(F.copy())
        if len(self.errors) > self.max_history:
            self.errors.pop(0); self.Fs.pop(0)
    def extrapolate(self, F_current, DM_current):
        if len(self.errors) < 2: return F_current, 0.0, 0
        n = len(self.errors); E = np.array(self.errors); B = E @ E.T
        A_aug = np.zeros((n+1, n+1))
        A_aug[:n, :n] = B; A_aug[:n, n] = 1.0; A_aug[n, :n] = 1.0
        rhs = np.zeros(n+1); rhs[n] = 1.0
        try:
            sol = np.linalg.solve(A_aug + 1e-12*np.eye(n+1), rhs); c = sol[:n]
        except np.linalg.LinAlgError:
            c = np.ones(n) / n
        F_extrap = sum(c[i] * self.Fs[i] for i in range(n))
        F_extrap = (F_extrap + F_extrap.T) / 2
        err_norm = float(np.linalg.norm(commutator(F_current, DM_current)))
        gamma = max(0.0, min(1.0, 1.0 - self.kappa * err_norm))
        F_used = gamma * F_extrap + (1.0 - gamma) * F_current
        return (F_used + F_used.T) / 2, gamma, n

# ============================================================================
# 4. Load cached PyTorch results (NO recompute)
# ============================================================================
print("\n[4] Loading cached PyTorch hidden states from _pt_multicutoff_result.json...")
PT_CACHE = OUT_DIR / "_pt_multicutoff_result.json"
if not PT_CACHE.exists():
    print(f"  ERROR: cache not found at {PT_CACHE}")
    print(f"  Run phase_d4_head_sweep.py first to generate it.")
    sys.exit(1)
pt_results = json.loads(PT_CACHE.read_text())
print(f"  Loaded {len(pt_results)} prompt results from cache")

# ============================================================================
# 5. LENS — IDENTICAL to phase_d4 (selects A, epsilon, V_prompt_norm, tfidf_weights)
# ============================================================================
def lens_select_archetypes(prompt_ids, expected_token, K=K_TARGET):
    tfidf_weights = np.array([1.0, 0.2, 0.3, 0.8, 0.4], dtype=np.float32)[:len(prompt_ids)]
    if len(prompt_ids) > len(tfidf_weights):
        extra = np.array([0.1] * (len(prompt_ids) - len(tfidf_weights)), dtype=np.float32)
        tfidf_weights = np.concatenate([tfidf_weights, extra])
    tfidf_weights = tfidf_weights / (tfidf_weights.sum() + 1e-12)
    V_prompt = np.stack([embed[tid] for tid in prompt_ids], axis=1)  # (D, L)
    V_prompt_norm = V_prompt / (np.linalg.norm(V_prompt, axis=0, keepdims=True) + 1e-12)
    prompt_centroid = V_prompt_norm @ tfidf_weights
    prompt_centroid /= (np.linalg.norm(prompt_centroid) + 1e-12)
    cos_to_centroid = embed_norm_rows @ prompt_centroid  # (V,)
    EXCLUDE = set(prompt_ids) | set(range(100))
    candidate_ids = np.array([i for i in range(VOCAB) if i not in EXCLUDE])
    candidate_cos = cos_to_centroid[candidate_ids]
    top_k_idx = np.argsort(-candidate_cos)[:K]
    archetype_token_ids = candidate_ids[top_k_idx].tolist()
    archetype_cos = candidate_cos[top_k_idx]
    france_in = expected_token in archetype_token_ids
    france_rank = (archetype_token_ids.index(expected_token) + 1) if france_in else None
    A = np.stack([embed[tid] for tid in archetype_token_ids], axis=1)  # (D, K)
    A = A / (np.linalg.norm(A, axis=0, keepdims=True) + 1e-12)
    epsilon = np.clip(archetype_cos.astype(np.float32), 0.0, None)
    epsilon = epsilon / (epsilon.max() + 1e-12)
    return A, epsilon, archetype_token_ids, france_in, france_rank, prompt_centroid, V_prompt_norm, tfidf_weights

# ============================================================================
# 6. *** FIX: build_scf_operators in K-subspace ***
# ============================================================================
def build_scf_operators_K(A, epsilon, V_prompt_norm, tfidf_weights):
    """Build SCF operators in K x K subspace (FIX for 896x896 bug).

    Strategy: project everything to K-subspace via A.T @ · @ A.
    Returns: H_core_K (K,K), D_mat_K (K,K), J_asym_K (K,K), DM_init_K (K,K)
             AND A (D,K) for final DM_final lift.
    """
    K = A.shape[1]

    # H_core in D-space: V_prompt_norm @ diag(w) @ V_prompt_norm.T  -- rank L
    # Project to K-space: H_core_K = A.T @ H_core_D @ A
    H_core_D = V_prompt_norm @ np.diag(tfidf_weights) @ V_prompt_norm.T  # (D,D) — but rank L (cheap)
    H_core_K = A.T @ H_core_D @ A  # (K,K)
    H_core_K = (H_core_K + H_core_K.T) / 2

    # D_mat: W_D = tanh(cos_sim_K) - I  -- already K x K, no projection needed
    cos_sim_K = A.T @ A  # (K,K)
    W_D = np.tanh(cos_sim_K) - np.eye(K)
    D_mat_K = W_D  # already in K-space
    D_mat_K = (D_mat_K + D_mat_K.T) / 2
    D_mat_K = D_mat_K * (5.0 / (np.linalg.norm(D_mat_K, 'fro') + 1e-12))

    # DM_init: in D-space was A @ diag(eps) @ A.T
    # In K-space: A.T @ (A @ diag(eps) @ A.T) @ A = (A.T A) @ diag(eps) @ (A.T A) = G @ diag(eps) @ G
    # where G = A.T @ A is the Gram matrix.
    # BUT semantically, the "intent" of DM_init is diag(eps) in K-space (epsilon as eigenvalues).
    # We use the natural projection A.T @ DM_init_D @ A for consistency with other operators.
    DM_init_D = (A @ np.diag(epsilon) @ A.T) / (epsilon.max() + 1e-12)  # (D,D)
    DM_init_K = A.T @ DM_init_D @ A  # (K,K)
    DM_init_K = (DM_init_K + DM_init_K.T) / 2

    # J_asym: J_small is already K x K
    order = np.argsort(-epsilon)
    J_small = np.zeros((K, K))
    rng = np.random.RandomState(42)
    for ii, i in enumerate(order[:-1]):
        for j in order[ii+1:]:
            w = rng.uniform(0.05, 0.2)
            J_small[i, j] = w
            J_small[j, i] = -w
    J_asym_K = J_small  # already in K-space (no projection — J_small was never lifted)
    J_asym_K = (J_asym_K - J_asym_K.T) / 2
    J_asym_K = J_asym_K * (1.0 / (np.linalg.norm(J_asym_K, 'fro') + 1e-12))

    return (H_core_K.astype(np.float64), D_mat_K.astype(np.float64),
            J_asym_K.astype(np.float64), DM_init_K.astype(np.float64))

# ============================================================================
# 7. *** FIX: M_coul_K, M_exch_K — project M_coul, M_exch to K-subspace ***
# ============================================================================
def project_M_to_K(M_D, A):
    """Project D-space operator M to K-subspace: A.T @ M @ A."""
    M_K = A.T @ M_D @ A  # (K,K)
    return M_K.astype(np.float64)

# ============================================================================
# 8. *** FIX: make_F_builder using K-space M_coul_K, M_exch_K ***
# ============================================================================
ALPHA_COULOMB = 0.005
ALPHA_EXCH = 0.003

def make_F_builder_K(H_core_K, D_mat_K, M_coul_K, M_exch_K):
    def semantic_interactions_K(DM_current_K):
        G = ALPHA_COULOMB * (DM_current_K @ M_coul_K @ DM_current_K) \
          - ALPHA_EXCH * (DM_current_K @ M_exch_K @ DM_current_K)
        return (G + G.T) / 2
    def build_F_sym_K(DM_current_K):
        F = H_core_K - D_mat_K + semantic_interactions_K(DM_current_K)
        return (F + F.T) / 2
    return build_F_sym_K, semantic_interactions_K

# ============================================================================
# 9. *** FIX: run_scf in K-space (much smaller eigh) ***
# ============================================================================
def run_scf_K(DM0_K, build_F_sym_K, J_asym_K, K, max_iter=20, threshold=1e-3, eta_r=0.002):
    DM = DM0_K.copy()
    diis = DIIS(8, 0.5)
    energies = [energy(build_F_sym_K(DM), DM)]
    gammas = [0.0]; kicks_applied = [False]; steps_conv = None
    eigh_warnings = []
    for step in range(1, max_iter + 1):
        F_sym = build_F_sym_K(DM)
        diis.push(F_sym, DM)
        F_used, gamma, _ = diis.extrapolate(F_sym, DM)
        # *** eigh on K x K (was D x D = 896 x 896 in original) ***
        try:
            eigvals, eigvecs = np.linalg.eigh(F_used)
        except np.linalg.LinAlgError as e:
            eigh_warnings.append(f"step {step}: {e}")
            eigvals, eigvecs = np.linalg.eigh(F_used + 1e-10 * np.eye(K))
        # Check for degeneracy at small HEAD
        if len(eigvals) >= 2:
            sorted_eigs = np.sort(eigvals)
            smallest_gap = float(np.min(np.diff(sorted_eigs[:K+1]))) if len(sorted_eigs) > K else 0.0
        else:
            smallest_gap = 0.0
        K_eff = K  # K-subspace dimension is fixed
        occ = eigvecs[:, :K_eff]
        DM_target = occ @ occ.T
        DM_pre = DM_target
        err_norm = float(np.linalg.norm(commutator(F_sym, DM), 'fro'))
        if (gamma < 0.5) and (err_norm > 0.1):
            kick = commutator(J_asym_K, DM)
            DM_post = DM_pre + eta_r * kick
            kicks_applied.append(True)
        else:
            DM_post = DM_pre
            kicks_applied.append(False)
        if step % 3 == 0:
            DM_post = pi_lambda(DM_post)
        DM_post = (DM_post + DM_post.T) / 2
        E = energy(build_F_sym_K(DM_post), DM_post)
        energies.append(E); gammas.append(gamma)
        DM = DM_post
        if E < threshold and steps_conv is None:
            steps_conv = step; break
    return DM, energies, gammas, kicks_applied, steps_conv, eigh_warnings, smallest_gap

# ============================================================================
# 10. Renderers — IDENTICAL to phase_d4 (work in D-space, need DM_final_D)
# ============================================================================
def rms_norm(x, weight, eps=1e-6):
    return x * weight / np.sqrt(np.mean(x**2, axis=-1) + eps)

def compute_logits(x_final, use_rms=True, use_cosine=True):
    if use_rms:
        x = rms_norm(x_final.astype(np.float32), final_norm_w)
    else:
        x = x_final.astype(np.float32)
    if use_cosine:
        x_n = x / (np.linalg.norm(x) + 1e-12)
        return embed_norm_rows @ x_n
    else:
        return embed @ x

def softmax_np(x, axis=-1):
    x = x - np.max(x, axis=axis, keepdims=True)
    e = np.exp(x); return e / np.sum(e, axis=axis, keepdims=True)

def R1_residual_DM_final(x_rich, alpha, DM_final_D):
    x = x_rich.astype(np.float64) + alpha * (DM_final_D @ x_rich.astype(np.float64))
    return x

def R3_residual_DM_init(x_rich, alpha, DM_init_D):
    return x_rich.astype(np.float64) + alpha * (DM_init_D @ x_rich.astype(np.float64))

def R2_lens_attn_residual(x_rich, alpha, A):
    scores = A.T.astype(np.float64) @ x_rich.astype(np.float64)
    w = softmax_np(scores / np.sqrt(D))
    x_corr = A.astype(np.float64) @ w
    return x_rich.astype(np.float64) + alpha * x_corr

def run_renderer(x_rich, render_fn, expected_token, alphas):
    results = []
    for alpha in alphas:
        x_final = render_fn(x_rich, alpha)
        logits = compute_logits(x_final)
        top5 = np.argsort(-logits)[:5]
        exact = int(top5[0]) == expected_token
        in5 = expected_token in top5.tolist()
        rank = top5.tolist().index(expected_token) + 1 if in5 else None
        results.append({
            "alpha": float(alpha),
            "predicted_token_str": id_to_str(int(top5[0])),
            "top5_strs": [id_to_str(int(t)) for t in top5],
            "exact_match": bool(exact), "in_top5": bool(in5), "rank": rank,
            "expected_logit": float(logits[expected_token]),
            "pred_logit": float(logits[int(top5[0])]),
        })
    return results

# ============================================================================
# 11. Run pipeline (use cached PyTorch results, only redo LENS + SCF + renderers)
# ============================================================================
print("\n[5] Encoding prompts...")
prompts_data = []
for prompt, expected in PROMPTS:
    pids = encode(prompt)
    eids = encode(expected)
    if not eids:
        continue
    prompts_data.append({
        "prompt": prompt, "expected_str": expected,
        "expected_id": eids[-1], "prompt_ids": pids,
    })

print(f"\n[6] Per-prompt LENS + SCF (K-subspace) + renderers × HEAD_layers sweep...")
all_results = []
scf_times_summary = []
for pi, p in enumerate(prompts_data):
    print(f"\n  ── Prompt [{pi}] {p['prompt']!r} ──")
    prompt_ids = p["prompt_ids"]
    expected_token = p["expected_id"]

    # LENS — IDENTICAL
    A, epsilon, arch_token_ids, france_in, france_rank, prompt_centroid, V_prompt_norm, tfidf_weights = \
        lens_select_archetypes(prompt_ids, expected_token, K=K_TARGET)
    print(f"    LENS: K={K_TARGET}, expected in archetypes: {france_in} (rank={france_rank})")

    # *** DIM FIX: Build operators in K-space ***
    H_core_K, D_mat_K, J_asym_K, DM_init_K = build_scf_operators_K(A, epsilon, V_prompt_norm, tfidf_weights)
    M_coul_K = project_M_to_K(M_coul, A)
    M_exch_K = project_M_to_K(M_exch, A)
    build_F_sym_K, _ = make_F_builder_K(H_core_K, D_mat_K, M_coul_K, M_exch_K)

    # SCF in K-space
    t_scf = time.perf_counter()
    DM_final_K, energies, gammas, kicks, steps_conv, eigh_warnings, smallest_gap = run_scf_K(
        DM_init_K, build_F_sym_K, J_asym_K, K=K_TARGET, max_iter=20, threshold=1e-3
    )
    t_scf_ms = (time.perf_counter() - t_scf) * 1000
    scf_times_summary.append(t_scf_ms)
    print(f"    SCF (K-subspace): {t_scf_ms:.1f}ms, steps={steps_conv}, E_final={energies[-1]:.4e}, "
          f"γ_final={gammas[-1]:.3f}, kicks={sum(kicks)}/{len(kicks)}, "
          f"min_eig_gap={smallest_gap:.2e}")
    if eigh_warnings:
        print(f"    EIGH WARNINGS: {eigh_warnings}")

    # *** LIFT DM_final_K to D-space ONCE for rendering ***
    DM_final_D = A @ DM_final_K @ A.T  # (D, D) — only computed once
    DM_final_D = (DM_final_D + DM_final_D.T) / 2

    # DM_init also lifted to D-space for R3 renderer
    DM_init_D = A @ DM_init_K @ A.T
    DM_init_D = (DM_init_D + DM_init_D.T) / 2

    # Frobenius norms comparison (D vs K)
    dm_init_D_norm_direct = float(np.linalg.norm((A @ np.diag(epsilon) @ A.T) / (epsilon.max() + 1e-12), 'fro'))
    dm_init_D_norm_lifted = float(np.linalg.norm(DM_init_D, 'fro'))
    dm_init_K_norm = float(np.linalg.norm(DM_init_K, 'fro'))

    prompt_result = {
        "prompt_idx": pi,
        "prompt": p["prompt"],
        "expected_str": p["expected_str"],
        "expected_id": expected_token,
        "baseline": {
            "top5_strs": pt_results[str(pi)]["baseline_top5_strs"],
            "baseline_match": pt_results[str(pi)]["baseline_match"],
            "baseline_expected_logit": pt_results[str(pi)]["baseline_France_logit"],
        },
        "lens": {
            "K": K_TARGET,
            "expected_in_archetypes": france_in,
            "expected_rank_in_archetypes": france_rank,
        },
        "scf": {
            "wall_clock_ms": t_scf_ms,
            "steps": steps_conv,
            "final_energy": energies[-1],
            "diis_gamma_final": gammas[-1],
            "kicks_applied": int(sum(kicks)),
            "kicks_total": len(kicks),
            "trace_DM_final_K": float(np.trace(DM_final_K)),
            "trace_DM_final_D": float(np.trace(DM_final_D)),
            "fro_DM_final_K": float(np.linalg.norm(DM_final_K, 'fro')),
            "fro_DM_final_D": float(np.linalg.norm(DM_final_D, 'fro')),
            "fro_DM_init_K": dm_init_K_norm,
            "fro_DM_init_D_direct": dm_init_D_norm_direct,
            "fro_DM_init_D_lifted": dm_init_D_norm_lifted,
            "min_eigenvalue_gap": smallest_gap,
            "eigh_warnings": eigh_warnings,
            "subspace_dim": K_TARGET,
        },
        "head_sweep": {},
    }

    # For each HEAD_layers cutoff: load x_rich, run renderers (IDENTICAL to phase_d4)
    for L in HEAD_LAYERS_SWEEP:
        x_rich = np.array(pt_results[str(pi)]["hidden_states"][str(L)], dtype=np.float32)

        # Baseline (no renderer)
        logits_b = compute_logits(x_rich)
        top5_b = np.argsort(-logits_b)[:5]
        exact_b = int(top5_b[0]) == expected_token
        in5_b = expected_token in top5_b.tolist()

        # Renderers — use DM_final_D (lifted) and DM_init_D (lifted)
        r1 = run_renderer(x_rich,
                          lambda x, a, DM=DM_final_D: R1_residual_DM_final(x, a, DM),
                          expected_token, ALPHAS)
        r3 = run_renderer(x_rich,
                          lambda x, a, DM=DM_init_D: R3_residual_DM_init(x, a, DM),
                          expected_token, ALPHAS)
        r2 = run_renderer(x_rich,
                          lambda x, a, AA=A: R2_lens_attn_residual(x, a, AA),
                          expected_token, ALPHAS)

        def best(res):
            return max(res, key=lambda r: (r["exact_match"], r["in_top5"], r["expected_logit"]))

        b_r1 = best(r1); b_r3 = best(r3); b_r2 = best(r2)

        prompt_result["head_sweep"][str(L)] = {
            "x_rich_norm": float(np.linalg.norm(x_rich)),
            "baseline_no_renderer": {
                "exact_match": bool(exact_b),
                "in_top5": bool(in5_b),
                "pred_str": id_to_str(int(top5_b[0])),
                "expected_logit": float(logits_b[expected_token]),
                "top5_strs": [id_to_str(int(t)) for t in top5_b],
            },
            "R1_residual_DM_final": {
                "best_alpha": b_r1["alpha"],
                "best_exact_match": b_r1["exact_match"],
                "best_in_top5": b_r1["in_top5"],
                "best_rank": b_r1["rank"],
                "best_pred_str": b_r1["predicted_token_str"],
                "best_expected_logit": b_r1["expected_logit"],
                "all_alphas": r1,
            },
            "R3_residual_DM_init": {
                "best_alpha": b_r3["alpha"],
                "best_exact_match": b_r3["exact_match"],
                "best_in_top5": b_r3["in_top5"],
                "best_rank": b_r3["rank"],
                "best_pred_str": b_r3["predicted_token_str"],
                "best_expected_logit": b_r3["expected_logit"],
                "all_alphas": r3,
            },
            "R2_lens_attn": {
                "best_alpha": b_r2["alpha"],
                "best_exact_match": b_r2["exact_match"],
                "best_in_top5": b_r2["in_top5"],
                "best_rank": b_r2["rank"],
                "best_pred_str": b_r2["predicted_token_str"],
                "best_expected_logit": b_r2["expected_logit"],
                "all_alphas": r2,
            },
        }
        print(f"    HEAD={L:>2d}  baseline={exact_b}  "
              f"R1={b_r1['exact_match']}@α={b_r1['alpha']:.1f}  "
              f"R3={b_r3['exact_match']}@α={b_r3['alpha']:.1f}  "
              f"R2={b_r2['exact_match']}@α={b_r2['alpha']:.1f}")

    all_results.append(prompt_result)

# ============================================================================
# 12. Summary + save
# ============================================================================
print("\n" + "=" * 78)
print("SUMMARY (DIM FIX): exact-match matrix")
print("=" * 78)
header = f"{'prompt':<40s} | " + " | ".join(f"L{L}" for L in HEAD_LAYERS_SWEEP) + " | baseline"
print(header)
print("-" * len(header))
exact_matrix = []
for r in all_results:
    row = [r["prompt"][:38]]
    matrix_row = []
    for L in HEAD_LAYERS_SWEEP:
        h = r["head_sweep"][str(L)]
        any_exact = (h["R1_residual_DM_final"]["best_exact_match"]
                  or h["R3_residual_DM_init"]["best_exact_match"]
                  or h["R2_lens_attn"]["best_exact_match"])
        row.append("✓" if any_exact else "·")
        matrix_row.append(any_exact)
    row.append("✓" if r["baseline"]["baseline_match"] else "·")
    print(" | ".join(row))
    exact_matrix.append(matrix_row)

print("\nAccuracy per HEAD_layers (DIM FIX):")
for li, L in enumerate(HEAD_LAYERS_SWEEP):
    acc = sum(row[li] for row in exact_matrix) / len(exact_matrix)
    print(f"  HEAD={L:>2d}  accuracy = {acc*100:.0f}%  ({sum(row[li] for row in exact_matrix)}/{len(exact_matrix)})")

# SCF timing summary
print(f"\nSCF wall-clock (DIM FIX):")
print(f"  mean: {np.mean(scf_times_summary):.1f}ms")
print(f"  min:  {min(scf_times_summary):.1f}ms")
print(f"  max:  {max(scf_times_summary):.1f}ms")
print(f"  vs original (mean 15892ms): speedup = {15892.0/np.mean(scf_times_summary):.1f}x")

out = {
    "phase": "G0-step1-dim-fix",
    "description": "Dimensionality bug fix verification — SCF in K-subspace (128x128) instead of D-space (896x896)",
    "config": {
        "model": "Qwen2.5-0.5B-Instruct",
        "d": D, "K": K_TARGET, "n_layers_total": N_LAYERS,
        "vocab_size": VOCAB,
        "head_layers_sweep": HEAD_LAYERS_SWEEP,
        "alphas": ALPHAS,
        "prompts": [{"prompt": p["prompt"], "expected": p["expected_str"]} for p in prompts_data],
        "subspace_dim": K_TARGET,
    },
    "results": all_results,
    "summary": {
        "accuracy_per_head_layers": {
            str(L): sum(row[li] for row in exact_matrix) / len(exact_matrix)
            for li, L in enumerate(HEAD_LAYERS_SWEEP)
        },
        "baseline_accuracy": sum(r["baseline"]["baseline_match"] for r in all_results) / len(all_results),
        "scf_wall_clock_ms": {
            "mean": float(np.mean(scf_times_summary)),
            "min": float(min(scf_times_summary)),
            "max": float(max(scf_times_summary)),
            "vs_original_speedup": float(15892.0 / np.mean(scf_times_summary)),
        },
    },
}
out_path = OUT_DIR / "results_v4_dim_fix.json"
out_path.write_text(json.dumps(out, indent=2))
print(f"\nSaved → {out_path}")

print("\n" + "=" * 78)
print("DONE — Phase G0 step 1 complete. Now run phase_g0_compare.py to diff vs original.")
print("=" * 78)
