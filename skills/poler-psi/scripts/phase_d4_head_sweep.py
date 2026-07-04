#!/usr/bin/env /usr/bin/python3
"""
phase_d4_head_sweep.py
======================
Phase D v4 — HEAD layer sweep × multi-prompt validation.

Answers two open questions from D-v3:
  D2: How many HEAD layers do we actually need?  (4 → 8 → 12 → 16 → 20 → 24)
  D5: Is the Paris/France victory a fluke?  (validate on 10 prompts)

Architecture per (prompt, n_head_layers):
  1. PyTorch Qwen2.5-0.5B forward (one pass per prompt, hooks at 6 cutoff layers)
  2. LENS: K=128 archetype selection from prompt embeddings (TF-IDF centroid)
  3. POLER SCF (numpy float64, max_iter=20)  — computed ONCE per prompt, reused
  4. Renderers (R1=residual DM_final, R3=residual DM_init) with α-sweep
  5. RMS norm + cosine logits → top-5 tokens → exact-match check vs PyTorch 24L baseline

Output:
  /home/z/my-project/work/phase_d/results_v4.json
  /home/z/my-project/work/phase_d/head_sweep_v4.png
  /home/z/my-project/work/phase_d/wall_clock_v4.png
"""
from __future__ import annotations
import json, time, sys, os, subprocess, types
from pathlib import Path
import numpy as np
from safetensors import safe_open
from tokenizers import Tokenizer
import ml_dtypes

OUT_DIR = Path("/home/z/my-project/work/phase_d")
OUT_DIR.mkdir(parents=True, exist_ok=True)
QWEN_DIR = Path("/home/z/my-project/work/qwen-text")
WEIGHTS_PATH = QWEN_DIR / "model.safetensors"
TOKENIZER_PATH = QWEN_DIR / "tokenizer.json"

D = 896
N_LAYERS = 24
VOCAB = 151936
K_TARGET = 128
HEAD_LAYERS_SWEEP = [4, 8, 12, 16, 20, 24]
ALPHAS = [0.0, 0.1, 0.3, 0.5, 0.7, 1.0, 1.5, 2.0, 3.0]

# 10 prompts — simple factual, expected single-token continuation.
# Token forms (Ġ-prefixed for words after space) verified at runtime.
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
print("Phase D v4 — HEAD layer sweep × multi-prompt validation")
print("=" * 78)

# ═══════════════════════════════════════════════════════════════════════════
# 1. Load Qwen weights (numpy)
# ═══════════════════════════════════════════════════════════════════════════
print("\n[1] Loading Qwen weights (numpy)...")
t0 = time.perf_counter()
WEIGHTS = {}
with safe_open(str(WEIGHTS_PATH), framework="numpy") as f:
    for k in f.keys():
        WEIGHTS[k] = f.get_tensor(k).astype(np.float32)
print(f"  {len(WEIGHTS)} tensors in {time.perf_counter()-t0:.2f}s")

embed = WEIGHTS["model.embed_tokens.weight"].astype(np.float32)
final_norm_w = WEIGHTS["model.norm.weight"].astype(np.float32)
embed_norm_rows = embed / (np.linalg.norm(embed, axis=1, keepdims=True) + 1e-12)

print("\n[2] Tokenizer...")
tokenizer = Tokenizer.from_file(str(TOKENIZER_PATH))
def encode(text): return tokenizer.encode(text).ids
def id_to_str(i): return tokenizer.id_to_token(int(i))

# ═══════════════════════════════════════════════════════════════════════════
# 2. Operators from Qwen weights (computed once)
# ═══════════════════════════════════════════════════════════════════════════
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

# ═══════════════════════════════════════════════════════════════════════════
# 3. SCF machinery (DIIS + PulayKick + McWeeny)
# ═══════════════════════════════════════════════════════════════════════════
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

# ═══════════════════════════════════════════════════════════════════════════
# 4. PyTorch multi-cutoff forward (one forward pass, hooks at 6 layers)
# ═══════════════════════════════════════════════════════════════════════════

PT_FORWARD_SCRIPT = OUT_DIR / "_pt_multicutoff_forward.py"

