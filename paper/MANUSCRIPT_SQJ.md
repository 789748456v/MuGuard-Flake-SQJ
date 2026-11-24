# Stabilization via Oracle Erosion: An Empirical Audit and Local Mutation Gate for Flaky Test Repair

**Authors:** Zhijie Li¹ (`lizhijie@mails.neu.edu.cn`), Ketong Liu¹ (`liuketong@mails.neu.edu.cn`), Shaoyang Xu¹ (`xusy3@mails.neu.edu.cn`), Shibo Meng¹ (`mengsb1@mails.neu.edu.cn`), Feiran Gao² (`3120260997@bit.edu.cn`), and Yuli Zhao¹* (`zhaoyl@swc.neu.edu.cn`, Corresponding Author)  
**Affiliations:** ¹Software College, Northeastern University, Shenyang, China; ²School of Computer Science and Technology, Beijing Institute of Technology, Beijing, China  
**Target Venue:** *Software Quality Journal* (Springer Nature)  
**Frozen Evaluation Protocol:** `2.3.0-certified` (`SHA-256: eecf64845bb3810e`)

---

## Abstract

Automated flaky test repair pipelines increasingly pair static/dynamic context localization with iterative large language model (LLM) synthesis, yet candidate acceptance remains governed by a *single-sided stabilization validator*: any patch that compiles and passes nondeterministic rerun schedules ($W^+$) without overt assertion deletion is accepted. Because test repair modifies the oracle and fixture themselves, optimizing solely for stability induces **Stabilization via Oracle Erosion**—converting intermittently failing tests into green yet fault-blind tests.

Under a pre-frozen `v2.0.0-frozen` protocol hardened via documented post-hoc audit revisions (`2.3.0-certified`) across **208** attempted tests (**$N = 203$** exact node-attributed eligible tests across **69** Java and Python repositories; **4,842** target and **1,037** partner `3/3` clean-$e_0$ focal obligations, plus **3,518** frozen held-out external obligations $M_{\text{ext}}$ across **177** tests), we audit historical SOTA artifacts, five LLMs across four model families, a structural correspondence AST guard (`B3+`), a same-gate scalar retry baseline (`B4`), and our closed-fail mutation-preserving gate **MuGuard-Flake** (`SAFE_PASS` / `EROSION_RETRY` / `ABSTAIN`). Auditing authoritative upstream `:P` patches through the unified `2.3.0-certified` evaluator shows that **27.27%** (`9/33`) of $W^+$-passing FlakyDoctor GPT-4 patches and **23.81%** (`10/42`) of partner-matched iPFlakies patches lose baseline focal obligations. Across multi-round ($K=5$) repair on `deepseek-flash`, `deepseek-v4-pro`, and confirmatory `qwen3.7-plus`, **MuGuard-Flake** exposes a clear risk--coverage trade-off: it eliminates leaked baseline erosion (`0.00%`), improves operational `SoundFix%` over `B0`, `B3`, and `B3+`, and lowers conditional unseen-$M_{\text{ext}}$ obligation loss among accepted patches relative to `B0`/`B3+`, while unconditional end-to-end held-out yield ($Y_s$) over the fixed frozen cohort $E$ is statistically inconclusive (95% clustered CIs span zero and establish neither superiority nor equivalence/non-inferiority). In a prefix-forked mechanism study ($n = 128$), localized surviving-mutant witness packages (`A3`) substantially raise post-erosion recovery over concise scalar counts (`A1`). Finally, post-hoc diff-grounded PR adjudication quantifies the conservative full dual-role gate's false-rejection rate (**20.51%** on legitimate test-only repairs), and uncached single-test target mutation probing completes in **9.51 s** median on Java and **3.72 s** on Python.

---

## 1. Benchmark Attrition & Obligation Profile (Table 1)

- **Cohort Accounting:** `208` initial candidate tests $\to$ **`203`** eligible (`110` Java across `35` Maven repos; `93` Python across `34` `pytest` repos). `5` instances fell below `3` strictly node-attributed `3/3` clean-$e_0$ kills and are reported transparently via both primary (`n=203`) and Intent-to-Treat (`N=208`) metrics.
- **Partner & External Coverage:** `126/142` OD tests carry `3/3` attributed partner obligations (`1,037` total); `16` zero-obligation partner instances are protected by closed-fail `ABSTAIN_PARTNER_UNCOVERED` when partner/shared scope is edited. Frozen external validation covers `3,518` disjoint $M_{\text{ext}}$ obligations across `177` tests.

---

## 2. Historical SOTA Artifact Audit (`B1`, Table 2) & PR Reference Adjudication (Table 3)

| Historical Artifact Cohort | Official Avail. | Target Mismatch | Verbatim Compiled | Pass@$W^+$ | Eroded ($<1$) | Leak Rate ($W^+$) | Operational SoundFix |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| FlakyDoctor GPT-4 (CSV `:P`) | 75 | 20 | 38 | 33 (44.00%) | **9** | **27.27%** | 24 (32.00%) |
| FlakyDoctor Magicoder (CSV `:P`) | 13 | 6 | 4 | 4 (30.77%) | **0** | **0.00%** | 4 (30.77%) |
| iPFlakies Python (`Patches.csv`) | 77 | 0 | 60 | 42 (54.55%) | **10** | **23.81%** | 32 (41.56%) |

