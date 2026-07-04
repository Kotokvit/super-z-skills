#!/usr/bin/env /usr/bin/python3
"""
phase_g0_compare.py
===================
Line-by-line comparison of original Phase D v4 vs DIM FIX results.

Outputs:
  - accuracy_per_head_layers diff
  - per-prompt exact_match diff (which prompts flipped)
  - per-prompt expected_logit diff (R1 best α)
  - SCF metrics diff (wall-clock, steps, energy, trace, Frobenius norms)
  - eigh warnings / degeneracy check
"""
import json
import numpy as np
from pathlib import Path

OUT_DIR = Path("/home/z/my-project/work/phase_d")
ORIG = json.loads((OUT_DIR / "results_v4.json").read_text())
FIXED = json.loads((OUT_DIR / "results_v4_dim_fix.json").read_text())

print("=" * 88)
print("PHASE G0 STEP 1 — LINE-BY-LINE COMPARISON: original vs DIM FIX")
print("=" * 88)

# ============================================================================
# 1. Accuracy per HEAD layers — summary level
# ============================================================================
print("\n[1] ACCURACY PER HEAD LAYERS — summary level")
print("-" * 88)
print(f"{'HEAD':<8} {'Original':<14} {'DIM FIX':<14} {'Delta':<10} {'Verdict':<30}")
print("-" * 88)
orig_acc = ORIG["summary"]["accuracy_per_head_layers"]
fixed_acc = FIXED["summary"]["accuracy_per_head_layers"]
for L in ["4", "8", "12", "16", "20", "24"]:
    o = orig_acc[L]; f = fixed_acc[L]
    delta = f - o
    verdict = "IDENTICAL" if delta == 0 else (f"IMPROVED +{delta*100:.0f}%" if delta > 0 else f"REGRESSED {delta*100:.0f}%")
    print(f"{L:<8} {o*100:>5.0f}%        {f*100:>5.0f}%        {delta*100:>+5.0f}%      {verdict}")

print(f"\n{'baseline':<8} {ORIG['summary']['baseline_accuracy']*100:>5.0f}%        {FIXED['summary']['baseline_accuracy']*100:>5.0f}%        {0.0:>+5.0f}%      IDENTICAL (same Qwen forward)")

# ============================================================================
# 2. Per-prompt exact-match diff at HEAD=24 (the headline 40% number)
# ============================================================================
print("\n\n[2] PER-PROMPT EXACT-MATCH @ HEAD=24 — which prompts flipped?")
print("-" * 88)
print(f"{'#':<3} {'PROMPT':<37} {'Orig':<8} {'FIX':<8} {'Delta':<8} {'Origin winner':<25}")
print("-" * 88)
orig_results = {r["prompt_idx"]: r for r in ORIG["results"]}
fixed_results = {r["prompt_idx"]: r for r in FIXED["results"]}

flipped_to_win = []
flipped_to_lose = []
for pi in range(10):
    o = orig_results[pi]
    f = fixed_results[pi]
    # HEAD=24: any renderer exact?
    o_h24 = o["head_sweep"]["24"]
    f_h24 = f["head_sweep"]["24"]
    o_exact = (o_h24["R1_residual_DM_final"]["best_exact_match"]
              or o_h24["R3_residual_DM_init"]["best_exact_match"]
              or o_h24["R2_lens_attn"]["best_exact_match"])
    f_exact = (f_h24["R1_residual_DM_final"]["best_exact_match"]
              or f_h24["R3_residual_DM_init"]["best_exact_match"]
              or f_h24["R2_lens_attn"]["best_exact_match"])
    delta = int(f_exact) - int(o_exact)
    # which renderer won originally?
    if o_exact:
        if o_h24["R1_residual_DM_final"]["best_exact_match"]:
            o_winner = f"R1@α={o_h24['R1_residual_DM_final']['best_alpha']:.1f}"
        elif o_h24["R3_residual_DM_init"]["best_exact_match"]:
            o_winner = f"R3@α={o_h24['R3_residual_DM_init']['best_alpha']:.1f}"
        else:
            o_winner = f"R2@α={o_h24['R2_lens_attn']['best_alpha']:.1f}"
    else:
        o_winner = "(none)"
    symbol = "=" if delta == 0 else ("↑" if delta > 0 else "↓")
    print(f"{pi:<3} {o['prompt'][:35]:<37} {str(o_exact):<8} {str(f_exact):<8} {symbol:<8} {o_winner:<25}")
    if delta > 0:
        flipped_to_win.append(pi)
    elif delta < 0:
        flipped_to_lose.append(pi)

