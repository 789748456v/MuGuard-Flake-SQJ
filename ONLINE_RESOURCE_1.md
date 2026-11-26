# Online Resource 1: Supplementary Empirical Tables (Tables S-1 to S-6)

Companion supplementary tables for *Stabilization via Oracle Erosion: An Empirical Audit and Local Mutation Gate for Flaky Test Repair* (*Software Quality Journal*).

---

## Table S-1: 10-Session Blinded Cross-Vendor Ensemble Reliability, Matched Historical SOTA Breakdown, and Complete Clustered Inference

### Part A: 10-Session Stateless Blinded OpenAI Codex (`gpt-6-astra`) Ensemble Audit (`2,310` Independent Adjudications)

| Audit Task & Dimension | Items ($N$) | Intra-Ensemble Fleiss' $\kappa$ | `10/10` Unanimous (`>=8/10`) | Mean Norm. Vote Entropy | Stage-1 vs. Consensus Raw% (Cohen's $\kappa$) | Single-Session $\kappa$ Range (`Min–Max`) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| Developer PRs (5-class, all evaluated) | 53 | **0.9945** | 98.1% (100.0%) | 0.0038 | 81.1% (0.6990) | 0.6630–0.6990 |
| Stabilized Test-Only PRs (Legitimate vs. Non-Legitimate) | 43 | **1.0000** | 100.0% (100.0%) | 0.0000 | 76.7% (0.3505) | 0.3505–0.3505 |
| Active Stratified Eroded Patches (7-class Mechanism) | 60 | **0.9756** | 93.3% (100.0%) | 0.0141 | 86.7% (0.8233) | 0.7608–0.8244 |
| Active Stratified Eroded Patches (Non-Legitimate Binary) | 60 | **0.9074** | 98.3% (100.0%) | 0.0078 | 93.3% (-0.0256) | -0.0256–0.3750 |
| Held-Out $M_{\text{ext}}$-Loss Patches (3-class $W/S/L$, base 111) | 111 | **0.9110** | 82.9% (94.6%) | 0.0731 | 78.4% (0.6597) | 0.5680–0.6606 |
| Held-Out $M_{\text{ext}}$-Loss Patches (Harmful $W+S$ vs. Benign $L$) | 111 | **0.8604** | 85.6% (94.6%) | 0.1032 | 85.6% (0.6092) | 0.4691–0.6293 |

*Note*: All 7 additional held-out $M_{\text{ext}}$-loss patches introduced by gap-completed runs and dynamic/LLM-judge baselines (`HO-112`..`HO-118`) achieved `10/10` (`100%`) unanimous ensemble agreement.

### Part B: Matched-Cohort Head-to-Head Comparison Against Historical SOTA Tools Across All Three Multi-Round Backbones

| Historical Benchmark Subset | Cohort $n$ | Upstream Tool SoundFix% (Leak%) | DS-Flash `MuGuard` SoundFix% | DS-V4-Pro `MuGuard` SoundFix% | Qwen 3.7+ `MuGuard` SoundFix% | `MuGuard` Leak% |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| `FlakyDoctor GPT-4` cohort | 75 | 32.00% (27.27%) | **94.67%** | **92.00%** | **92.00%** | **0.00%** |
| `FlakyDoctor Magicoder` cohort | 13 | 30.77% (0.00%) | **84.62%** | **84.62%** | **69.23%** | **0.00%** |
| `iPFlakies` partner-matched cohort | 77 | 41.56% (23.81%) | **85.71%** | **72.73%** | **70.13%** | **0.00%** |

### Part C: Shared Python Held-Out Mutant Deduplication Sensitivity

