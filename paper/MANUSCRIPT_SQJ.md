# Stabilization via Oracle Erosion: An Empirical Audit and Local Mutation Gate for Flaky Test Repair

**Authors:** Zhijie Li¹ (`lizhijie@mails.neu.edu.cn`), Ketong Liu¹ (`liuketong@mails.neu.edu.cn`), Shaoyang Xu¹ (`xusy3@mails.neu.edu.cn`), Shibo Meng¹ (`mengsb1@mails.neu.edu.cn`), Feiran Gao² (`3120260997@bit.edu.cn`), and Yuli Zhao¹* (`zhaoyl@swc.neu.edu.cn`, Corresponding Author)  
**Affiliations:** ¹Software College, Northeastern University, Shenyang, China; ²School of Computer Science and Technology, Beijing Institute of Technology, Beijing, China  
**Target Venue:** *Software Quality Journal* (Springer Nature)  
**Certified Protocol & Submission PDF:** `3.0.0-certified` (`paper/main.pdf`, 30 pages; supplementary tables in `ONLINE_RESOURCE_1.md`)

---

## Abstract

Automated flaky test repair tools increasingly combine context localization with large language models to remove intermittent continuous integration failures. Nearly every pipeline, however, accepts a candidate patch once it compiles, passes rerun schedules, and keeps its assertion statements. Because test repair edits the test oracle and fixture themselves, optimizing solely for green runs invites *Stabilization via Oracle Erosion*: repaired tests turn permanently green while losing their ability to catch future production faults. Across 203 flaky Java and Python tests spanning historical repair tools, developer pull requests, and five modern language models, roughly one in five to one in four stabilization-passing patches silently drops baseline fault-detection obligations. Static syntax rules, line and branch coverage gates, and cross-vendor language-model judges still miss over two-thirds of eroded patches because upstream behavioral shifts preserve assertion syntax and code execution. We introduce **MuGuard-Flake**, a fail-closed local mutation gate that verifies modified tests and their shared-state partners in seconds per candidate. **MuGuard-Flake** eliminates in-loop erosion across all five models, raises operational sound-fix yield substantially over static, coverage, and model-judge gates, and cuts unseen fault loss among accepted patches by two-fifths to two-thirds. During repair retries, localized surviving-mutant witnesses also recover far more eroded drafts than numeric counts (71% versus 43%). Strict conservation carries explicit trade-offs: conservative abstention leaves unconditional held-out yield statistically inconclusive and rejects 20.5% of legitimate developer pull requests, though witness-guided triage eliminates those false rejections.

---

## 1. Benchmark Profile & Eight-Gate Matrix (`100%` Complete Across All 5 Models)

