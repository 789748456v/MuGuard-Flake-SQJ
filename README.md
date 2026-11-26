# Stabilization via Oracle Erosion: An Empirical Audit and Local Mutation Gate for Flaky Test Repair

Official replication package (`3.0.0-certified` + `v3.1` same-model SOTA head-to-head), complete five-model benchmark artifacts, same-model $W^+$-standardized adaptations (`FlakyFix`-style, `FlakyDoctor`-style, `FlakyGuard`-style, `MuGuard-Flake`, and `FlakyGuard + MuGuard`), ten-session stateless blinded cross-vendor ensemble reliability archive (`2,770` ratings), supplementary empirical tables (`ONLINE_RESOURCE_1.md`, `Tables S-1..S-9`), and automated verification suite for the manuscript submitted to *Software Quality Journal* (Springer Nature).

**Authors:** Zhijie Li¹ · Shaoyang Xu¹ · Ketong Liu¹ · Shibo Meng¹ · Feiran Gao² · Yuli Zhao¹\*  
¹ Software College, Northeastern University, Shenyang 110169, China  
² School of Computer Science and Technology, Beijing Institute of Technology, Beijing 100081, China  
\* Corresponding author: `zhaoyl@swc.neu.edu.cn`

---

## Study Overview

Automated flaky test repair pipelines increasingly pair context localization with large language models (LLMs) to eliminate intermittent continuous integration (CI) failures. However, nearly every pipeline accepts a candidate repair once it compiles, passes rerun schedules ($W^+$), and retains assertion statements. Because flaky test repair edits the test oracle and fixture themselves, optimizing solely for green runs invites **Stabilization via Oracle Erosion**: tests turn permanently green while silently losing sensitivity to future production faults.