Deduplicating all `38` held-out Python mutant keys shared across sibling tests in the same repository (zero overlap with any test's own gate $M_{\text{det}}$) leaves accepted-patch $M_{\text{ext}}$-loss counts completely unchanged on `MuGuard-Flake`, `Scalar Gate`, and `Structural Guard` across all three multi-round backbones (`7 -> 7` on DS-Flash, `7 -> 7` on DS-V4-Pro, `6 -> 6` on Qwen 3.7+ for `MuGuard-Flake`).

### Part D: Supplementary Clustered Paired Inference (Prompt Guard Operational & Stress-Relaxed Held-Out Yield $Y_s^{\text{stress}}$)

| Model | Family | `MuGuard-Flake` Minus | Test-Wt. $\Delta$ [95% CI] | Equal-Repo $\Delta$ [95% CI] | Discordant $(b, a)$ | $p_{\text{cluster}}$ |
| :--- | :--- | :--- | :---: | :---: | :---: | :---: |
| DS-Flash | Fam.1 Op. | Prompt Guard | +16.26 [+8.72, +23.36] | +8.93 [+4.45, +14.13] | `(0, 33)` | 0.0002 |
| DS-Flash | Fam.2 $Y_s^{\text{stress}}$ | Unconstrained | -3.95 [-13.46, +4.08] | -1.05 [-10.22, +7.91] | `(17, 10)` | 0.5099 |
| DS-Flash | Fam.2 $Y_s^{\text{stress}}$ | Syntactic | -2.82 [-13.51, +5.99] | -4.17 [-11.01, +1.81] | `(14, 9)` | 0.6758 |
| DS-Flash | Fam.2 $Y_s^{\text{stress}}$ | Structural | -2.82 [-13.51, +5.99] | -4.17 [-11.01, +1.81] | `(14, 9)` | 0.6758 |
| DS-Flash | Fam.2 $Y_s^{\text{stress}}$ | Scalar Gate | +4.52 [+0.00, +10.13] | +1.07 [-3.23, +4.90] | `(1, 9)` | 0.1562 |
| DS-V4-Pro | Fam.1 Op. | Prompt Guard | +16.75 [+9.40, +23.83] | +10.34 [+5.59, +15.87] | `(0, 34)` | <0.0001 |
| DS-V4-Pro | Fam.2 $Y_s^{\text{stress}}$ | Unconstrained | -3.39 [-14.18, +6.02] | -3.42 [-12.46, +5.46] | `(21, 15)` | 0.5972 |
| DS-V4-Pro | Fam.2 $Y_s^{\text{stress}}$ | Syntactic | -2.26 [-12.64, +6.81] | -4.22 [-12.49, +3.41] | `(18, 14)` | 0.7401 |
| DS-V4-Pro | Fam.2 $Y_s^{\text{stress}}$ | Structural | -2.26 [-13.02, +7.14] | -4.35 [-12.99, +3.54] | `(18, 14)` | 0.7437 |
| DS-V4-Pro | Fam.2 $Y_s^{\text{stress}}$ | Scalar Gate | +4.52 [+0.74, +9.83] | +2.45 [+0.40, +5.10] | `(0, 8)` | 0.0625 |
| Qwen3.7+ | Fam.1 Op. | Prompt Guard | +11.82 [+5.08, +19.55] | +8.71 [+3.67, +14.75] | `(0, 24)` | 0.0010 |
| Qwen3.7+ | Fam.2 $Y_s^{\text{stress}}$ | Unconstrained | +0.56 [-8.72, +9.93] | +8.04 [-0.85, +17.43] | `(18, 19)` | 1.0000 |
| Qwen3.7+ | Fam.2 $Y_s^{\text{stress}}$ | Syntactic | -2.26 [-10.32, +5.95] | -0.31 [-7.52, +6.92] | `(15, 11)` | 0.6951 |
| Qwen3.7+ | Fam.2 $Y_s^{\text{stress}}$ | Structural | -1.13 [-9.94, +7.55] | +1.39 [-5.16, +8.22] | `(15, 13)` | 0.9033 |
| Qwen3.7+ | Fam.2 $Y_s^{\text{stress}}$ | Scalar Gate | +2.82 [+0.56, +5.39] | +1.47 [+0.20, +3.46] | `(1, 6)` | 0.1250 |
| Qwen3.7+ | Fam.2 $Y_s^{\text{stress}}$ | Coverage Gate ($n=195$) | -2.37 [-11.29, +6.59] | +1.28 [-6.58, +9.49] | `(16, 12)` | 0.7046 |
| Qwen3.7+ | Fam.2 $Y_s^{\text{stress}}$ | LLM-Judge Gate | +2.82 [-6.77, +12.09] | +9.37 [+0.11, +19.09] | `(14, 19)` | 0.6530 |

---

## Table S-2: Retention Threshold ($\tau$) Pareto Cliff vs. Witness-Augmented Hybrid PR Triage

| Gate Policy / Threshold $\tau$ | Eroded Patch Recall (`N=400`) | Missed Eroded Patches (Bypass%) | Legitimate PR False Rejections (`n=39`) | Genuine PR Weakenings Caught (`n=3`) |
| :--- | :---: | :---: | :---: | :---: |
| Scalar/Mutation Threshold $\tau = 1.00$ | 400/400 (100.00%) | 0 (0.00%) | 8/39 (20.51%) | 1/3 |
| Scalar/Mutation Threshold $\tau = 0.99$ | 396/400 (99.00%) | 4 (1.00%) | 8/39 (20.51%) | 1/3 |
| Scalar/Mutation Threshold $\tau = 0.98$ | 382/400 (95.50%) | 18 (4.50%) | 8/39 (20.51%) | 1/3 |
| Scalar/Mutation Threshold $\tau = 0.95$ | 312/400 (78.00%) | 88 (22.00%) | 7/39 (17.95%) | 1/3 |
| Scalar/Mutation Threshold $\tau = 0.90$ | 172/400 (43.00%) | 228 (57.00%) | 6/39 (15.38%) | 1/3 |
| Scalar/Mutation Threshold $\tau = 0.85$ | 130/400 (32.50%) | 270 (67.50%) | 5/39 (12.82%) | 0/3 |
| Scalar/Mutation Threshold $\tau = 0.80$ | 100/400 (25.00%) | 300 (75.00%) | 2/39 (5.13%) | 0/3 |
| **Witness-Augmented LLM Triage (`\tau = 1.00` Gate + Witness Triage, Qwen 3.7+)** | **52/60 (86.67% stratified sample)** | **8/60 (13.33% sample)** | **1/39 (2.56%)** | **1/1 (of 3 total)** |

*Finding*: Relaxing $\tau$ from `1.00` to `0.90` cuts PR false rejections modestly (`20.51% -> 15.38%`) while missing **57.00% (`228/400`)** of eroded patches. Keeping strict $\tau = 1.00$ preserves `400/400` automated gate interception, and attaching surviving-mutant witnesses to abstention reviews boosts cross-vendor LLM-judge erosion recognition on stratified eroded patches from **28.33% (`17/60`) to 86.67% (`52/60`, +58.33 pp)** while reducing legitimate PR false rejections to **1/39 (2.56%)** (`1/19 (5.3%)` at the PR-URL level) and keeping the Nacos weakening blocked (`1/1`).

---

## Table S-3: Five-Model Cross-Vendor `LLM-Judge` Post-Gate Screening (`N=203` per Backbone)

| Repair Backbone | Cross-Vendor Judge | Accepted $W^+$ | Leaked Eroded (Leak%) | Eroded Caught | Healthy Sound Fixes Falsely Rejected | Net SoundFix% |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: |
| `deepseek-flash` | Qwen 3.7+ | 182 | 34 (18.68%) | 6 | **9** | 66.83% |
| `deepseek-v4-pro` | Qwen 3.7+ | 155 | 34 (21.94%) | 9 | **10** | 61.70% |
| `qwen3.7-plus` | Zhipu GLM-5 | 150 | 21 (14.00%) | 14 | **15** | 60.10% |
| `kimi-k2.5` ($K=1$) | Qwen 3.7+ | 101 | 22 (21.78%) | 7 | **4** | 38.92% |
| `glm-5` ($K=1$) | Qwen 3.7+ | 113 | 26 (23.01%) | 5 | **10** | 41.87% |

*Finding*: On 4 of 5 models, standalone cross-vendor LLM-Judge screening falsely rejects **more** healthy sound fixes than eroded patches caught!

---

## Table S-4: Fair Python Method Re-Indentation Ablation on Confirmatory Qwen 3.7 Plus (`N=203, |E|=177`)

| Strategy | Default SoundFix% | With Fair Re-Indentation SoundFix% ($\Delta$ pp) | Leaked Eroded (Default $\to$ Re-Indented) | Strict $Y_s$ (Default $\to$ Re-Indented) | Stress $Y_s$ (Default $\to$ Re-Indented) |
| :--- | :---: | :---: | :---: | :---: | :---: |
| Unconstrained | 65.02% | 68.97% (+3.95) | 37 $\to$ **44 (22.56%)** | 72.32% $\to$ 74.01% | 77.97% $\to$ 80.23% |
| Structural Guard | 63.05% | 67.00% (+3.95) | 39 $\to$ **45 (23.81%)** | 71.75% $\to$ 72.88% | 79.66% $\to$ 81.36% |
| Scalar Gate | 75.37% | 79.80% (+4.43) | 0 $\to$ **0 (0.00%)** | 67.23% $\to$ 70.06% | 75.71% $\to$ 78.53% |
| **MuGuard-Flake** | **79.31%** | **85.22% (+5.91)** | **0 $\to$ 0 (0.00%)** | **70.06% $\to$ 72.88%** | **78.53% $\to$ 81.36%** |

---

## Table S-5: Live Online `Top-10` Subset Gate vs. Full `MuGuard-Flake` on Confirmatory Qwen 3.7 Plus (`N=203, |E|=177`)

| Gate Configuration | Accepted ($W^+$) | Full-Pool Leaked Eroded | Operational SoundFix% | Held-Out $M_{\text{ext}}$ Loss | Strict $Y_s$ | Stress $Y_s$ |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| Online Uniform `Top-10` Gate | 165 (81.28%) | 12 (7.27%) | 75.37% | 8/151 (5.30%) | 126 (71.19%) | 141 (79.66%) |
| **Full `MuGuard-Flake` (All $M_{\text{det}}$)** | **161 (79.31%)** | **0 (0.00%)** | **79.31%** | **6/146 (4.11%)** | **124 (70.06%)** | **139 (78.53%)** |

---

## Table S-6: Empirical Audit of Historical Production Bug-Commit Reversions (`167` Test–Commit Replay Trials Across `59` Distinct Commits, `61` Tests, and `19` Repositories)

| Replay Outcome Against Frozen Benchmark HEAD | File-Level Trials (`n=149`) | Line-Targeted Focal Trials (`n=18`) | Combined (`n=167`) | Methodological Implication |
| :--- | :---: | :---: | :---: | :--- |
| Fails to compile cleanly against HEAD | 14 | 8 | 22 (13.2%) | API/schema evolution breaks historical commit reverts |
| Compiles cleanly, does not fail $T_{\text{orig}}$ under $e_0$ | 128 | 10 | 138 (82.6%) | Reverted production changes do not trigger $T_{\text{orig}}$ failure under $e_0$ |
| Reproduces failure on $T_{\text{orig}}$ under $e_0$ | 7 | 0 | 7 (4.2%) | Coarse return-value break (`centreon-sdk-python` commit `70d1752f91`), caught by all candidate patches |

*Finding*: Because real historical bug commits in evolving repositories rarely isolate fine-grained relational or delimiter invariants covered by a single flaky test without breaking compilation, disjoint held-out mutation pools ($M_{\text{ext}}$) remain indispensable for non-circular sensitivity evaluation.