def run_pt_forward_all_prompts(prompts_with_expected):
    """One PyTorch subprocess that handles ALL prompts.
    For each prompt:
      - run full 24-layer forward (golden baseline top-1)
      - extract hidden state at cutoff layers {4,8,12,16,20,24}
    Returns dict: prompt_idx → {baseline_top5, baseline_match, hidden_states: {layer: x_rich}}
    """
    payload = {
        "model_dir": str(QWEN_DIR),
        "prompts": [p for p, _ in prompts_with_expected],
        "expected_tokens": [e for _, e in prompts_with_expected],
        "cutoff_layers": HEAD_LAYERS_SWEEP,
        "out_path": str(OUT_DIR / "_pt_multicutoff_result.json"),
        "vocab": VOCAB,
        "d": D,
    }
    PT_FORWARD_SCRIPT.write_text(
        "import json, time, types\n"
        "from pathlib import Path\n"
        "import numpy as np\n"
        "import torch, torch.nn as nn\n"
        "from transformers import AutoModelForCausalLM, AutoTokenizer\n"
        f"PAYLOAD = {payload!r}\n"
        "torch.manual_seed(42)\n"
        "tok = AutoTokenizer.from_pretrained(PAYLOAD['model_dir'])\n"
        "model = AutoModelForCausalLM.from_pretrained(PAYLOAD['model_dir'], torch_dtype=torch.float32)\n"
        "model.eval()\n"
        "out = {}\n"
        "for pi, (prompt, expected) in enumerate(zip(PAYLOAD['prompts'], PAYLOAD['expected_tokens'])):\n"
        "    ids = tok(prompt, return_tensors='pt').input_ids\n"
        "    # Forward with hidden_states returned\n"
        "    with torch.no_grad():\n"
        "        res = model(ids, output_hidden_states=True, use_cache=False)\n"
        "    logits_full = res.logits[0, -1].numpy()  # (V,)\n"
        "    top5 = np.argsort(-logits_full)[:5]\n"
        "    # hidden_states is tuple of (n_layers+1): [embed, layer1_out, ..., layer24_out]\n"
        "    hs = res.hidden_states  # tuple of tensors\n"
        "    hidden_at = {}\n"
        "    for L in PAYLOAD['cutoff_layers']:\n"
        "        # hidden_states[L] is output after layer L (0-indexed: hs[0]=embed, hs[1]=after L1, ..., hs[L]=after layer L)\n"
        "        x = hs[L][0, -1].numpy()  # (D,)\n"
        "        hidden_at[str(L)] = x.tolist()\n"
        "    out[str(pi)] = {\n"
        "        'baseline_top5_ids': top5.tolist(),\n"
        "        'baseline_top5_strs': [tok.decode([int(i)]) for i in top5],\n"
        "        'baseline_match': int(top5[0]) == tok(expected).input_ids[-1] if tok(expected).input_ids else False,\n"
        "        'baseline_expected_id': tok(expected).input_ids[-1] if tok(expected).input_ids else -1,\n"
        "        'baseline_France_logit': float(logits_full[tok(expected).input_ids[-1]]) if tok(expected).input_ids else 0.0,\n"
        "        'hidden_states': hidden_at,\n"
        "        'prompt': prompt,\n"
        "        'expected': expected,\n"
        "        'n_tokens': int(ids.shape[1]),\n"
        "    }\n"
        "Path(PAYLOAD['out_path']).write_text(json.dumps(out))\n"
        "print(f'  [pt] processed {len(out)} prompts', flush=True)\n",
        encoding="utf-8"
    )
    t0 = time.perf_counter()
    proc = subprocess.run(["/usr/bin/python3.13", str(PT_FORWARD_SCRIPT)],
                          capture_output=True, text=True, timeout=900)
    dt = time.perf_counter() - t0
    print(f"  PyTorch forward (10 prompts × 24 layers): {dt:.1f}s")
    if proc.returncode != 0:
        print(f"  stderr: {proc.stderr[-1500:]}")
        sys.exit(1)
    print(proc.stdout)
    return json.loads((OUT_DIR / "_pt_multicutoff_result.json").read_text())

# ═══════════════════════════════════════════════════════════════════════════
# 5. Per-prompt LENS + SCF (numpy)
# ═══════════════════════════════════════════════════════════════════════════

