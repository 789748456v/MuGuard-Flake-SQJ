# Stabilization via Oracle Erosion: An Empirical Audit and Local Mutation Gate for Flaky Test Repair

Official replication package, certified benchmark artifacts, blinded dual-auditor reliability archive, and automated verification suite for the manuscript submitted to *Software Quality Journal* (Springer Nature).

**Authors:** Zhijie Li¹ · Ketong Liu¹ · Shaoyang Xu¹ · Shibo Meng¹ · Feiran Gao² · Yuli Zhao¹\*  
¹ Software College, Northeastern University, Shenyang 110169, China  
² School of Computer Science and Technology, Beijing Institute of Technology, Beijing 100081, China  
\* Corresponding author: `zhaoyl@swc.neu.edu.cn`

---

## Study Overview

Automated flaky test repair pipelines increasingly pair context localization with large language models (LLMs) to eliminate intermittent continuous integration (CI) failures. However, nearly every pipeline accepts a candidate repair once it compiles, passes rerun schedules ($W^+$), and retains assertion statements. Because flaky test repair edits the test oracle and fixture themselves, optimizing solely for green runs invites **Stabilization via Oracle Erosion**: tests turn permanently green while silently losing sensitivity to future production faults.

This replication package accompanies our dual-language empirical audit and **MuGuard-Flake** fail-closed local mutation gate across:
- **203 eligible flaky tests** (208 attempted; 5 excluded below the three-obligation floor) across **69 Java and Python repositories** (**4,842** target baseline obligations $M_{\text{det}}$, **1,037** partner obligations, and **3,518** disjoint held-out obligations $M_{\text{ext}}$ across 177 tests).
- **Historical repair artifacts** (`FlakyDoctor` GPT-4 & Magicoder; `iPFlakies` partner-matched patches), **108 PR-linked Java test instances across 50 GitHub pull requests**, and **five modern LLMs from four vendors** (`deepseek-flash`, `deepseek-v4-pro`, `qwen3.7-plus` at $K=5$; `kimi-k2.5` and `glm-5` at $K=1$).
- **1,243 unique held-out candidate patches**, **142 unmodified-repository repeated-pollution controls**, **128 prefix-forked witness ablation trajectories** across 20 repositories, and a **two-stage blinded semantic adjudication archive** (Stage-1 author diff coding + Stage-2 physically blinded OpenAI Codex CLI v0.153.4 / `gpt-6-astra` cross-audit over 219 diffs with unweighted Cohen's $\kappa$ and 35 third-pass arbitrations).

### Key Headline Findings

- **Prevalence of Silent Oracle Erosion & PR Boundaries (RQ1):** Roughly one in five to one in four $W^+$-passing patches silently drops baseline obligations across historical tools (`27.27%` FlakyDoctor GPT-4; `23.81%` iPFlakies) and unconstrained LLMs (`19.90%–23.73%` at $K=5$; `25.56%–26.79%` at $K=1$), opening a `24–26 pp` Proxy Inflation Gap. On audited developer pull requests, strict `MuGuard-Flake` gating incurs a `20.51%` (`8/39`) false-rejection rate on legitimate fixes (`22.50%` sensitivity including one Apache Avro responsibility shift) and misses `2/3` subtle weakenings outside default mutators.
- **Behavioral Erosion Mechanisms & Static-Guard Blind Spots (RQ2):** Over four-fifths (`335/400`) of unique eroded patches bypass an enhanced Structural Guard via precondition/singleton shifts (`119/120`), delimiter/format normalizations (`86/112`), partner/shared-scope edits (`43/53`), and focal call bypasses (`48/52`). Static rejections even reduce out-of-sample sound-fix yield on confirmatory `qwen3.7-plus` (`-3.9 pp`).
- **Fail-Closed Gating, Asymmetric CI Break-Even, & Witness Recovery (RQ3):** `MuGuard-Flake` eliminates in-loop erosion (`0.00%` across all five models), lifts operational `SoundFix%` by `+12.3` to `+17.8 pp` over static guards (all $p_{\text{Holm}} \le 0.014$), and cuts conditional unseen $M_{\text{ext}}$ loss among accepted patches by `45.2%–64.8%`. Conservative abstention leaves unconditional end-to-end held-out yield $Y_s$ statistically inconclusive (`-5.7` to `+0.6 pp`), yet asymmetric CI cost analysis shows `MuGuard-Flake` achieves strictly lower expected CI cost than every non-mutation baseline whenever a silent regression costs more than `1.25×–2.43×` an abstention review (`1.27×–1.63×` vs. Structural Guard). Across `128` prefix-forked erosion states, localized surviving-mutant witnesses recover `71.09%` of eroded patches versus `42.97%` for bare scalar ratios (`+28.12 pp`, $p = 0.0049$) and `36.21%` for misleading witnesses.
- **Uncached Probing Latency & Subset Sensitivity (RQ4):** Uncached in-loop target mutation probing finishes in a median of `9.51 s` on Java and `3.72 s` on Python (replaying only frozen $M_{\text{det}}$, median 14 mutants, vs. `12.29 s` full $M_{\text{focal}}$ baseline sweeps), whereas single LLM calls consume `53–80 s`. Probing the full target-and-partner pool avoids both small-subset recall drops (`50.8%` at Top-5, `92.3%` at Top-25) and partner-only blind spots (`99.0%` target-only cap).

---

## Manuscript Table & Figure Crosswalk

Every quantitative table (`Tables 2–12`), macro (`paper/generated_macros.tex`), and figure (`Figures 1–5`) in the manuscript is programmatically generated and verified against the frozen JSON artifacts in `data/`.

| Manuscript Element | Backing Artifact(s) in `data/` | Generator / Verifier Script |
| :--- | :--- | :--- |
| **Table 1** (Related Work Positioning) | Literature synthesis (`paper/sections/sec2_background_related.tex`) | `paper/main.tex` |
| **Table 2** (Benchmark Cohort & Base Probe) | `v2_frozen_benchmark_light.json`, `v2_study_summary_metrics.json` | `src/generate_v2_latex_and_verify.py` |
| **Table 3** (Historical Artifacts: FlakyDoctor & iPFlakies) | `b1_provenance_manifest.json`, `v2_study_summary_metrics.json` | `src/generate_v2_latex_and_verify.py` |
| **Table 4** (Developer PR False-Rejection Audit) | `pr_reference_manifest.json`, `pr_independent_adjudication_input.json`, `pr_semantic_adjudication.json` | `src/generate_v2_latex_and_verify.py` |
| **Table 5** (400-Patch Taxonomy & 60-Patch Audit) | `taxonomy_audit_codebook.json`, `taxonomy_manual_audit.json`, `v24_codex_blinded_reliability_audit.json` | `src/generate_v2_latex_and_verify.py` |
| **Table 6** (Multi-Round $K=5$ Operational Outcomes) | `v2_results_{deepseek-flash,deepseek-v4-pro,qwen3.7-plus}.json`, `v2_study_summary_metrics.json` | `src/generate_v2_latex_and_verify.py` |
| **Table 7** (Single-Pass $K=1$ Screening: Kimi K2.5 & GLM-5) | `v2_round1_{kimi-k2.5,glm-5}.json`, `v2_study_summary_metrics.json` | `src/generate_v2_latex_and_verify.py` |
| **Table 8** (Fixed-Cohort Held-Out $M_{\text{ext}}$ & $Y_s$) | `v2_heldout_validation.json`, `mext_loss_adjudication_input.json`, `v23_upstream_repeated_pollution_controls.json` | `src/generate_v2_latex_and_verify.py` |
| **Table 9** (Project-Clustered Bootstrap & Sign-Flip Inference) | `v2_study_summary_metrics.json` | `src/v2_statistical_and_audit_engine.py`, `src/generate_v2_latex_and_verify.py` |
| **Table 10** (Prefix-Forked Witness Ablation, $n=128$) | `v2_forked_ablation_results.json`, `v2_study_summary_metrics.json` | `src/generate_v2_latex_and_verify.py` |
| **Table 11** (Offline Pre-Comp. & Uncached Stage Latencies) | `v2_frozen_benchmark_light.json`, `v2_results_*.json`, `v2_study_summary_metrics.json` | `src/generate_v2_latex_and_verify.py` |
| **Table 12** (Offline Subset-Sampling Sensitivity) | `v2_study_summary_metrics.json` (`topk_offline_sensitivity`) | `src/generate_v2_latex_and_verify.py` |
| **Figure 1** (`MuGuard-Flake` Architecture & Running Example) | `figures/fig0_muguard_architecture.{pdf,png}` | `src/generate_architecture_figure.py` |
| **Figures 2–5** (Inflation Gap, Forest Plot, Forked Ablation, Latency/Top-$k$) | `figures/fig{1,2,4,5}_*.{pdf,png}` | `src/v2_statistical_and_audit_engine.py` |
| **Blinded Reliability Audit** (Section 5.2 & 8.1 $\kappa$ + 35 Arbitrations) | `v24_codex_blinded_reliability_audit.json` | Archived blinded Stage-2 audit & arbitration ledger |

---

## Repository Structure

```text
MuGuard-Flake-SQJ/
├── README.md                                        # Study overview, crosswalk, and reproduction guide
├── LICENSE                                          # MIT License
├── requirements.txt                                 # Python dependencies
├── FROZEN_AUDIT_PROTOCOL.md                         # Locked experimental and adjudication protocol
├── paper/                                           # Complete LaTeX manuscript sources, tables, figures, and compiled PDF
│   ├── main.pdf                                     # 31-page submission PDF (0 errors, 0 warnings, 0 overfull hboxes)
│   ├── main.tex                                     # Master LaTeX entry point
│   ├── references.bib                               # 30 verified BibTeX references
│   ├── generated_macros.tex                         # Auto-synchronized numeric macros
│   ├── MANUSCRIPT_SQJ.md                            # Synchronized Markdown manuscript
│   ├── FULL_STUDY_REPORT.md                         # Synchronized study report
│   ├── sections/                                    # sec1_intro.tex .. sec8_threats_conclusion.tex
│   ├── tables/                                      # Auto-generated LaTeX tables (Tables 2–12)
│   └── figures/                                     # Publication vector PDFs and 300-DPI PNGs
├── figures/                                         # Top-level mirror of all 5 publication figures
├── data/                                            # Certified v2.3/v2.4 benchmarks, traces, audits, and LLM caches
│   ├── v2_study_summary_metrics.json                # Master aggregated statistical & audit summary
│   ├── v2_frozen_benchmark_light.json               # 208 attempted / 203 eligible tests with M_det, partner, and M_ext pools
│   ├── v2_results_{deepseek-flash,deepseek-v4-pro,qwen3.7-plus}.json
│   ├── v2_round1_{kimi-k2.5,glm-5}.json
│   ├── v2_heldout_validation.json                   # 1,243 held-out candidate evaluations (strict & stress schedules)
│   ├── v2_forked_ablation_results.json              # 128 prefix-forked trajectories across 20 repositories
│   ├── v23_upstream_repeated_pollution_controls.json
│   ├── v23_r6_live_java_counterexamples.json
│   ├── v23_reconciliation_manifest.json
│   ├── b1_provenance_manifest.json
│   ├── pr_{reference_manifest,independent_adjudication_input,semantic_adjudication}.json
│   ├── taxonomy_{audit_codebook,manual_audit}.json
│   ├── mext_loss_adjudication_input.json
│   ├── v24_codex_blinded_reliability_audit.json     # Stage-2 blinded Codex reliability (kappa) & 35 arbitration records
│   └── llm_cache_*.json                             # Archived deterministic temperature=0.0 LLM prompt-response caches
└── src/                                             # MuGuard-Flake implementation, statistical engine, and verifiers
    ├── ast_guard_and_prompts.py                     # Java static guards & differential witness prompt builder
    ├── python_ast_guard_and_prompts.py              # Python static guards & witness prompt builder
    ├── v2_guards_and_attribution.py                 # Structural guard (B3+), 8-tuple PIT XML attribution, Top-k engine
    ├── v2_pytest_attribution_runner.py              # Full-node, phase-isolated pytest & repeated-pollution runner
    ├── remote_and_llm.py                            # Environment-variable-configured API/execution client (no secrets)
    ├── remote_core.py / remote_python_core.py       # Java (NonDex/PIT/JUnit4) & Python (coverage/AST/pytest) backends
    ├── remote_eval_v2.py / remote_v2_freeze_benchmark_and_mext.py / remote_v23_certify.py
    ├── run_full_study_matrix.py / run_v2_confirmatory_campaign.py
    ├── v2_statistical_and_audit_engine.py           # Whole-project clustered inference & empirical figure generator
    ├── generate_architecture_figure.py              # Figure 1 architecture diagram generator
    ├── generate_v2_latex_and_verify.py              # Automated macro & table generator + consistency assertions
    ├── generate_v2_markdown_deliverables.py         # Synchronized Markdown deliverable generator
    ├── verify_v23_certification.py                  # 10-invariant hard-failing end-to-end certification verifier
    ├── analyze_and_security_audit.py                # Automated secret-leak and isolation checker
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

Verifies exact JUnit 3/4/5 and `pytest` node/phase attribution, lossless 8-field PIT XML identity, shared-scope diff detection, Structural Guard (`B3+`) rules, fail-closed indeterminate handling, and single-lifecycle repeated-pollution ordering (`19/19 OK`).

### 3. Verify All 10 End-to-End Certification Invariants

```bash
python src/verify_v23_certification.py
```

Audits all `v2.3.0-certified` invariants directly from `data/`: zero empty PIT indices or `NONE` attribution granularities across 110 Java eligible tests, zero `B3+` guard flips or endpoint denominator mismatches across all 5 models, 100% unified B1 provenance, all 142 OD repeated-pollution controls and 73 verified stress exemptions, atomic same-tree PR gating, zero unadjudicated $M_{\text{ext}}$ losses, `100.0%` joint Top-$k$ recall, and zero secret leaks.

### 4. Recompute All Clustered Statistics, Figures, and LaTeX Tables

```bash
python src/generate_architecture_figure.py
python src/v2_statistical_and_audit_engine.py
python src/generate_v2_latex_and_verify.py
```

To recompile the 31-page PDF from LaTeX sources (`pdflatex` + `bibtex`):

```bash
cd paper
pdflatex -interaction=nonstopmode main.tex
bibtex main
pdflatex -interaction=nonstopmode main.tex
pdflatex -interaction=nonstopmode main.tex
```

> **Note on Re-Executing Live LLM Calls or Docker Container Builds:** All archived LLM responses (temperature `0.0`, November 2026 campaign) and container evaluations are preserved in `data/` so every statistic, table, and figure reproduces offline without API keys. To invoke live vendor APIs (`deepseek-flash`, `deepseek-v4-pro`, `qwen3.7-plus`, `kimi-k2.5`, `glm-5`), set `DEEPSEEK_API_KEY` or `DASHSCOPE_API_KEY` via environment variables (never commit key files).

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