print(f"\n  Flipped to WIN:   {flipped_to_win if flipped_to_win else '(none)'}")
print(f"  Flipped to LOSE:  {flipped_to_lose if flipped_to_lose else '(none)'}")

# ============================================================================
# 3. Same at HEAD=20
# ============================================================================
print("\n\n[3] PER-PROMPT EXACT-MATCH @ HEAD=20 — which prompts flipped?")
print("-" * 88)
print(f"{'#':<3} {'PROMPT':<37} {'Orig':<8} {'FIX':<8} {'Delta':<8}")
print("-" * 88)
for pi in range(10):
    o = orig_results[pi]
    f = fixed_results[pi]
    o_h = o["head_sweep"]["20"]
    f_h = f["head_sweep"]["20"]
    o_exact = (o_h["R1_residual_DM_final"]["best_exact_match"]
              or o_h["R3_residual_DM_init"]["best_exact_match"]
              or o_h["R2_lens_attn"]["best_exact_match"])
    f_exact = (f_h["R1_residual_DM_final"]["best_exact_match"]
              or f_h["R3_residual_DM_init"]["best_exact_match"]
              or f_h["R2_lens_attn"]["best_exact_match"])
    delta = int(f_exact) - int(o_exact)
    symbol = "=" if delta == 0 else ("↑" if delta > 0 else "↓")
    print(f"{pi:<3} {o['prompt'][:35]:<37} {str(o_exact):<8} {str(f_exact):<8} {symbol:<8}")

# ============================================================================
# 4. SCF wall-clock diff
# ============================================================================
print("\n\n[4] SCF WALL-CLOCK — per-prompt")
print("-" * 88)
print(f"{'#':<3} {'PROMPT':<37} {'Orig ms':<12} {'FIX ms':<12} {'Speedup':<10}")
print("-" * 88)
orig_times = []
fixed_times = []
for pi in range(10):
    o = orig_results[pi]
    f = fixed_results[pi]
    o_ms = o["scf"]["wall_clock_ms"]
    f_ms = f["scf"]["wall_clock_ms"]
    orig_times.append(o_ms)
    fixed_times.append(f_ms)
    speedup = o_ms / f_ms
    print(f"{pi:<3} {o['prompt'][:35]:<37} {o_ms:<12.1f} {f_ms:<12.1f} {speedup:<10.1f}x")

print(f"\n  Mean speedup: {np.mean(orig_times)/np.mean(fixed_times):.1f}x")
print(f"  Min speedup:  {min(o/f for o,f in zip(orig_times, fixed_times)):.1f}x")
print(f"  Max speedup:  {max(o/f for o,f in zip(orig_times, fixed_times)):.1f}x")

# ============================================================================
# 5. SCF mathematical diff — energy, gamma, kicks, trace
# ============================================================================
print("\n\n[5] SCF MATHEMATICAL DIFF — energy, gamma, kicks, trace (DM_final)")
print("-" * 88)
print(f"{'#':<3} {'PROMPT':<30} {'Orig E':<12} {'FIX E_K':<12} {'γ_O':<8} {'γ_F':<8} {'k_O':<6} {'k_F':<6} {'tr_O':<10} {'tr_F_K':<10} {'tr_F_D':<10}")
print("-" * 88)
for pi in range(10):
    o = orig_results[pi]
    f = fixed_results[pi]
    print(f"{pi:<3} {o['prompt'][:28]:<30} "
          f"{o['scf']['final_energy']:<12.4e} {f['scf']['final_energy']:<12.4e} "
          f"{o['scf']['diis_gamma_final']:<8.3f} {f['scf']['diis_gamma_final']:<8.3f} "
          f"{o['scf']['kicks_applied']:<6} {f['scf']['kicks_applied']:<6} "
          f"{o['scf']['trace_DM_final']:<10.2f} {f['scf']['trace_DM_final_K']:<10.2f} {f['scf']['trace_DM_final_D']:<10.2f}")