def lens_select_archetypes(prompt_ids, expected_token, K=K_TARGET):
    """Return A (D,K), epsilon (K,), france_in_archetypes, france_rank."""
    tfidf_weights = np.array([1.0, 0.2, 0.3, 0.8, 0.4], dtype=np.float32)[:len(prompt_ids)]
    if len(prompt_ids) > len(tfidf_weights):
        # extend weights with decaying values
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

def build_scf_operators(A, epsilon, V_prompt_norm, tfidf_weights):
    """Build H_core, D_mat, J_asym, DM_init in float64 for SCF."""
    K = A.shape[1]
    H_core = V_prompt_norm @ np.diag(tfidf_weights) @ V_prompt_norm.T
    H_core = (H_core + H_core.T) / 2
    cos_sim_K = A.T @ A
    W_D = np.tanh(cos_sim_K) - np.eye(K)
    D_mat = A @ W_D @ A.T
    D_mat = (D_mat + D_mat.T) / 2
    D_mat = D_mat * (5.0 / (np.linalg.norm(D_mat, 'fro') + 1e-12))
    DM_init = (A @ np.diag(epsilon) @ A.T) / (epsilon.max() + 1e-12)
    DM_init = (DM_init + DM_init.T) / 2
    order = np.argsort(-epsilon)
    J_small = np.zeros((K, K))
    rng = np.random.RandomState(42)
    for ii, i in enumerate(order[:-1]):
        for j in order[ii+1:]:
            w = rng.uniform(0.05, 0.2)
            J_small[i, j] = w
            J_small[j, i] = -w
    J_asym = A @ J_small @ A.T
    J_asym = (J_asym - J_asym.T) / 2
    J_asym = J_asym * (1.0 / (np.linalg.norm(J_asym, 'fro') + 1e-12))
    return (H_core.astype(np.float64), D_mat.astype(np.float64),
            J_asym.astype(np.float64), DM_init.astype(np.float64))

ALPHA_COULOMB = 0.005
ALPHA_EXCH = 0.003

def make_F_builder(H_core, D_mat):
    def semantic_interactions(DM_current):
        G = ALPHA_COULOMB * (DM_current @ M_coul @ DM_current) \
          - ALPHA_EXCH * (DM_current @ M_exch @ DM_current)
        return (G + G.T) / 2
    def build_F_sym(DM_current):
        F = H_core - D_mat + semantic_interactions(DM_current)
        return (F + F.T) / 2
    return build_F_sym, semantic_interactions

def run_scf(DM0, build_F_sym, J_asym, K, max_iter=20, threshold=1e-3, eta_r=0.002):
    DM = DM0.copy()
    diis = DIIS(8, 0.5)
    energies = [energy(build_F_sym(DM), DM)]
    gammas = [0.0]; kicks_applied = [False]; steps_conv = None
    for step in range(1, max_iter + 1):
        F_sym = build_F_sym(DM)
        diis.push(F_sym, DM)
        F_used, gamma, _ = diis.extrapolate(F_sym, DM)
        eigvals, eigvecs = np.linalg.eigh(F_used)
        K_eff = min(K, D)
        occ = eigvecs[:, :K_eff]
        DM_target = occ @ occ.T
        DM_pre = DM_target  # alpha_mix=1.0
        err_norm = float(np.linalg.norm(commutator(F_sym, DM), 'fro'))
        if (gamma < 0.5) and (err_norm > 0.1):
            kick = commutator(J_asym, DM)
            DM_post = DM_pre + eta_r * kick
            kicks_applied.append(True)
        else:
            DM_post = DM_pre
            kicks_applied.append(False)
        if step % 3 == 0:
            DM_post = pi_lambda(DM_post)
        DM_post = (DM_post + DM_post.T) / 2
        E = energy(build_F_sym(DM_post), DM_post)
        energies.append(E); gammas.append(gamma)
        DM = DM_post
        if E < threshold and steps_conv is None:
            steps_conv = step; break
    return DM, energies, gammas, kicks_applied, steps_conv

# ═══════════════════════════════════════════════════════════════════════════
# 6. Renderers
# ═══════════════════════════════════════════════════════════════════════════

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

