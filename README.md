# Stabilization via Oracle Erosion: An Empirical Audit and Local Mutation Gate for Flaky Test Repair

Official replication package (`3.0.0-certified`), complete five-model benchmark artifacts, ten-session stateless blinded cross-vendor ensemble reliability archive, supplementary empirical tables (`ONLINE_RESOURCE_1.md`), and automated verification suite for the manuscript submitted to *Software Quality Journal* (Springer Nature).

**Authors:** Zhijie Li¹ · Ketong Liu¹ · Shaoyang Xu¹ · Shibo Meng¹ · Feiran Gao² · Yuli Zhao¹\*  
¹ Software College, Northeastern University, Shenyang 110169, China  
² School of Computer Science and Technology, Beijing Institute of Technology, Beijing 100081, China  
\* Corresponding author: `zhaoyl@swc.neu.edu.cn`

---

## Study Overview

Automated flaky test repair pipelines increasingly pair context localization with large language models (LLMs) to eliminate intermittent continuous integration (CI) failures. However, nearly every pipeline accepts a candidate repair once it compiles, passes rerun schedules ($W^+$), and retains assertion statements. Because flaky test repair edits the test oracle and fixture themselves, optimizing solely for green runs invites **Stabilization via Oracle Erosion**: tests turn permanently green while silently losing sensitivity to future production faults.