### PR-Linked Reference Stratification & False-Rejection Trade-Off
- **Provenance Breakdown (`108` instances / `50` unique PRs):** `{'PR_Opened': 44, 'PR_Accepted_TestOnly': 37, 'PR_Accepted_WithProd': 18, 'PR_InspiredAFix': 5, 'PR_Rejected': 3, 'PR_DeveloperWontFix': 1}`.
- **Accepted-PR Rejection Rate (`Accepted`, `src/test` only, $W^+$ pass, $n=16$):** `B3 = 1/16 (6.25%)`, `B3+ = 3/16 (18.75%)`, `MuGuard-Flake = 2/16 (12.50%)`.
- **Adjudicated Legitimate-Repair False-Rejection Rate ($n=39$):** `B3 = 1/39 (2.56%)`, `B3+ = 4/39 (10.26%)`, `MuGuard-Flake = 8/39 (20.51%)`.

---

## 3. Multi-Round ($K=5$) Repair Matrix & Non-Circular External Validation (Tables 4–6)

| Model ($K=5$) | Strategy | Pass@$W^+$ | Leaked ($W^+$) | Operational SoundFix% [95% Cluster CI] | ITT-208% | Held-Out $M_{\text{ext}}$ Loss | Strict Yield $Y_s$ | Stress Yield $Y_s^{\text{stress}}$ |
| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `deepseek-flash` ($n=202$) | B0: Unconstrained | 196 (97.03%) | 39 (19.90%) | 71.29% [59.7, 82.1] | 69.23% | 14 (8.09%) | 145/177 (81.92%) | 157/177 (88.70%) |
|  | B2: Prompt Guard | 197 (97.52%) | 40 (20.30%) | 71.29% [61.8, 81.1] | 69.23% | 17 (9.83%) | 140/177 (79.10%) | 155/177 (87.57%) |
|  | B3: Syntactic Guard | 198 (98.02%) | 41 (20.71%) | 71.29% [61.8, 81.1] | 69.23% | 17 (9.83%) | 140/177 (79.10%) | 155/177 (87.57%) |
|  | B3+: Structural Guard | 195 (96.53%) | 41 (21.03%) | 69.80% [59.6, 80.1] | 67.79% | 15 (8.77%) | 140/177 (79.10%) | 155/177 (87.57%) |
|  | B4: Scalar Gate Loop | 162 (80.20%) | 0 (0.00%) | 80.20% [69.0, 90.7] | 77.88% | 5 (3.38%) | 127/177 (71.75%) | 142/177 (80.23%) |
|  | **MuGuard-Flake (Ours)** | 177 (87.62%) | 0 (0.00%) | 87.62% [78.7, 95.2] | 85.10% | 7 (4.43%) | 135/177 (76.27%) | 150/177 (84.75%) |
| `deepseek-v4-pro` ($n=188$) | B0: Unconstrained | 177 (94.15%) | 42 (23.73%) | 69.68% [59.6, 80.2] | 62.98% | 15 (9.55%) | 128/177 (72.32%) | 138/177 (77.97%) |
|  | B2: Prompt Guard | 174 (92.55%) | 43 (24.71%) | 67.02% [57.1, 77.1] | 60.58% | 18 (11.61%) | 125/177 (70.62%) | 136/177 (76.84%) |
|  | B3: Syntactic Guard | 181 (96.28%) | 46 (25.41%) | 69.15% [60.0, 78.8] | 62.50% | 18 (11.32%) | 129/177 (72.88%) | 140/177 (79.10%) |
|  | B3+: Structural Guard | 182 (96.81%) | 49 (26.92%) | 68.09% [58.6, 78.1] | 61.54% | 18 (11.32%) | 129/177 (72.88%) | 140/177 (79.10%) |
|  | B4: Scalar Gate Loop | 147 (78.19%) | 0 (0.00%) | 78.19% [65.8, 89.4] | 70.67% | 5 (3.70%) | 118/177 (66.67%) | 129/177 (72.88%) |
|  | **MuGuard-Flake (Ours)** | 160 (85.11%) | 0 (0.00%) | 85.11% [73.2, 94.2] | 76.92% | 7 (4.83%) | 126/177 (71.19%) | 137/177 (77.40%) |
| `qwen3.7-plus` ($n=203$) | B0: Unconstrained | 180 (88.67%) | 37 (20.56%) | 65.02% [53.2, 76.7] | 63.46% | 19 (11.66%) | 128/177 (72.32%) | 138/177 (77.97%) |
|  | B2: Prompt Guard | 180 (88.67%) | 35 (19.44%) | 67.49% [56.9, 78.6] | 65.87% | 17 (10.49%) | 130/177 (73.45%) | 144/177 (81.36%) |
|  | B3: Syntactic Guard | 180 (88.67%) | 36 (20.00%) | 67.00% [55.6, 78.9] | 65.38% | 17 (10.56%) | 129/177 (72.88%) | 143/177 (80.79%) |
|  | B3+: Structural Guard | 175 (86.21%) | 39 (22.29%) | 63.05% [50.9, 75.8] | 61.54% | 16 (10.13%) | 127/177 (71.75%) | 141/177 (79.66%) |
|  | B4: Scalar Gate Loop | 153 (75.37%) | 0 (0.00%) | 75.37% [64.7, 85.6] | 73.56% | 4 (2.88%) | 119/177 (67.23%) | 134/177 (75.71%) |
|  | **MuGuard-Flake (Ours)** | 161 (79.31%) | 0 (0.00%) | 79.31% [68.3, 89.2] | 77.40% | 6 (4.11%) | 124/177 (70.06%) | 139/177 (78.53%) |