def R1_residual_DM_final(x_rich, alpha, DM_final):
    x = x_rich.astype(np.float64) + alpha * (DM_final @ x_rich.astype(np.float64))
    return x

def R3_residual_DM_init(x_rich, alpha, DM_init_f64):
    return x_rich.astype(np.float64) + alpha * (DM_init_f64 @ x_rich.astype(np.float64))

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

# ═══════════════════════════════════════════════════════════════════════════
# 7. RUN PIPELINE
# ═══════════════════════════════════════════════════════════════════════════

print("\n[4] Encoding prompts + expected tokens...")
prompts_data = []
for prompt, expected in PROMPTS:
    pids = encode(prompt)
    eids = encode(expected)
    if not eids:
        print(f"  WARN: expected token {expected!r} encodes to empty, skipping")
        continue
    prompts_data.append({
        "prompt": prompt,
        "expected_str": expected,
        "expected_id": eids[-1],
        "prompt_ids": pids,
    })
    print(f"  {prompt!r:38s} → {expected!r:14s} (id={eids[-1]}, prompt_len={len(pids)})")

print(f"\n[5] PyTorch forward pass (golden baseline + hidden states at {HEAD_LAYERS_SWEEP})...")
pt_results = run_pt_forward_all_prompts([(p["prompt"], p["expected_str"]) for p in prompts_data])
print(f"  PyTorch golden baseline results:")
for pi, p in enumerate(prompts_data):
    r = pt_results[str(pi)]
    match = "✓ EXACT" if r["baseline_match"] else "✗"
    print(f"    [{pi}] {p['prompt']!r:38s} → pred={r['baseline_top5_strs'][0]!r:14s} "
          f"expected={p['expected_str']!r:14s} {match}")

# For each prompt: LENS + SCF (once), then renderers at each HEAD_layers cutoff
print(f"\n[6] Per-prompt LENS + SCF + renderers × HEAD_layers sweep...")
all_results = []
for pi, p in enumerate(prompts_data):
    print(f"\n  ── Prompt [{pi}] {p['prompt']!r} ──")
    prompt_ids = p["prompt_ids"]
    expected_token = p["expected_id"]

    # LENS
    A, epsilon, arch_token_ids, france_in, france_rank, prompt_centroid, V_prompt_norm, tfidf_weights = \
        lens_select_archetypes(prompt_ids, expected_token, K=K_TARGET)
    print(f"    LENS: K={K_TARGET}, expected in archetypes: {france_in} (rank={france_rank})")

    # SCF operators + run
    H_core, D_mat, J_asym, DM_init = build_scf_operators(A, epsilon, V_prompt_norm, tfidf_weights)
    build_F_sym, _ = make_F_builder(H_core, D_mat)
    t_scf = time.perf_counter()
    DM_final, energies, gammas, kicks, steps_conv = run_scf(
        DM_init, build_F_sym, J_asym, K=K_TARGET, max_iter=20, threshold=1e-3
    )
    t_scf_ms = (time.perf_counter() - t_scf) * 1000
    print(f"    SCF: {t_scf_ms:.0f}ms, steps={steps_conv}, E_final={energies[-1]:.4e}, "
          f"γ_final={gammas[-1]:.3f}, kicks={sum(kicks)}/{len(kicks)}")

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
            "trace_DM_final": float(np.trace(DM_final)),
        },
        "head_sweep": {},
    }

    # For each HEAD_layers cutoff: load x_rich, run renderers
    for L in HEAD_LAYERS_SWEEP:
        x_rich = np.array(pt_results[str(pi)]["hidden_states"][str(L)], dtype=np.float32)

        # Baseline (no renderer, just RMS + cosine)
        logits_b = compute_logits(x_rich)
        top5_b = np.argsort(-logits_b)[:5]
        exact_b = int(top5_b[0]) == expected_token
        in5_b = expected_token in top5_b.tolist()

        # Renderers
        r1 = run_renderer(x_rich,
                          lambda x, a, DM=DM_final: R1_residual_DM_final(x, a, DM),
                          expected_token, ALPHAS)
        r3 = run_renderer(x_rich,
                          lambda x, a, DM=DM_init: R3_residual_DM_init(x, a, DM),
                          expected_token, ALPHAS)
        r2 = run_renderer(x_rich,
                          lambda x, a, AA=A: R2_lens_attn_residual(x, a, AA),
                          expected_token, ALPHAS)

        # Best per renderer
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

