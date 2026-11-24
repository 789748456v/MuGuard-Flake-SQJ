"""
FT-1 v2.0.0-frozen Synchronized Markdown Deliverables Generator (`FULL_STUDY_REPORT.md` & `paper/MANUSCRIPT_SQJ.md`).
Rebuilds both Markdown deliverables directly from `data/v2_study_summary_metrics.json` with zero internal
project reading codes (`N14/N33/N59/N60`), honest operational & external boundaries, and exact table parity.
"""
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent))
from remote_and_llm import DATA_DIR, FT1_ROOT

PAPER_DIR = FT1_ROOT / "paper"


def fp(p: Any) -> str:
    if p is None:
        return "---"
    pv = float(p)
    return "<0.0001" if pv < 0.0001 else f"{pv:.4f}"


def fpct(v: Any) -> str:
    return "---" if v is None else f"{float(v):.2f}%"


def fret(v: Any) -> str:
    return "---" if v is None else f"{float(v):.4f}"


def build_v2_manuscript_md(S: Dict[str, Any]) -> str:
    b = S["benchmark"]
    pr = S["pr_adjudication_and_false_rejection"]
    b1 = S["b1_authoritative_audit"]
    k5 = S["k5_models"]
    k1 = S["k1_models"]
    ab = S["forked_ablation"]
    tax = S["taxonomy_dual_axis"]
    tk = S["topk_offline_sensitivity"]
    lat = S["latency_and_abstentions"]

    k5_order = [m for m in ("deepseek-flash", "deepseek-v4-pro", "qwen3.7-plus") if m in k5]
    k1_order = [m for m in ("kimi-k2.5", "glm-5") if m in k1]

    lines: List[str] = [
        "# Stabilization via Oracle Erosion: An Empirical Audit and Local Mutation Gate for Flaky Test Repair",
        "",
        "**Authors:** Zhijie Li¹ (`lizhijie@mails.neu.edu.cn`), Ketong Liu¹ (`liuketong@mails.neu.edu.cn`), Shaoyang Xu¹ (`xusy3@mails.neu.edu.cn`), Shibo Meng¹ (`mengsb1@mails.neu.edu.cn`), Feiran Gao² (`3120260997@bit.edu.cn`), and Yuli Zhao¹* (`zhaoyl@swc.neu.edu.cn`, Corresponding Author)  ",
        "**Affiliations:** ¹Software College, Northeastern University, Shenyang, China; ²School of Computer Science and Technology, Beijing Institute of Technology, Beijing, China  ",
        "**Target Venue:** *Software Quality Journal* (Springer Nature)  ",
        f"**Frozen Evaluation Protocol:** `{S['harness_version']}` (`SHA-256: {S['exec_benchmark_sha256'][:16]}`)",
        "",
        "---",
        "",
        "## Abstract",
        "",
        "Automated flaky test repair pipelines increasingly pair static/dynamic context localization with iterative large language model (LLM) synthesis, yet candidate acceptance remains governed by a *single-sided stabilization validator*: any patch that compiles and passes nondeterministic rerun schedules ($W^+$) without overt assertion deletion is accepted. Because test repair modifies the oracle and fixture themselves, optimizing solely for stability induces **Stabilization via Oracle Erosion**—converting intermittently failing tests into green yet fault-blind tests.",
        "",
        f"Under a pre-frozen `v2.0.0-frozen` protocol hardened via documented post-hoc audit revisions (`{S['harness_version']}`) across **{b['initial_attempted_count']}** attempted tests (**$N = {b['eligible_count']}$** exact node-attributed eligible tests across **{b['distinct_projects']}** Java and Python repositories; **{b['total_target_m_det']:,}** target and **{b['total_partner_m_det']:,}** partner `3/3` clean-$e_0$ focal obligations, plus **{b['total_m_ext_obligations']:,}** frozen held-out external obligations $M_{{\\text{{ext}}}}$ across **{b['samples_with_m_ext']}** tests), we audit historical SOTA artifacts, five LLMs across four model families, a structural correspondence AST guard (`B3+`), a same-gate scalar retry baseline (`B4`), and our closed-fail mutation-preserving gate **MuGuard-Flake** (`SAFE_PASS` / `EROSION_RETRY` / `ABSTAIN`). Auditing authoritative upstream `:P` patches through the unified `{S['harness_version']}` evaluator shows that **{b1['FlakyDoctor_gpt']['leaked_erosion_among_w_plus_pct']:.2f}%** (`{b1['FlakyDoctor_gpt']['eroded_count']}/{b1['FlakyDoctor_gpt']['w_plus_count']}`) of $W^+$-passing FlakyDoctor GPT-4 patches and **{b1['iPFlakies']['leaked_erosion_among_w_plus_pct']:.2f}%** (`{b1['iPFlakies']['eroded_count']}/{b1['iPFlakies']['w_plus_count']}`) of partner-matched iPFlakies patches lose baseline focal obligations. Across multi-round ($K=5$) repair on `deepseek-flash`, `deepseek-v4-pro`, and confirmatory `qwen3.7-plus`, **MuGuard-Flake** exposes a clear risk--coverage trade-off: it eliminates leaked baseline erosion (`0.00%`), improves operational `SoundFix%` over `B0`, `B3`, and `B3+`, and lowers conditional unseen-$M_{{\\text{{ext}}}}$ obligation loss among accepted patches relative to `B0`/`B3+`, while unconditional end-to-end held-out yield ($Y_s$) over the fixed frozen cohort $E$ is statistically inconclusive (95% clustered CIs span zero and establish neither superiority nor equivalence/non-inferiority). In a prefix-forked mechanism study ($n = {ab.get('n_forks', 0)}$), localized surviving-mutant witness packages (`A3`) substantially raise post-erosion recovery over concise scalar counts (`A1`). Finally, post-hoc diff-grounded PR adjudication quantifies the conservative full dual-role gate's false-rejection rate (**{pr['adjudicated_false_rejection_rate']['MuGuard_Flake']['pct']:.2f}%** on legitimate test-only repairs), and uncached single-test target mutation probing completes in **{lat['uncached_in_loop_stages_sec']['target_mut_java']['median']} s** median on Java and **{lat['uncached_in_loop_stages_sec']['target_mut_python']['median']} s** on Python.",
        "",
        "---",
        "",
        "## 1. Benchmark Attrition & Obligation Profile (Table 1)",
        "",
        f"- **Cohort Accounting:** `{b['initial_attempted_count']}` initial candidate tests $\\to$ **`{b['eligible_count']}`** eligible (`{b['java_eligible']}` Java across `{b['java_projects']}` Maven repos; `{b['python_eligible']}` Python across `{b['python_projects']}` `pytest` repos). `{len(b['attrition_samples'])}` instances fell below `3` strictly node-attributed `3/3` clean-$e_0$ kills and are reported transparently via both primary (`n={b['eligible_count']}`) and Intent-to-Treat (`N={b['initial_attempted_count']}`) metrics.",
        f"- **Partner & External Coverage:** `{b['od_with_partner_obligations']}/{b['od_total']}` OD tests carry `3/3` attributed partner obligations (`{b['total_partner_m_det']:,}` total); `{b['od_zero_partner_obligations']}` zero-obligation partner instances are protected by closed-fail `ABSTAIN_PARTNER_UNCOVERED` when partner/shared scope is edited. Frozen external validation covers `{b['total_m_ext_obligations']:,}` disjoint $M_{{\\text{{ext}}}}$ obligations across `{b['samples_with_m_ext']}` tests.",
        "",
        "---",
        "",
        "## 2. Historical SOTA Artifact Audit (`B1`, Table 2) & PR Reference Adjudication (Table 3)",
        "",
        "| Historical Artifact Cohort | Official Avail. | Target Mismatch | Verbatim Compiled | Pass@$W^+$ | Eroded ($<1$) | Leak Rate ($W^+$) | Operational SoundFix |",
        "| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for kb, lbl in [
        ("FlakyDoctor_gpt", "FlakyDoctor GPT-4 (CSV `:P`)"),
        ("FlakyDoctor_magicoder", "FlakyDoctor Magicoder (CSV `:P`)"),
        ("iPFlakies", "iPFlakies Python (`Patches.csv`)"),
    ]:
        rb = b1[kb]
        mm = rb["stage_outcomes"].get("APPLICATOR_THIRD_CLASS_MISMATCH", 0) + rb["stage_outcomes"].get("APPLICATOR_APPLY_FAIL", 0)
        lines.append(
            f"| {lbl} | {rb['official_available']} | {mm} | {rb['verbatim_compiled']} | {rb['w_plus_count']} ({rb['pass_w_plus_pct']:.2f}%) | **{rb['eroded_count']}** | **{rb['leaked_erosion_among_w_plus_pct']:.2f}%** | {rb['sound_fix_count']} ({rb['sound_fix_pct']:.2f}%) |"
        )

    fr = pr["adjudicated_false_rejection_rate"]
    lines.extend([
        "",
        "### PR-Linked Reference Stratification & False-Rejection Trade-Off",
        f"- **Provenance Breakdown (`{pr['total_pr_linked_instances']}` instances / `{pr['distinct_pr_urls']}` unique PRs):** `{pr['category_counts']}`.",
        f"- **Accepted-PR Rejection Rate (`Accepted`, `src/test` only, $W^+$ pass, $n={pr['accepted_test_only_wplus_count']}$):** `B3 = {pr['accepted_pr_rejection_rate']['B3']}`, `B3+ = {pr['accepted_pr_rejection_rate']['B3_Plus']}`, `MuGuard-Flake = {pr['accepted_pr_rejection_rate']['MuGuard_Flake']}`.",
        f"- **Adjudicated Legitimate-Repair False-Rejection Rate ($n={pr['adjudicated_legitimate_wplus_count']}$):** `B3 = {fr['B3']['rejected']}/{fr['B3']['total']} ({fr['B3']['pct']:.2f}%)`, `B3+ = {fr['B3_Plus']['rejected']}/{fr['B3_Plus']['total']} ({fr['B3_Plus']['pct']:.2f}%)`, `MuGuard-Flake = {fr['MuGuard_Flake']['rejected']}/{fr['MuGuard_Flake']['total']} ({fr['MuGuard_Flake']['pct']:.2f}%)`.",
        "",
        "---",
        "",
        "## 3. Multi-Round ($K=5$) Repair Matrix & Non-Circular External Validation (Tables 4–6)",
        "",
        "| Model ($K=5$) | Strategy | Pass@$W^+$ | Leaked ($W^+$) | Operational SoundFix% [95% Cluster CI] | ITT-208% | Held-Out $M_{\\text{ext}}$ Loss | Strict Yield $Y_s$ | Stress Yield $Y_s^{\\text{stress}}$ |",
        "| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ])
    for m_name in k5_order:
        mo = k5[m_name]
        for idx_s, (sk, slbl) in enumerate([
            ("B0_Standard_Validator", "B0: Unconstrained"),
            ("B2_Prompt_Guard", "B2: Prompt Guard"),
            ("B3_Static_AST_Guard", "B3: Syntactic Guard"),
            ("B3_Plus_Structural_Guard", "B3+: Structural Guard"),
            ("B4_Scalar_Gate_Loop", "B4: Scalar Gate Loop"),
            ("Ours_MuGuard_Flake", "**MuGuard-Flake (Ours)**"),
        ]):
            sv = mo["strategies"][sk]
            he = sv.get("heldout_external") or {}
            ci = sv["sound_fix_cluster_ci95"]
            ml = f"`{m_name}` ($n={mo['n_eligible']}$)" if idx_s == 0 else ""
            ys = f"{he.get('end_to_end_heldout_yield_Ys_count', 0)}/{he.get('cohort_E_size_fixed', 0)} ({fpct(he.get('end_to_end_heldout_yield_Ys_pct'))})" if he else "---"
            yss = f"{he.get('end_to_end_heldout_yield_Ys_stress_count', 0)}/{he.get('cohort_E_size_fixed', 0)} ({fpct(he.get('end_to_end_heldout_yield_Ys_stress_pct'))})" if he else "---"
            hl = f"{he.get('heldout_m_ext_loss_among_accepted', 0)} ({fpct(he.get('conditional_m_ext_loss_pct'))})" if he else "---"
            lines.append(
                f"| {ml} | {slbl} | {sv['w_plus_count']} ({sv['pass_w_plus_pct']:.2f}%) | {sv['leaked_eroded_count']} ({sv['leaked_erosion_among_w_plus_pct']:.2f}%) | {sv['sound_fix_pct']:.2f}% [{ci[0]:.1f}, {ci[1]:.1f}] | {sv['itt_208_sound_fix_pct']:.2f}% | {hl} | {ys} | {yss} |"
            )

    if k1_order:
        lines.extend([
            "",
            "### Cross-Family Single-Pass ($K=1$) Gate Screening (Table 5)",
            "",
            "| Model ($K=1$) | Strategy | Pass@$W^+$ | Leaked ($W^+$) | Operational SoundFix% [95% Cluster CI] |",
            "| :--- | :--- | ---: | ---: | ---: |",
        ])
        for m_name in k1_order:
            mo = k1[m_name]
            for idx_s, (sk, slbl) in enumerate([
                ("B0_Standard_Validator", "B0: Unconstrained"),
                ("B3_Static_AST_Guard", "B3: Syntactic Guard"),
                ("B3_Plus_Structural_Guard", "B3+: Structural Guard"),
                ("Ours_MuGuard_Flake", "**MuGuard-Flake Gate**"),
            ]):
                sv = mo["strategies"][sk]
                ci = sv["sound_fix_cluster_ci95"]
                ml = f"`{m_name}` ($n={mo['n_eligible']}$)" if idx_s == 0 else ""
                lines.append(
                    f"| {ml} | {slbl} | {sv['w_plus_count']} ({sv['pass_w_plus_pct']:.2f}%) | {sv['leaked_eroded_count']} ({sv['leaked_erosion_among_w_plus_pct']:.2f}%) | {sv['sound_fix_pct']:.2f}% [{ci[0]:.1f}, {ci[1]:.1f}] |"
                )

    if ab and ab.get("n_forks", 0) > 0:
        pa1 = ab["primary_mechanism_A3_vs_A1"]
        pa2 = ab["secondary_stress_A3_vs_A2"]
        lines.extend([
            "",
            "---",
            "",
            f"## 4. Prefix-Forked Witness Experiment ($n = {ab['n_forks']}$ forks across {ab['distinct_tests']} tests / {ab['distinct_projects']} repos, Table 8)",
            "",
            f"- **A1 (Concise Scalar Ratio $k/M$):** `{ab['arms']['A1_ScalarRatio']['recovered_count']}/{ab['arms']['A1_ScalarRatio']['total_forks']}` (`{ab['arms']['A1_ScalarRatio']['recovery_pct']:.2f}%`, 95% CI `{ab['arms']['A1_ScalarRatio']['cluster_ci95']}`)",
            f"- **A2 (Matched Misleading Location Stress Test):** `{ab['arms']['A2_MisleadingLocation']['recovered_count']}/{ab['arms']['A2_MisleadingLocation']['total_forks']}` (`{ab['arms']['A2_MisleadingLocation']['recovery_pct']:.2f}%`, 95% CI `{ab['arms']['A2_MisleadingLocation']['cluster_ci95']}`)",
            f"- **A3 (True Surviving Mutant Witness):** `{ab['arms']['A3_TrueWitness']['recovered_count']}/{ab['arms']['A3_TrueWitness']['total_forks']}` (`{ab['arms']['A3_TrueWitness']['recovery_pct']:.2f}%`, 95% CI `{ab['arms']['A3_TrueWitness']['cluster_ci95']}`)",
            f"- **Primary Mechanism Comparison (`A3 - A1`):** `{pa1['diff_test_weighted_pp']:+.2f} pp` (95% cluster CI `{pa1['cluster_ci95_test_weighted']}`, $p_{{\\text{{cluster}}}} = {fp(pa1['p_cluster_signflip'])}$); **Secondary Stress Test (`A3 - A2`):** `{pa2['diff_test_weighted_pp']:+.2f} pp` ($p_{{\\text{{cluster}}}} = {fp(pa2['p_cluster_signflip'])}$).",
        ])

    lines.extend([
        "",
        "---",
        "",
        "## 5. Dual-Axis Patch-Mechanism Codebook, Uncached Latencies, & Abstention Root Causes (Tables 7 & 9)",
        "",
        f"- **Unique Eroded Patches:** `{tax['unique_eroded_patches']}` unique normalized candidate fingerprints (`{tax['total_strategy_round_appearances']}` appearances across `{tax['distinct_tests_with_erosion']}` tests / `{tax['distinct_projects_with_erosion']}` projects); `{tax['unique_patches_bypassing_B3']}` bypass `B3` and `{tax['unique_patches_bypassing_B3_Plus']}` bypass `B3+`.",
        f"- **Uncached In-Loop Target Mutation Latency:** Java median `{lat['uncached_in_loop_stages_sec']['target_mut_java']['median']} s` (P90 `{lat['uncached_in_loop_stages_sec']['target_mut_java']['p90']} s`); Python median `{lat['uncached_in_loop_stages_sec']['target_mut_python']['median']} s` (P90 `{lat['uncached_in_loop_stages_sec']['target_mut_python']['p90']} s`).",
        f"- **Terminal Abstention States by Model:** `{json.dumps(lat['abstentions_breakdown'], ensure_ascii=False)}`.",
        "",
    ])
    return "\n".join(lines)


def build_v2_chinese_report(S: Dict[str, Any]) -> str:
    b = S["benchmark"]
    pr = S["pr_adjudication_and_false_rejection"]
    b1 = S["b1_authoritative_audit"]
    k5 = S["k5_models"]
    k1 = S["k1_models"]
    ab = S["forked_ablation"]
    tax = S["taxonomy_dual_axis"]
    lat = S["latency_and_abstentions"]
    sec = S["security_audit"]

    k5_order = [m for m in ("deepseek-flash", "deepseek-v4-pro", "qwen3.7-plus") if m in k5]
    k1_order = [m for m in ("kimi-k2.5", "glm-5") if m in k1]
    fr = pr["adjudicated_false_rejection_rate"]

    lines: List[str] = [
        f"# FT-1 (`{S['harness_version']}`) 经 Codex 对抗审计重构与全链路核验后的实证与投稿包总报告",
        "",
        "- **论文题目**：*Stabilization via Oracle Erosion: An Empirical Audit and Local Mutation Gate for Flaky Test Repair*",
        "- **作者信息**：李智杰¹ (`lizhijie@mails.neu.edu.cn`)、刘柯彤¹ (`liuketong@mails.neu.edu.cn`)、徐绍洋¹ (`xusy3@mails.neu.edu.cn`)、孟士博¹ (`mengsb1@mails.neu.edu.cn`)、高斐然² (`3120260997@bit.edu.cn`)、赵玉丽¹*（通讯作者，`zhaoyl@swc.neu.edu.cn`）；¹东北大学软件学院，²北京理工大学计算机学院",
        "- **目标期刊**：*Software Quality Journal* (SQJ, Springer Nature)",
        f"- **协议版本与冻结摘要**：`{S['harness_version']}`（执行基准 SHA-256 前缀：`{S['exec_benchmark_sha256'][:16]}`）",
        f"- **队列与留出故障覆盖**：原始尝试队列 **`N_init = {b['initial_attempted_count']}`** $\\to$ 严格节点/阶段归因有效队列 **`N = {b['eligible_count']}`**（跨 **`{b['distinct_projects']}`** 个开源仓库：Java `{b['java_eligible']}` 例 / Python `{b['python_eligible']}` 例；共 `{b['total_target_m_det']:,}` 个目标 `3/3` 义务、`{b['total_partner_m_det']:,}` 个伴随 `3/3` 义务，以及 **`{b['total_m_ext_obligations']:,}`** 个未暴露给门禁与反馈的冻结外部留出故障 $M_{{\\text{{ext}}}}$）",
        f"- **模型与基线覆盖**：`3` 个完整 `K=5` 多轮闭环模型（探索集 `deepseek-flash`、`deepseek-v4-pro` + 预指定确认性模型 `qwen3.7-plus`）$\\times$ `6` 组策略（含新增结构对应守卫 **`B3+`** 与全队列同门禁标量反馈 **`B4_Scalar_Gate_Loop`**）+ `2` 个完整 `K=1` 跨家族模型（`kimi-k2.5`、`glm-5`）+ 权威历史 SOTA 工件（`B1`）",
        f"- **安全与隔离复核**：扫描 `{sec['scanned_ft1_files']}` 个文件，密钥/IP 泄露 `{len(sec['secret_leaks_found'])}` 处，目录越界改动 `{len(sec['outside_ft1_git_changes'])}` 处",
        "",
        "---",
        "",
        "## 一、核心结果总表（含操作性 `SoundFix%` 与非循环外部留出产出率 $Y_s$）",
        "",
        "| 模型 (`K=5`) | 策略 | Pass@$W^+$ | 漏放退化 ($W^+$) | 操作性 SoundFix% [95% 聚类 CI] | ITT-208% | 留出 $M_{\\text{ext}}$ 损失 | 主严控产出率 $Y_s$ | 匹配对照敏感性 $Y_s^{\\text{stress}}$ |",
        "| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for m_name in k5_order:
        mo = k5[m_name]
        for idx_s, (sk, slbl) in enumerate([
            ("B0_Standard_Validator", "`B0` 标准验证"),
            ("B2_Prompt_Guard", "`B2` 契约提示"),
            ("B3_Static_AST_Guard", "`B3` 语法守卫"),
            ("B3_Plus_Structural_Guard", "`B3+` 结构对应守卫"),
            ("B4_Scalar_Gate_Loop", "`B4` 同门禁标量循环"),
            ("Ours_MuGuard_Flake", "**`MuGuard-Flake` (完整见证)**"),
        ]):
            sv = mo["strategies"][sk]
            he = sv.get("heldout_external") or {}
            ci = sv["sound_fix_cluster_ci95"]
            ml = f"`{m_name}` ($n={mo['n_eligible']}$)" if idx_s == 0 else ""
            ys = f"{he.get('end_to_end_heldout_yield_Ys_count', 0)}/{he.get('cohort_E_size_fixed', 0)} ({fpct(he.get('end_to_end_heldout_yield_Ys_pct'))})" if he else "---"
            yss = f"{he.get('end_to_end_heldout_yield_Ys_stress_count', 0)}/{he.get('cohort_E_size_fixed', 0)} ({fpct(he.get('end_to_end_heldout_yield_Ys_stress_pct'))})" if he else "---"
            hl = f"{he.get('heldout_m_ext_loss_among_accepted', 0)} ({fpct(he.get('conditional_m_ext_loss_pct'))})" if he else "---"
            lines.append(
                f"| {ml} | {slbl} | {sv['w_plus_count']} ({sv['pass_w_plus_pct']:.2f}%) | {sv['leaked_eroded_count']} ({sv['leaked_erosion_among_w_plus_pct']:.2f}%) | {sv['sound_fix_pct']:.2f}% [{ci[0]:.1f}, {ci[1]:.1f}] | {sv['itt_208_sound_fix_pct']:.2f}% | {hl} | {ys} | {yss} |"
            )

    lines.extend([
        "",
        "## 二、关键边界、误拒率与消融诚实结论",
        f"1. **历史 SOTA 权威清单审计（`B1`）**：仅统计官方 CSV `:P` 与 `Patches.csv` 正式条目，`FlakyDoctor` GPT-4 通过 $W^+$ 补丁中退化率为 **`{b1['FlakyDoctor_gpt']['eroded_count']}/{b1['FlakyDoctor_gpt']['w_plus_count']} ({b1['FlakyDoctor_gpt']['leaked_erosion_among_w_plus_pct']:.2f}%)`**，`iPFlakies` 为 **`{b1['iPFlakies']['eroded_count']}/{b1['iPFlakies']['w_plus_count']} ({b1['iPFlakies']['leaked_erosion_among_w_plus_pct']:.2f}%)`**。",
        f"2. **PR 来源分层、完整双角色门禁与合理修复误拒率**：`{pr['total_pr_linked_instances']}` 个实例按真实状态分层为 `{pr['category_counts']}`；在事后源码 diff 裁决为合理且通过 $W^+$ 的仅测试修改参考修复（$n={pr['adjudicated_legitimate_wplus_count']}$）上，完整双角色受影响范围门禁下 `B3` 误拒 `{fr['B3']['pct']:.2f}%`，`B3+` 误拒 `{fr['B3_Plus']['pct']:.2f}%`，`MuGuard-Flake` 误拒 **`{fr['MuGuard_Flake']['rejected']}/{fr['MuGuard_Flake']['total']} ({fr['MuGuard_Flake']['pct']:.2f}%)`**（含 `fastjson` 规范化路径与 `visualee` 纯 `@Before clear()` 夹具敏感性，转为人工复核弃权而非静默放行）。",
    ])
    if ab and ab.get("n_forks", 0) > 0:
        pa1 = ab["primary_mechanism_A3_vs_A1"]
        pa2 = ab["secondary_stress_A3_vs_A2"]
        lines.append(
            f"3. **同前缀严格分叉见证实验（$n={ab['n_forks']}$）**：`A1` 标量比例恢复 `{ab['arms']['A1_ScalarRatio']['recovery_pct']:.2f}%`，`A2` 错误位置压力对照恢复 `{ab['arms']['A2_MisleadingLocation']['recovery_pct']:.2f}%`，`A3` 真实存活变异见证恢复 **`{ab['arms']['A3_TrueWitness']['recovery_pct']:.2f}%`**；主机制比较 `A3 - A1 = {pa1['diff_test_weighted_pp']:+.2f} pp`（95% 项目聚类区间 `{pa1['cluster_ci95_test_weighted']}`，$p_{{\\text{{cluster}}}} = {fp(pa1['p_cluster_signflip'])}$），辅助压力比较 `A3 - A2 = {pa2['diff_test_weighted_pp']:+.2f} pp`（$p_{{\\text{{cluster}}}} = {fp(pa2['p_cluster_signflip'])}$）。"
        )
    lines.extend([
        f"4. **未缓存分阶段实测耗时与弃权归因**：Java 未缓存目标变异验证中位 `{lat['uncached_in_loop_stages_sec']['target_mut_java']['median']} s`（P90 `{lat['uncached_in_loop_stages_sec']['target_mut_java']['p90']} s`），Python 中位 `{lat['uncached_in_loop_stages_sec']['target_mut_python']['median']} s`（P90 `{lat['uncached_in_loop_stages_sec']['target_mut_python']['p90']} s`）；全部弃权实例按编译子类、伴随未覆盖、稳定性未收敛与退化未恢复完整披露。",
        "",
    ])
    return "\n".join(lines)


def main() -> None:
    S = json.loads((DATA_DIR / "v2_study_summary_metrics.json").read_text(encoding="utf-8"))
    (PAPER_DIR / "MANUSCRIPT_SQJ.md").write_text(build_v2_manuscript_md(S), encoding="utf-8")
    (FT1_ROOT / "FULL_STUDY_REPORT.md").write_text(build_v2_chinese_report(S), encoding="utf-8")
    print("[OK] Wrote v2 MANUSCRIPT_SQJ.md and FULL_STUDY_REPORT.md")


if __name__ == "__main__":
    main()