### Cross-Family Single-Pass ($K=1$) Gate Screening (Table 5)

| Model ($K=1$) | Strategy | Pass@$W^+$ | Leaked ($W^+$) | Operational SoundFix% [95% Cluster CI] |
| :--- | :--- | ---: | ---: | ---: |
| `kimi-k2.5` ($n=203$) | B0: Unconstrained | 112 (55.17%) | 30 (26.79%) | 40.39% [29.7, 52.3] |
|  | B3: Syntactic Guard | 112 (55.17%) | 29 (25.89%) | 40.89% [29.6, 53.4] |
|  | B3+: Structural Guard | 100 (49.26%) | 21 (21.00%) | 38.92% [27.3, 51.7] |
|  | **MuGuard-Flake Gate** | 83 (40.89%) | 0 (0.00%) | 40.89% [29.6, 53.4] |
| `glm-5` ($n=203$) | B0: Unconstrained | 133 (65.52%) | 34 (25.56%) | 47.29% [35.2, 61.0] |
|  | B3: Syntactic Guard | 126 (62.07%) | 30 (23.81%) | 46.31% [34.8, 59.1] |
|  | B3+: Structural Guard | 117 (57.64%) | 25 (21.37%) | 44.33% [32.7, 57.5] |
|  | **MuGuard-Flake Gate** | 95 (46.80%) | 0 (0.00%) | 46.80% [35.4, 59.7] |

---

## 4. Prefix-Forked Witness Experiment ($n = 128$ forks across 61 tests / 20 repos, Table 8)

- **A1 (Concise Scalar Ratio $k/M$):** `55/128` (`42.97%`, 95% CI `[20.13, 70.8]`)
- **A2 (Matched Misleading Location Stress Test):** `42/116` (`36.21%`, 95% CI `[21.02, 56.45]`)
- **A3 (True Surviving Mutant Witness):** `91/128` (`71.09%`, 95% CI `[54.11, 87.6]`)
- **Primary Mechanism Comparison (`A3 - A1`):** `+28.12 pp` (95% cluster CI `[11.84, 43.56]`, $p_{\text{cluster}} = 0.0049$); **Secondary Stress Test (`A3 - A2`):** `+37.07 pp` ($p_{\text{cluster}} = 0.0005$).

---

## 5. Dual-Axis Patch-Mechanism Codebook, Uncached Latencies, & Abstention Root Causes (Tables 7 & 9)

- **Unique Eroded Patches:** `400` unique normalized candidate fingerprints (`979` appearances across `74` tests / `27` projects); `396` bypass `B3` and `335` bypass `B3+`.
- **Uncached In-Loop Target Mutation Latency:** Java median `9.51 s` (P90 `16.56 s`); Python median `3.72 s` (P90 `9.88 s`).
- **Terminal Abstention States by Model:** `{"deepseek-flash": {"total_abstained": 25, "terminal_states": {"ABSTAIN_EROSION": 5, "ABSTAIN_COMPILE": 2, "ABSTAIN_PARTNER_UNCOVERED": 3, "ABSTAIN_UNSTABLE": 8, "SANITY_REJECT_FINAL": 7}, "compile_failure_subtypes": {"JAVA_MISSING_SYMBOL_OR_IMPORT": 2}}, "deepseek-v4-pro": {"total_abstained": 28, "terminal_states": {"ABSTAIN_EROSION": 6, "ABSTAIN_PARTNER_UNCOVERED": 2, "ABSTAIN_UNSTABLE": 5, "SANITY_REJECT_FINAL": 14, "ABSTAIN_COMPILE": 1}, "compile_failure_subtypes": {"MISSING_DELIMITER_OR_EMPTY_PARSE": 1}}, "qwen3.7-plus": {"total_abstained": 42, "terminal_states": {"ABSTAIN_COMPILE": 1, "ABSTAIN_EROSION": 9, "ABSTAIN_UNSTABLE": 11, "SANITY_REJECT_FINAL": 18, "ABSTAIN_PARTNER_UNCOVERED": 3}, "compile_failure_subtypes": {"JAVA_MISSING_SYMBOL_OR_IMPORT": 1}}}`.