# ═══════════════════════════════════════════════════════════════════════════
# 8. Summary + save
# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 78)
print("SUMMARY: exact-match matrix (rows=prompts, cols=HEAD_layers)")
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
        # Combined: any of R1/R3/R2 exact?
        any_exact = (h["R1_residual_DM_final"]["best_exact_match"]
                  or h["R3_residual_DM_init"]["best_exact_match"]
                  or h["R2_lens_attn"]["best_exact_match"])
        row.append("✓" if any_exact else "·")
        matrix_row.append(any_exact)
    row.append("✓" if r["baseline"]["baseline_match"] else "·")
    print(" | ".join(row))
    exact_matrix.append(matrix_row)

# Accuracy per HEAD layer
print("\nAccuracy per HEAD_layers (any renderer exact match):")
for li, L in enumerate(HEAD_LAYERS_SWEEP):
    acc = sum(row[li] for row in exact_matrix) / len(exact_matrix)
    print(f"  HEAD={L:>2d}  accuracy = {acc*100:.0f}%  ({sum(row[li] for row in exact_matrix)}/{len(exact_matrix)})")

# Save
out = {
    "phase": "D-v4-head-sweep",
    "config": {
        "model": "Qwen2.5-0.5B-Instruct",
        "d": D, "K": K_TARGET, "n_layers_total": N_LAYERS,
        "vocab_size": VOCAB,
        "head_layers_sweep": HEAD_LAYERS_SWEEP,
        "alphas": ALPHAS,
        "prompts": [{"prompt": p["prompt"], "expected": p["expected_str"]} for p in prompts_data],
    },
    "results": all_results,
    "summary": {
        "accuracy_per_head_layers": {
            str(L): sum(row[li] for row in exact_matrix) / len(exact_matrix)
            for li, L in enumerate(HEAD_LAYERS_SWEEP)
        },
        "baseline_accuracy": sum(r["baseline"]["baseline_match"] for r in all_results) / len(all_results),
    },
}
(OUT_DIR / "results_v4.json").write_text(json.dumps(out, indent=2))
print(f"\nSaved → {OUT_DIR / 'results_v4.json'}")