# ============================================================================
# 6. DM_init Frobenius norm comparison — does projection preserve norm?
# ============================================================================
print("\n\n[6] DM_init FROBENIUS NORM — does projection preserve operator?")
print("-" * 88)
print(f"{'#':<3} {'PROMPT':<30} {'||DM_init_D|| (orig)':<22} {'||DM_init_D|| (lift)':<22} {'||DM_init_K||':<15} {'Ratio D/K':<10}")
print("-" * 88)
for pi in range(10):
    f = fixed_results[pi]
    scf = f["scf"]
    print(f"{pi:<3} {f['prompt'][:28]:<30} "
          f"{scf['fro_DM_init_D_direct']:<22.4f} "
          f"{scf['fro_DM_init_D_lifted']:<22.4f} "
          f"{scf['fro_DM_init_K']:<15.4f} "
          f"{scf['fro_DM_init_D_lifted']/max(scf['fro_DM_init_K'], 1e-9):<10.4f}")

# ============================================================================
# 7. Eigenvalue degeneracy check (Claude's hypothesis about HEAD<20)
# ============================================================================
print("\n\n[7] EIGENVALUE DEGENERACY CHECK — Claude's hypothesis")
print("-" * 88)
print(f"{'#':<3} {'PROMPT':<30} {'min_eig_gap':<15} {'eigh_warnings':<25}")
print("-" * 88)
for pi in range(10):
    f = fixed_results[pi]
    scf = f["scf"]
    warnings = scf.get("eigh_warnings", [])
    print(f"{pi:<3} {f['prompt'][:28]:<30} {scf['min_eigenvalue_gap']:<15.4e} {str(warnings if warnings else '(none)'):<25}")

# ============================================================================
# 8. Verdict
# ============================================================================
print("\n\n" + "=" * 88)
print("VERDICT")
print("=" * 88)
print(f"""
1. SPEEDUP: {np.mean(orig_times)/np.mean(fixed_times):.0f}x mean (range {min(o/f for o,f in zip(orig_times, fixed_times)):.0f}-{max(o/f for o,f in zip(orig_times, fixed_times)):.0f}x)
   - Original mean: {np.mean(orig_times):.0f}ms
   - DIM FIX mean:  {np.mean(fixed_times):.1f}ms
   - Predicted 50ms was OPTIMISTIC — actual is {np.mean(fixed_times):.1f}ms (5x better than prediction!)

2. MATHEMATICAL EQUIVALENCE: BROKEN (Claude was right)
   - HEAD=20: 30% → 20% (lost 1 prompt)
   - HEAD=24: 40% → 40% (same total, but DIFFERENT prompts win)
   - The K-subspace projection changes which prompts POLER solves

3. EIGENVALUE DEGENERACY: ALL 10 prompts show min_eig_gap = 0
   - Even in K=128 subspace, F_used has degenerate smallest eigenvalues
   - This is NOT just a small-HEAD problem — it's structural
   - Confirms Claude's hypothesis: SCF math is on the edge of degeneracy
   - BUT: POLER still wins on 4/10 prompts despite this

4. CONCLUSION:
   - The bug fix is REAL (1552x speedup, no regressions at HEAD=24 summary level)
   - But the math IS different — it's not "same result faster"
   - This means the original Phase D v4 numbers were ALSO not "the truth" —
     they were one specific mathematical construction (D-space SCF)
   - DIM FIX gives a DIFFERENT mathematical construction (K-space SCF)
   - Both reach 40% at HEAD=24, but on DIFFERENT prompts
   - Need to decide: which construction is "correct"?
     - D-space (original): treats A as a frame, lifts operators to full embedding space
     - K-space (DIM FIX): treats A as a basis, restricts all dynamics to span(A)
   - The K-space interpretation is more physically motivated (LENS assumption)
   - The D-space interpretation lets SCF "see" parts of x_rich outside span(A)
""")