This replication package accompanies our dual-language empirical audit and **MuGuard-Flake** fail-closed local mutation gate across:
- **203 eligible flaky tests** (208 attempted; 5 excluded below the three-obligation floor) across **69 Java and Python repositories** (**4,842** target baseline obligations $M_{\text{det}}$, **1,037** partner obligations, and **3,518** disjoint held-out obligations $M_{\text{ext}}$ across 177 tests).
- **100% complete evaluations across five modern LLMs from four vendors** (`203/203` multi-round $K=5$ runs on `deepseek-flash`, `deepseek-v4-pro`, and confirmatory `qwen3.7-plus` with zero API omissions; `203/203` single-pass $K=1$ runs on `kimi-k2.5` and `glm-5`), **eight controlled repair gating strategies** (including dynamic **Coverage Gate** via JaCoCo / `coverage.py` and **Cross-Vendor LLM-Judge Gate**), historical repair artifacts (`FlakyDoctor` GPT-4 & Magicoder; partner-matched `iPFlakies`), and **108 PR-linked Java test instances across 50 GitHub pull requests**.
- **100% complete prefix-forked witness ablation (`129/129` forks across all four arms in 20 repositories)**, **142 unmodified-repository repeated-pollution controls**, fair Python re-indentation and live online `Top-10` subset-gating ablations, historical bug-commit replay (`167` commits across `61` tests in `19` repositories), and a **two-stage blinded ensemble audit** pairing our frozen Stage-1 author rubric with **10 independent, stateless, order-permuted OpenAI Codex (`gpt-6-astra`) sessions** (`2,310` blinded adjudications across all 53 PRs, 60 active stratified eroded patches, and all 118 unique held-out $M_{\text{ext}}$-loss patches; Fleiss' $\kappa = 0.91\text{--}1.00$).

### Key Headline Findings

- **Prevalence of Silent Oracle Erosion & Hybrid PR Triage (RQ1):** Roughly one in five to one in four $W^+$-passing patches silently drops baseline obligations across historical tools (`27.27%` FlakyDoctor GPT-4; `23.81%` iPFlakies) and unconstrained LLMs (`19.90%–23.78%` at $K=5$; `25.56%–26.79%` at $K=1$), opening a `24–26 pp` Proxy Inflation Gap. Head to head on matched historical cohorts, `MuGuard-Flake` lifts sound-fix yield to `92.0%–94.7%` vs. FlakyDoctor GPT-4 and `70.1%–85.7%` vs. iPFlakies with `0%` leaked erosion. On developer pull requests (`43` stabilized test-only instances across `22` distinct PRs in `17` repositories), strict `MuGuard-Flake` gating incurs a conservative `20.51%` (`8/39`) instance-level false-rejection rate concentrated in `4/19` (`21.1%`) legitimate PRs (`3.4%` = `1/29` under strict ensemble coding), which **witness-augmented hybrid triage reduces to `0.0%` at both the instance (`0/39`) and PR-URL (`0/19`) levels**.
- **Behavioral Erosion Mechanisms & Four-Paradigm Blind Spots (RQ2):** Across `400` unique eroded patches, Syntactic Guard misses `99.0%`, Structural Guard misses `83.8%` (`335/400`), cross-vendor LLM-Judge misses `78.3%` (`313/400`), and dynamic line/branch Coverage Gate misses `70.3%` (`265/377`, `83.1%` in Java—directly refuting `ReproFlake`'s coverage-comparison conjecture), because precondition shifts, lossy delimiter splits, partner/shared-scope edits, and focal call bypasses preserve assertion syntax and line execution while severing fault propagation.
- **Fail-Closed Gating, Asymmetric CI Break-Even, & Complete Witness Isolation (RQ3):** Across all `203` eligible tests per model, `MuGuard-Flake` eliminates in-loop erosion (`0.00%` across all five models), lifts operational `SoundFix%` to `87.19%` (`deepseek-flash`), `79.80%` (`deepseek-v4-pro`), and `79.31%` (`qwen3.7-plus`)—beating static, dynamic coverage, and cross-vendor LLM-judge gates by `+11.8` to `+17.7 pp` (all Holm-adjusted $p \le 0.011$)—and cuts raw conditional unseen $M_{\text{ext}}$ loss by `39%–65%` (**and truly harmful `W+S` oracle losses by `62%–87.5%`, dropping from `16` tests under unconstrained repair to `2` on confirmatory `qwen3.7-plus`**). Asymmetric CI cost analysis shows `MuGuard-Flake` lowers expected CI cost whenever silent regressions cost more than `1.11×–2.43×` an abstention review and dominates LLM-Judge Gate at every $\lambda > 0$. Across all `129/129` prefix-forked erosion states, localized surviving-mutant witnesses recover `70.54%` (`91/129`) versus `42.64%` (`55/129`) for scalar counts (`+27.91 pp`, $p = 0.0049$) and `32.56%` (`42/129`) for length-matched misleading witnesses (`+37.98 pp`, $p = 0.0002$).
- **Uncached Probing Latency, Python Re-Indentation, & Live Subset Gating (RQ4):** Uncached in-loop target mutation probing finishes in a median of `9.53 s` on Java and `3.72 s` on Python (faster than `20.89 s` LLM-Judge calls and far below `53–80 s` repair generation). Deterministic Python method re-indentation raises confirmatory `qwen3.7-plus` `MuGuard-Flake` yield to `85.22%` (`+5.91 pp`) with `0%` leaked erosion, whereas relaxed $\tau < 1.00$ thresholds and live uniform `Top-10` gating (`12` leaked erosions) re-open silent oracle loss.

---

## Manuscript Table, Figure, & Supplementary Crosswalk

Every quantitative table (`Tables 2–12` in `paper/tables/` and `Tables S-1–S-6` in `ONLINE_RESOURCE_1.md`), macro (`paper/generated_macros.tex`), and figure (`Figures 1–5`) is programmatically generated and verified by `src/v3_generate_final_artifacts.py` and `src/verify_v30_certification.py`.

| Manuscript Element | Backing Artifact(s) in `data/` | Generator / Verifier Script |
| :--- | :--- | :--- |
| **Table 1** (Related Work Positioning) | Literature synthesis (`paper/sections/sec2_background_related.tex`) | `paper/main.tex` |
| **Table 2** (Benchmark Cohort & Base Probe) | `v2_frozen_benchmark_light.json`, `v3_study_summary_metrics.json` | `src/v3_generate_final_artifacts.py` |
| **Table 3** (Historical Artifacts & Matched Head-to-Head) | `b1_provenance_manifest.json`, `v3_offline_pareto_and_b1_head2head.json` | `src/v3_generate_final_artifacts.py` |
| **Table 4** (Developer PR Audit & Witness Hybrid Triage) | `pr_*`, `v3_coverage_gate_offline_and_pr.json`, `v3_llm_judge_offline_and_pr.json`, `v3_witness_hybrid_triage.json` | `src/v3_generate_final_artifacts.py` |
| **Table 5** (400-Patch 4-Paradigm Bypass & 60-Patch Audit) | `taxonomy_*`, `v3_coverage_gate_offline_and_pr.json`, `v3_llm_judge_offline_and_pr.json`, `v3_codex_10session_blinded_audit.json` | `src/v3_generate_final_artifacts.py` |
| **Table 6** (Complete `203/203` $K=5$ Operational Outcomes) | `v2_results_*.json`, `v3_completed_missing_k5.json`, `v3_b5_coverage_k5_qwen3.7-plus.json`, `v3_b6_llm_judge_k5_qwen3.7-plus.json` | `src/v3_generate_final_artifacts.py` |
| **Table 7** (`203/203` $K=1$ Screening + LLM-Judge) | `v2_round1_{kimi-k2.5,glm-5}.json`, `v3_k1_llm_judge_screening.json` | `src/v3_generate_final_artifacts.py` |
| **Table 8** (Fixed-Cohort Held-Out $M_{\text{ext}}$ & $Y_s$) | `v2_heldout_validation.json`, `v3_completed_heldout.json`, `v3_b5_b6_heldout_validation.json`, `v3_extra7_heldout_wsl_10sessions.json` | `src/v3_generate_final_artifacts.py` |
| **Table 9** (Project-Clustered Bootstrap & Sign-Flip Inference) | `v3_study_summary_metrics.json`, `v3_master_summary.json` | `src/v3_generate_final_artifacts.py` |
| **Table 10** (Complete `129/129` Prefix-Forked Witness Ablation) | `v2_forked_ablation_results.json`, `v3_completed_a2_forks.json` | `src/v3_generate_final_artifacts.py` |
| **Table 11** (Offline Pre-Comp. & Uncached Stage Latencies) | `v3_study_summary_metrics.json`, `v3_coverage_gate_offline_and_pr.json`, `v3_llm_judge_offline_and_pr.json` | `src/v3_generate_final_artifacts.py` |
| **Table 12** (Offline & Live Online Subset-Sampling Sensitivity) | `v3_study_summary_metrics.json`, `v3_rq4_online_topk10_qwen.json` | `src/v3_generate_final_artifacts.py` |
| **Tables S-1–S-6** (`ONLINE_RESOURCE_1.md`) | `v3_codex_10session_blinded_audit.json`, `v3_python_indent_ablation.json`, `v3_real_bug_replay_results.json`, `v3_master_summary.json` | `src/v3_generate_final_artifacts.py` |
| **Figures 1–5** (Architecture, Inflation Gap, Forest Plot, Forked Ablation, Latency/Top-$k$) | `figures/fig{0,1,2,4,5}_*.{pdf,png}` | `src/generate_architecture_figure.py`, `src/v3_generate_final_artifacts.py` |

---

## Repository Structure

```text
MuGuard-Flake-SQJ/
├── README.md                                        # Study overview, crosswalk, and reproduction guide
├── ONLINE_RESOURCE_1.md                             # Supplementary Tables S-1 to S-6 (Online Resource 1)
├── FROZEN_AUDIT_PROTOCOL.md                         # Locked experimental and adjudication protocol
├── LICENSE                                          # MIT License
├── requirements.txt                                 # Python dependencies
├── paper/                                           # Complete LaTeX manuscript sources, tables, figures, and compiled PDF
│   ├── main.pdf                                     # 33-page submission PDF (0 errors, 0 warnings, 0 overfull hboxes)
│   ├── main.tex                                     # Master LaTeX entry point
│   ├── references.bib                               # 62 verified BibTeX references (100% cited)
│   ├── generated_macros.tex                         # Auto-synchronized v3.0 numeric macros
│   ├── MANUSCRIPT_SQJ.md                            # Synchronized Markdown manuscript summary
│   ├── FULL_STUDY_REPORT.md                         # Synchronized Chinese technical report
│   ├── sections/                                    # sec1_intro.tex .. sec8_threats_conclusion.tex
│   ├── tables/                                      # Auto-generated LaTeX tables (Tables 2–12, all >= 7.5pt)
│   └── figures/                                     # Publication vector PDFs and 300-DPI PNGs
├── figures/                                         # Top-level mirror of all 5 publication figures
├── data/                                            # Certified v2.3 base + v3.0 complete upgrades, baselines, and 10-session audits
│   ├── v3_study_summary_metrics.json                # Master 3.0.0-certified statistical & audit summary
│   ├── v3_master_summary.json                       # Aggregated v3.0 baseline, ablation, and ensemble metrics
│   ├── v3_codex_10session_{blinded_audit,raw_runs}.json  # 10 stateless blinded Codex sessions (2,310 ratings)
│   ├── v3_completed_{missing_k5,a2_forks,heldout}.json   # Completed DeepSeek gap runs (203/203 K=5, 129/129 forks)
│   ├── v3_b5_coverage_k5_qwen3.7-plus.json          # Dynamic JaCoCo / coverage.py K=5 repair campaign
│   ├── v3_b6_llm_judge_k5_qwen3.7-plus.json         # Cross-vendor LLM-Judge K=5 repair campaign
│   ├── v3_{coverage_gate,llm_judge}_offline_and_pr.json  # Offline 400-patch & 43-PR multi-paradigm evaluations
│   ├── v3_witness_hybrid_triage.json                # Witness-augmented PR & eroded-patch triage evaluation
│   ├── v3_python_indent_ablation.json               # Fair deterministic Python method re-indentation ablation
│   ├── v3_rq4_online_topk10_qwen.json               # Live online Top-10 subset-gating K=5 campaign
│   ├── v3_*_bug_*.json                              # Historical bug-commit mining & Docker replay results
│   └── v2_*.json / llm_cache_*.json                 # Preserved 2.3.0-certified benchmark, controls, and caches
└── src/                                             # MuGuard-Flake implementation, v3.0 campaign runners, and verifiers
    ├── v3_generate_final_artifacts.py               # Master v3.0 summary, macro, 12-table, figure, & Online Resource generator
    ├── verify_v30_certification.py                  # 8-gate v3.0 end-to-end certification verifier
    ├── verify_v23_certification.py                  # 10-invariant v2.3 base certification verifier
    ├── v3_codex_10session_blinded_audit.py          # Stateless order-permuted 10-session blinded ensemble harness
    ├── v3_complete_missing_runs.py                  # Bounded-reasoning gap completion runner (identical prompts)
    ├── v3_run_coverage_gate.py / v3_remote_coverage_gate.py  # Dynamic JaCoCo & coverage.py gate backend
    ├── v3_llm_judge_baseline.py / v3_run_llm_judge_k5.py     # Cross-vendor LLM-Judge gate backend
    ├── v3_witness_hybrid_triage.py / v3_python_indent_ablation.py / v3_rq4_online_topk_and_cached_latency.py
    ├── v2_statistical_and_audit_engine.py           # Whole-project clustered bootstrap & Rademacher permutation engine
    ├── analyze_and_security_audit.py                # Automated secret-leak and workspace isolation checker
    └── test_harness_regressions.py                  # 19-test unit & integration regression suite
```

---

## Quick-Start Reproduction & Automated Verification

### 1. Environment Setup

```bash
python -m venv .venv
source .venv/bin/activate  # On Windows PowerShell: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. Run the 19-Test Attribution, Isolation & Guard Regression Suite

```bash
python src/test_harness_regressions.py
```

### 3. Recompute All v3.0 Summary Metrics, Macros, 12 Main Tables, Figures, and `ONLINE_RESOURCE_1.md`

```bash
python src/v3_generate_final_artifacts.py
python src/v3_generate_markdown_deliverables.py
```

### 4. Recompile the 30-Page Submission PDF and Run Full Certification Verifiers

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
  author  = {Li, Zhijie and Liu, Ketong and Xu, Shaoyang and Meng, Shibo and Gao, Feiran and Zhao, Yuli},
  title   = {Stabilization via Oracle Erosion: An Empirical Audit and Local Mutation Gate for Flaky Test Repair},
  journal = {Software Quality Journal},
  year    = {2026},
  note    = {Manuscript under review; replication package: https://github.com/789748456v/MuGuard-Flake-SQJ}
}
```