# ═══════════════════════════════════════════════════════════════════════════
# 9. Plots
# ═══════════════════════════════════════════════════════════════════════════
import matplotlib.font_manager as fm
fm.fontManager.addfont('/usr/share/fonts/truetype/chinese/NotoSansSC-Regular.ttf')
fm.fontManager.addfont('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')
import matplotlib.pyplot as plt
plt.rcParams['font.sans-serif'] = ['Noto Sans SC', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

# Plot 1: accuracy vs HEAD_layers (per renderer)
fig, ax = plt.subplots(figsize=(10, 6), constrained_layout=True)
renderer_names = ["R1_residual_DM_final", "R3_residual_DM_init", "R2_lens_attn", "baseline_no_renderer"]
labels = ["R1: residual DM_final", "R3: residual DM_init", "R2: LENS-attn residual", "no renderer"]
colors = ["#d62728", "#2ca02c", "#1f77b4", "#7f7f7f"]
markers = ["o", "s", "^", "x"]
for rname, label, color, m in zip(renderer_names, labels, colors, markers):
    accs = []
    for L in HEAD_LAYERS_SWEEP:
        n_exact = sum(1 for r in all_results if r["head_sweep"][str(L)][rname]["best_exact_match"])
        accs.append(n_exact / len(all_results))
    ax.plot(HEAD_LAYERS_SWEEP, accs, marker=m, label=label, color=color, linewidth=2, markersize=8)
# Baseline (PyTorch 24L) horizontal
baseline_acc = sum(r["baseline"]["baseline_match"] for r in all_results) / len(all_results)
ax.axhline(baseline_acc, color="black", linestyle="--", alpha=0.5, label=f"PyTorch 24L baseline ({baseline_acc*100:.0f}%)")
ax.set_xlabel("HEAD layers (n)")
ax.set_ylabel("Exact-match accuracy (10 prompts)")
ax.set_title("Phase D v4: Exact-match accuracy vs HEAD depth\n(POLER SCF K=128 + residual renderers)")
ax.set_xticks(HEAD_LAYERS_SWEEP)
ax.set_ylim(-0.05, 1.05)
ax.grid(True, alpha=0.3)
ax.legend(loc="lower right")
plt.savefig(OUT_DIR / "head_sweep_v4.png", dpi=120)
print(f"Saved → {OUT_DIR / 'head_sweep_v4.png'}")

# Plot 2: wall-clock breakdown
fig, ax = plt.subplots(figsize=(10, 6), constrained_layout=True)
scf_times = [r["scf"]["wall_clock_ms"] for r in all_results]
prompt_labels = [f"[{r['prompt_idx']}] {r['prompt'][:30]}" for r in all_results]
x = np.arange(len(scf_times))
ax.bar(x, scf_times, color="#ff7f0e", label="POLER SCF (numpy float64, K=128)")
ax.set_xticks(x)
ax.set_xticklabels(prompt_labels, rotation=45, ha="right", fontsize=8)
ax.set_ylabel("Wall-clock (ms)")
ax.set_title("Phase D v4: SCF wall-clock per prompt\n(K=128, max_iter=20, threshold=1e-3)")
ax.axhline(np.mean(scf_times), color="black", linestyle="--", alpha=0.5,
           label=f"mean = {np.mean(scf_times):.0f}ms")
ax.legend()
ax.grid(True, alpha=0.3, axis="y")
plt.savefig(OUT_DIR / "wall_clock_v4.png", dpi=120)
print(f"Saved → {OUT_DIR / 'wall_clock_v4.png'}")

# Plot 3: α-sweep heatmaps for R1 and R3, averaged across prompts
fig, axes = plt.subplots(1, 2, figsize=(14, 5), constrained_layout=True)
for ax_i, (rname, title) in enumerate(zip(
    ["R1_residual_DM_final", "R3_residual_DM_init"],
    ["R1: residual DM_final", "R3: residual DM_init"]
)):
    # rows = HEAD_layers, cols = alphas
    matrix = np.zeros((len(HEAD_LAYERS_SWEEP), len(ALPHAS)))
    for li, L in enumerate(HEAD_LAYERS_SWEEP):
        for ai, alpha in enumerate(ALPHAS):
            n_exact = sum(
                1 for r in all_results
                if r["head_sweep"][str(L)][rname]["all_alphas"][ai]["exact_match"]
            )
            matrix[li, ai] = n_exact / len(all_results)
    im = axes[ax_i].imshow(matrix, aspect="auto", cmap="RdYlGn", vmin=0, vmax=1, origin="lower")
    axes[ax_i].set_xticks(range(len(ALPHAS))); axes[ax_i].set_xticklabels([f"{a:.1f}" for a in ALPHAS])
    axes[ax_i].set_yticks(range(len(HEAD_LAYERS_SWEEP))); axes[ax_i].set_yticklabels([f"L={L}" for L in HEAD_LAYERS_SWEEP])
    axes[ax_i].set_xlabel("α (residual strength)")
    axes[ax_i].set_ylabel("HEAD layers")
    axes[ax_i].set_title(title)
    # Annotate cells
    for li in range(len(HEAD_LAYERS_SWEEP)):
        for ai in range(len(ALPHAS)):
            v = matrix[li, ai]
            axes[ax_i].text(ai, li, f"{v*100:.0f}", ha="center", va="center",
                            color="black" if v > 0.3 else "white", fontsize=8)
    plt.colorbar(im, ax=axes[ax_i], label="accuracy")
fig.suptitle("Phase D v4: α-sweep × HEAD_layers (10 prompts)", fontsize=13)
plt.savefig(OUT_DIR / "alpha_sweep_v4.png", dpi=120)
print(f"Saved → {OUT_DIR / 'alpha_sweep_v4.png'}")

print("\n" + "=" * 78)
print("DONE — Phase D v4 complete")
print("=" * 78)