- **Cohort:** `208` attempted tests $\to$ **`N = 203`** eligible tests across **`69`** Java and Python repositories (`110` Java across `35` Maven repos; `93` Python across `34` `pytest` repos), covering **`4,842`** target `3/3` baseline obligations, **`1,037`** partner obligations, and **`3,518`** disjoint held-out obligations $M_{\text{ext}}$ across `177` tests.
- **Complete Model Matrix:** `203/203` (`100%`) multi-round ($K=5$) repair across `deepseek-flash`, `deepseek-v4-pro`, and confirmatory `qwen3.7-plus` (`0` API omissions on $N=203$ and $|E|=177$), plus `203/203` single-pass ($K=1$) screening on `kimi-k2.5` and `glm-5`.
- **Eight Gating Strategies:** Unconstrained, Prompt Guard, Syntactic Guard, Structural Guard, dynamic Coverage Gate (JaCoCo + `coverage.py`), cross-vendor LLM-Judge Gate (`qwen3.7-plus` / `glm-5`), Scalar Gate, and **MuGuard-Flake**.
- **Two-Stage Blinded Ensemble Audit (`2,310` Independent Ratings):** Stage-1 frozen author rubric paired with 10 stateless, order-permuted OpenAI Codex (`gpt-6-astra`) sessions across all 53 cleanly applied PRs (Fleiss' $\kappa = 0.9945$; binary legitimacy $\kappa = 1.0000$), 60 active stratified eroded patches ($\kappa = 0.9756$), and all 118 unique held-out $M_{\text{ext}}$-loss patches ($\kappa = 0.9110$).

---

## 2. Historical Artifacts, Developer PRs, & Multi-Paradigm Blind Spots (RQ1–RQ2)

- **Historical Artifacts (`Table 3` & `Table S-1`):** `27.27%` (`9/33`) of stabilized `FlakyDoctor` GPT-4 patches and `23.81%` (`10/42`) of partner-matched `iPFlakies` patches silently lose baseline obligations; on those exact matched cohorts, **MuGuard-Flake** lifts SoundFix% to `92.00%–94.67%` and `70.13%–85.71%` with `0.00%` leaked erosion.
- **Developer PRs & Witness-Guided Hybrid Triage (`Table 4` & `Table S-2`):** Strict gating rejects `8/39` (`20.51%`) broadly legitimate PRs (`1/29 = 3.4%` under strict 10-session ensemble coding), whereas attaching surviving-mutant witnesses during triage reduces legitimate PR false rejections to **`0/39` (`0.00%`)** while preserving the Nacos weakening catch.
- **Four-Paradigm Bypass (`Table 5`):** Across `400` unique eroded patches, Syntactic Guard misses `99.0%`, Structural Guard misses `83.8%` (`335/400`), cross-vendor LLM-Judge misses `78.3%` (`313/400`), and dynamic Coverage Gate misses `70.3%` (`265/377`, `83.1%` in Java).

---

## 3. Complete Multi-Round ($K=5$) Operational & Held-Out Matrix (RQ3, Tables 6 & 8)

| Model (`n=203, |E|=177`) | Strategy | Pass@$W^+$ | Leaked ($W^+$) | Operational SoundFix% [95% CI] | Held-Out $M_{\text{ext}}$ Loss (`W/S/L`) | Strict $Y_s$ | Stress $Y_s$ |
| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: |
| `deepseek-flash` | Unconstrained | 196 (96.55%) | 39 (19.90%) | 70.94% [59.5, 81.9] | 14 (8.09%, `4/8/2`) | 145 (81.92%) | 157 (88.70%) |
|  | Prompt Guard | 197 (97.04%) | 40 (20.30%) | 70.94% [61.5, 80.8] | 17 (9.83%, `3/10/4`) | 140 (79.10%) | 155 (87.57%) |
|  | Syntactic Guard | 198 (97.54%) | 41 (20.71%) | 70.94% [61.5, 80.8] | 17 (9.83%, `3/10/4`) | 140 (79.10%) | 155 (87.57%) |
|  | Structural Guard | 195 (96.06%) | 41 (21.03%) | 69.46% [59.3, 79.8] | 15 (8.77%, `3/9/3`) | 140 (79.10%) | 155 (87.57%) |
|  | Scalar Gate | 162 (79.80%) | 0 (0.00%) | 79.80% [68.8, 90.2] | 5 (3.38%, `2/1/2`) | 127 (71.75%) | 142 (80.23%) |
|  | **MuGuard-Flake** | 177 (87.19%) | 0 (0.00%) | 87.19% [78.2, 94.7] | 7 (4.43%, `2/1/4`) | 135 (76.27%) | 150 (84.75%) |
| `deepseek-v4-pro` | Unconstrained | 185 (91.13%) | 44 (23.78%) | 65.52% [55.2, 76.2] | 16 (9.70%, `3/10/3`) | 133 (75.14%) | 145 (81.92%) |
|  | Prompt Guard | 178 (87.68%) | 44 (24.72%) | 63.05% [53.4, 72.9] | 18 (11.32%, `6/8/4`) | 128 (72.32%) | 140 (79.10%) |
|  | Syntactic Guard | 184 (90.64%) | 47 (25.54%) | 64.53% [55.2, 74.5] | 18 (11.11%, `6/8/4`) | 132 (74.58%) | 143 (80.79%) |
|  | Structural Guard | 185 (91.13%) | 50 (27.03%) | 63.55% [53.9, 73.9] | 18 (11.11%, `6/9/3`) | 132 (74.58%) | 143 (80.79%) |
|  | Scalar Gate | 149 (73.40%) | 0 (0.00%) | 73.40% [61.2, 84.9] | 5 (3.65%, `2/0/3`) | 119 (67.23%) | 131 (74.01%) |
|  | **MuGuard-Flake** | 162 (79.80%) | 0 (0.00%) | 79.80% [67.5, 90.2] | 7 (4.76%, `3/2/2`) | 127 (71.75%) | 139 (78.53%) |
| `qwen3.7-plus` | Unconstrained | 180 (88.67%) | 37 (20.56%) | 65.02% [53.2, 76.7] | 19 (11.66%, `7/9/3`) | 128 (72.32%) | 138 (77.97%) |
|  | Prompt Guard | 180 (88.67%) | 35 (19.44%) | 67.49% [56.9, 78.6] | 17 (10.49%, `6/8/3`) | 130 (73.45%) | 144 (81.36%) |
|  | Syntactic Guard | 180 (88.67%) | 36 (20.00%) | 67.00% [55.6, 78.9] | 17 (10.56%, `6/8/3`) | 129 (72.88%) | 143 (80.79%) |
|  | Structural Guard | 175 (86.21%) | 39 (22.29%) | 63.05% [50.9, 75.8] | 16 (10.13%, `6/8/2`) | 127 (71.75%) | 141 (79.66%) |
| | Coverage Gate (`n=195`) | 165 (84.62%) | 28 (16.97%) | 66.15% [55.1, 77.4] | 13 (8.67%, `6/5/2`) | 122 (72.19%) | 136 (80.47%) |
| | LLM-Judge Gate | 165 (81.28%) | 22 (13.33%) | 66.50% [55.4, 77.8] | 10 (6.76%, `4/4/2`) | 122 (68.93%) | 134 (75.71%) |
|  | Scalar Gate | 153 (75.37%) | 0 (0.00%) | 75.37% [64.7, 85.6] | 4 (2.88%, `2/0/2`) | 119 (67.23%) | 134 (75.71%) |
|  | **MuGuard-Flake** | 161 (79.31%) | 0 (0.00%) | 79.31% [68.3, 89.2] | 6 (4.11%, `2/0/4`) | 124 (70.06%) | 139 (78.53%) |

---

## 4. Complete Prefix-Forked Witness Experiment (`129/129` = `100%`) & CI Runtime Trade-Offs (RQ3–RQ4)

- **Prefix-Forked Recovery (`Table 10`):** Gate-Only `0/129` (`0.00%`); Scalar Ratio `55/129` (`42.64%`); Misleading Witness `42/129` (`32.56%`); **True Witness `91/129` (`70.54%`)** (`A3 - A1 = +27.91 pp`, $p = 0.0049$; `A3 - A2 = +37.98 pp`, $p = 0.0002$).
- **Uncached Probing Latency & Fair Python Re-Indentation (`Table 11` & `Table S-4`):** Target mutation probing finishes in `9.53 s` median on Java and `3.72 s` on Python (faster than `20.89 s` LLM-Judge calls and far below `53–80 s` repair LLM generation). Fair Python method re-indentation lifts confirmatory `qwen3.7-plus` **MuGuard-Flake** SoundFix% to **`85.22%` (`+5.91 pp`)** with `0.00%` leaked erosion.
- **Online Subset Gating (`Table 12` & `Table S-5`):** Live uniform `Top-10` gating inside `qwen3.7-plus` leaks `12/165` (`7.27%`) eroded patches and drops SoundFix% to `75.37%` (vs. `79.31%` under full **MuGuard-Flake**).