This replication package accompanies our dual-language empirical audit and **MuGuard-Flake** fail-closed local mutation gate across:
- **203 eligible flaky tests** (208 attempted; 5 excluded below the three-obligation floor) across **69 Java and Python repositories** (**4,842** target baseline obligations $M_{\text{det}}$, **1,037** partner obligations, and **3,518** disjoint held-out obligations $M_{\text{ext}}$ across 177 tests).
- **100% complete evaluations across five modern LLMs from four vendors** (`203/203` multi-round $K=5$ runs on `deepseek-flash`, `deepseek-v4-pro`, and confirmatory `qwen3.7-plus` with zero API omissions; `203/203` single-pass $K=1$ runs on `kimi-k2.5` and `glm-5`), **eight controlled repair gating strategies** (including dynamic focal line **Coverage Gate** via JaCoCo / `coverage.py` and **Diff-Only & Offline Focal-Aware Cross-Vendor LLM-Judge**), verbatim historical repair artifacts (`FlakyDoctor` GPT-4 & Magicoder; partner-matched `iPFlakies`), **same-model, $W^+$-standardized SOTA adaptations** (`FlakyFix`-style $K=1$ & $K=5$, `FlakyDoctor`-style, `FlakyGuard`-style, `MuGuard-Flake`, and `FlakyGuard + MuGuard` across `203/203` tests and `|E|=177` held-out tests on `qwen3.7-plus`), and **108 PR-linked Java test instances across 50 GitHub pull requests**.
- **100% complete prefix-forked witness ablation (`129/129` forks across all four arms in 20 repositories)**, **142 unmodified-repository repeated-pollution controls**, fair Python re-indentation and live online `Top-10` subset-gating ablations, historical bug-commit replay (`167` test–commit replay trials across `59` distinct commits, `61` tests, and `19` repositories), and a **two-stage blinded ensemble audit** pairing our frozen Stage-1 author rubric with **10 independent, stateless, order-permuted OpenAI Codex (`gpt-6-astra`) sessions** (`2,770` blinded adjudications across all 53 PR instances, 60 stratified eroded patches, 118 main-campaign held-out $M_{\text{ext}}$-loss patches, and 46 SOTA held-out loss patches; Fleiss' $\kappa = 0.91\text{--}1.00$).

### Key Headline Findings

- **Prevalence of Silent Oracle Erosion, Same-Model SOTA Adaptations, & Hybrid PR Triage (RQ1):** One in five to over two in five $W^+$-passing patches silently drop baseline obligations across verbatim upstream artifacts (`27.27%` FlakyDoctor GPT-4; `23.81%` iPFlakies), same-model $W^+$-standardized adaptations on `qwen3.7-plus` (`21.28%` `FlakyFix`-style $K=1$, `43.09%` `FlakyFix`-style $K=5$, `26.53%` `FlakyDoctor`-style, and `29.26%` call-graph-guided `FlakyGuard`-style), and unconstrained LLMs (`19.90%–23.78%` at $K=5$; `25.56%–26.79%` at $K=1$). Adding the `MuGuard-Flake` package lifts same-model operational `SoundFix%` to `82.27%` (`167/203`, `+16.26` and `+18.23 pp` over `FlakyDoctor`- and `FlakyGuard`-style) and lowers conditional harmful (`W+S`) unseen loss to `1.53%` (`2/131`, and `3.10%` or `4/129` in `FlakyGuard + MuGuard`; statistically significant vs. `FlakyFix`-style $K=5$ at $p_{\text{Holm}} \le 0.0016$ and directionally lower vs. `FlakyDoctor`- and `FlakyGuard`-style at $p_{\text{Holm}} = 0.21\text{--}0.27$; `Tables 3, S-7, S-8`). On developer pull requests (`43` stabilized test-only instances), witness-augmented hybrid triage cuts strict gating's `20.51%` (`8/39`) false-rejection rate to `2.56%` (`1/39`; `0/29 = 0.0%` under strict `L`-only coding).
- **Behavioral Erosion Mechanisms & Four-Paradigm Blind Spots (RQ2):** Across `400` unique eroded patches, Syntactic Guard misses `99.0%`, Structural Guard misses `83.8%` (`335/400`), diff-only cross-vendor LLM-Judge misses `78.0%` (`312/400`), offline focal-aware cross-vendor LLM-Judge ($F \le 5$ dynamically executed focal functions) still misses `80.75%` (`323/400`; `Table S-9`), and dynamic focal line Coverage Gate misses `67.2%` (`268/399`, `77.5%` in Java).
- **Fail-Closed Gating, Asymmetric CI Break-Even, & Controlled Witness Isolation (RQ3):** Across all `203` eligible tests per model, `MuGuard-Flake` eliminates in-loop erosion by construction across all five models, lifts operational `SoundFix%` to `87.19%` (`deepseek-flash`), `79.80%` (`deepseek-v4-pro`), and `79.31%` (`qwen3.7-plus`)—beating static, dynamic coverage, and cross-vendor LLM-judge gates by `+11.8` to `+17.7 pp` (all Holm-adjusted $p \le 0.011$)—and lowers expected CI cost over every non-mutation baseline above $\lambda^* = 2.43$ (`1.11×–2.43×` across backbones; $\lambda > 0.50$ vs. LLM-Judge Gate). Across all `129/129` prefix-forked erosion states, localized surviving-mutant witnesses recover `70.54%` (`91/129`) versus `42.64%` (`55/129`) for scalar counts (`+27.91 pp`, $p_{\text{Holm}} = 0.0049$) and `32.56%` (`42/129`) for length-matched misleading witnesses (`+37.98 pp`, $p_{\text{Holm}} = 0.0005$).
- **Uncached Probing Latency, Python Re-Indentation, & Subset Gating (RQ4):** Uncached in-loop target mutation probing finishes in a median of `9.53 s` on Java and `3.72 s` on Python (faster than `20.89 s` LLM-Judge calls and far below `53–80 s` repair generation). Deterministic Python method re-indentation raises confirmatory `qwen3.7-plus` `MuGuard-Flake` yield up to `85.22%` (`+5.91 pp`, `Table S-4`), whereas relaxed $\tau < 1.00$ thresholds and live uniform `Top-10` gating (`12` leaked erosions, `Table S-5`) re-open silent oracle loss.

---

## Manuscript Table, Figure, & Supplementary Crosswalk

Every quantitative table (`Tables 2–12` in `paper/tables/` and `Tables S-1–S-9` in `ONLINE_RESOURCE_1.md`), macro (`paper/generated_macros.tex`), and figure (`Figures 1–5`) is programmatically generated and verified by `src/v3_generate_final_artifacts.py` and `src/verify_v30_certification.py`.

| Manuscript Element | Backing Artifact(s) in `data/` | Generator / Verifier Script |
| :--- | :--- | :--- |
| **Table 1** (Related Work Positioning) | Literature synthesis (`paper/sections/sec2_background_related.tex`) | `paper/main.tex` |
| **Table 2** (Benchmark Cohort & Base Probe) | `v2_frozen_benchmark_light.json`, `v3_study_summary_metrics.json` | `src/v3_generate_final_artifacts.py` |
| **Table 3** (Panel A Historical Artifacts; Panel B Same-Model SOTA) | `b1_provenance_manifest.json`, `v31_sota_qwen3.7-plus.json`, `v31_sota_statistical_summary.json` | `src/v31_adjudicate_and_analyze_sota.py`, `src/v3_generate_final_artifacts.py` |
| **Table 4** (Developer PR Audit & Witness Hybrid Triage) | `pr_*`, `v3_coverage_gate_offline_and_pr.json`, `v3_llm_judge_offline_and_pr.json`, `v3_witness_hybrid_triage.json` | `src/v3_generate_final_artifacts.py` |
| **Table 5** (400-Patch 4-Paradigm Bypass & 60-Patch Audit) | `taxonomy_*`, `v3_coverage_gate_offline_and_pr.json`, `v3_llm_judge_offline_and_pr.json`, `v3_focal_aware_llm_judge.json` | `src/v3_generate_final_artifacts.py` |
| **Table 6** (Complete `203/203` $K=5$ Operational Outcomes) | `v2_results_*.json`, `v3_completed_missing_k5.json`, `v3_b5_coverage_k5_qwen3.7-plus.json`, `v3_b6_llm_judge_k5_qwen3.7-plus.json` | `src/v3_generate_final_artifacts.py` |
| **Table 7** (`203/203` $K=1$ Screening + LLM-Judge) | `v2_round1_{kimi-k2.5,glm-5}.json`, `v3_k1_llm_judge_screening.json` | `src/v3_generate_final_artifacts.py` |
| **Table 8** (Fixed-Cohort Held-Out $M_{\text{ext}}$ & $Y_s$) | `v2_heldout_validation.json`, `v3_completed_heldout.json`, `v3_b5_b6_heldout_validation.json`, `v3_extra7_heldout_wsl_10sessions.json` | `src/v3_generate_final_artifacts.py` |
| **Table 9** (Project-Clustered Bootstrap & Sign-Flip Inference) | `v3_study_summary_metrics.json`, `v3_master_summary.json` | `src/v3_generate_final_artifacts.py` |
| **Table 10** (Complete `129/129` Prefix-Forked Witness Ablation) | `v2_forked_ablation_results.json`, `v3_completed_a2_forks.json` | `src/v3_generate_final_artifacts.py` |
| **Table 11** (Offline Pre-Comp. & Uncached Stage Latencies) | `v3_study_summary_metrics.json`, `v3_coverage_gate_offline_and_pr.json`, `v3_llm_judge_offline_and_pr.json` | `src/v3_generate_final_artifacts.py` |
| **Table 12** (Offline & Live Online Subset-Sampling Sensitivity) | `v3_study_summary_metrics.json`, `v3_rq4_online_topk10_qwen.json` | `src/v3_generate_final_artifacts.py` |
| **Tables S-1–S-9** (`ONLINE_RESOURCE_1.md`) | `v3_codex_10session_blinded_audit.json`, `v31_sota_statistical_summary.json`, `v3_focal_aware_llm_judge.json`, `v3_real_bug_replay_results.json` | `src/v3_generate_final_artifacts.py` |
| **Figures 1–5** (Architecture, Inflation Gap, Forest Plot, Forked Ablation, Latency/Top-$k$) | `figures/fig{0,1,2,4,5}_*.{pdf,png}` | `src/generate_architecture_figure.py`, `src/v3_generate_final_artifacts.py` |

---

## Quick-Start Reproduction & Automated Verification

### 1. Environment Setup

```bash
python -m venv .venv
source .venv/bin/activate  # On Windows PowerShell: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. Run the Attribution, Isolation & Guard Regression Suite

```bash
python src/test_harness_regressions.py
```

### 3. Recompute All Summary Metrics, Macros, 12 Main Tables, Figures, and `ONLINE_RESOURCE_1.md` (`Tables S-1..S-9`)

```bash
python src/v3_generate_final_artifacts.py
python src/v3_generate_markdown_deliverables.py
```

### 4. Recompile the 34-Page Submission PDF and Run Full Certification Verifiers

```bash
cd paper
pdflatex -interaction=nonstopmode main.tex
bibtex main
pdflatex -interaction=nonstopmode main.tex
pdflatex -interaction=nonstopmode main.tex
cd ..
python src/verify_v30_certification.py
python src/verify_v23_certification.py
```

---

## Citation

If you use this benchmark, replication package, or **MuGuard-Flake**, please cite:

```bibtex
@article{li2026muguardflake,
  author  = {Li, Zhijie and Xu, Shaoyang and Liu, Ketong and Meng, Shibo and Gao, Feiran and Zhao, Yuli},
  title   = {Stabilization via Oracle Erosion: An Empirical Audit and Local Mutation Gate for Flaky Test Repair},
  journal = {Software Quality Journal},
  year    = {2026},
  note    = {Manuscript under review; replication package: https://github.com/789748456v/MuGuard-Flake-SQJ}
}
```
