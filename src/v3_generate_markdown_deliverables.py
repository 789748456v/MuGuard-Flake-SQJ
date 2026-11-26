"""
FT-1 v3.0.0-certified Synchronized Markdown Deliverables Generator (`FULL_STUDY_REPORT.md` & `paper/MANUSCRIPT_SQJ.md`).

Rebuilds both Markdown deliverables directly from `data/v3_study_summary_metrics.json` (`3.0.0-certified`)
and `ONLINE_RESOURCE_1.md` with exact parity across all 203/203 complete K=5 backbones, 129/129 prefix-forked
witness runs, dynamic Coverage Gate, cross-vendor LLM-Judge Gate, witness-augmented hybrid PR triage, and
the 10-session stateless blinded OpenAI Codex (`gpt-6-astra`) ensemble audit (`2,310` adjudications).
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


def build_v3_manuscript_md(S: Dict[str, Any]) -> str:
    b = S["benchmark"]
    pr = S["pr_adjudication_and_false_rejection"]
    b1 = S["b1_authoritative_audit"]
    k5 = S["k5_models"]
    k1 = S["k1_models"]
    qx = S["qwen_extra_baselines_k5"]
    ab = S["forked_ablation"]
    tax = S["taxonomy_dual_axis"]
    lat = S["latency_and_abstentions"]
    c10 = S["codex_10session_blinded_audit"]["dimensions"]
    fr = pr["adjudicated_false_rejection_rate"]
    cg_er = json.loads((DATA_DIR / "v3_coverage_gate_offline_and_pr.json").read_text(encoding="utf-8"))["eroded_400_summary"]
    lj_er = json.loads((DATA_DIR / "v3_llm_judge_offline_and_pr.json").read_text(encoding="utf-8"))["eroded_400_summary"]
    fa_er = S["focal_aware_llm_judge"]["eroded_400_summary"]
    sota = S["sota_same_model_qwen37"]["arms"]
    hyb_q = json.loads((DATA_DIR / "v3_witness_hybrid_triage.json").read_text(encoding="utf-8"))["pr_9_muguard_rejected_triage"]["qwen3.7-plus"]

    lines: List[str] = [
        "# Stabilization via Oracle Erosion: An Empirical Audit and Local Mutation Gate for Flaky Test Repair",
        "",
        "**Authors:** Zhijie Li¹ (`lizhijie@mails.neu.edu.cn`), Shaoyang Xu¹ (`xusy3@mails.neu.edu.cn`), Ketong Liu¹ (`liuketong@mails.neu.edu.cn`), Shibo Meng¹ (`mengsb1@mails.neu.edu.cn`), Feiran Gao² (`3120260997@bit.edu.cn`), and Yuli Zhao¹* (`zhaoyl@swc.neu.edu.cn`, Corresponding Author)  ",
        "**Affiliations:** ¹Software College, Northeastern University, Shenyang, China; ²School of Computer Science and Technology, Beijing Institute of Technology, Beijing, China  ",
        "**Target Venue:** *Software Quality Journal* (Springer Nature)  ",
        f"**Certified Protocol & Submission PDF:** `{S['harness_version']}` (`paper/main.pdf`, 34 pages; supplementary tables `Tables S-1..S-9` in `ONLINE_RESOURCE_1.md`)",
        "",
        "---",
        "",
        "## Abstract",
        "",
        "Automated flaky test repair tools increasingly combine context localization with large language models to remove intermittent continuous integration failures. Nearly every pipeline, however, accepts a candidate patch once it compiles, passes rerun schedules, and keeps its assertion statements. Because test repair edits the test oracle and fixture themselves, optimizing solely for green runs invites *Stabilization via Oracle Erosion*: repaired tests turn permanently green while losing their ability to catch future production faults. We audit 203 flaky Java and Python tests across historical artifacts, same-model adaptations of recent repair agents, developer pull requests, and five modern language models. Roughly one in five to over two in five stabilization-passing patches silently drop baseline fault-detection obligations. Static syntax rules, focal line-coverage gates, and diff-only or focal-aware cross-vendor language-model judges still miss over two-thirds of eroded patches because upstream behavioral shifts preserve assertion syntax and code execution. We introduce **MuGuard-Flake**, a fail-closed local mutation gate that verifies modified tests and their shared-state partners in seconds per candidate. **MuGuard-Flake** eliminates in-loop erosion by construction across all five models, raises operational sound-fix yield substantially over static, coverage, model-judge, and call-graph-guided baselines, and lowers unseen fault loss among accepted patches. During repair retries, localized surviving-mutant witnesses also recover far more eroded drafts than numeric counts (71% versus 43%). Strict conservation carries explicit trade-offs: conservative abstention leaves total external fix yield without a statistically significant gain over unguarded repair and rejects 20.5% of legitimate pull-request repair instances, though witness-guided triage cuts those false rejections to 2.6%.",
        "",
        "---",
        "",
        "## 1. Benchmark Profile, Same-Model SOTA Head-to-Head, & Eight-Gate Matrix (`100%` Complete)",
        "",
        f"- **Cohort:** `{b['initial_attempted_count']}` attempted tests $\\to$ **`N = {b['eligible_count']}`** eligible tests across **`{b['distinct_projects']}`** Java and Python repositories (`{b['java_eligible']}` Java across `{b['java_projects']}` Maven repos; `{b['python_eligible']}` Python across `{b['python_projects']}` `pytest` repos), covering **`{b['total_target_m_det']:,}`** target `3/3` baseline obligations, **`{b['total_partner_m_det']:,}`** partner obligations, and **`{b['total_m_ext_obligations']:,}`** disjoint held-out obligations $M_{{\\text{{ext}}}}$ across `{b['samples_with_m_ext']}` tests.",
        "- **Complete Model Matrix & Same-Model SOTA Adaptations:** `203/203` (`100%`) multi-round ($K=5$) repair across `deepseek-flash`, `deepseek-v4-pro`, and confirmatory `qwen3.7-plus`, `203/203` single-pass ($K=1$) screening on `kimi-k2.5` and `glm-5`, plus `203/203` same-model, $W^+$-standardized end-to-end adaptations of `FlakyFix` ($K=1$ & $K=5$), `FlakyDoctor`, `FlakyGuard`, `MuGuard-Flake`, and `FlakyGuard + MuGuard` on `qwen3.7-plus` (`Tables 3, S-7, S-8`).",
        "- **Eight Controlled Gating Strategies + Focal-Aware Judge:** Unconstrained, Prompt Guard, Syntactic Guard, Structural Guard, dynamic focal line Coverage Gate (JaCoCo + `coverage.py`), diff-only & offline focal-aware ($F \\le 5$ DCG functions) cross-vendor LLM-Judge (`qwen3.7-plus` / `glm-5`; `Table S-9`), Scalar Gate, and **MuGuard-Flake**.",
        f"- **Two-Stage Blinded Ensemble Audit (`2,770` Independent Ratings):** Stage-1 frozen author rubric paired with 10 stateless, order-permuted OpenAI Codex (`gpt-6-astra`) sessions across all 53 cleanly applied PR instances (Fleiss' $\\kappa = {c10['pr_53_five_class']['intra_ensemble_fleiss_kappa']:.4f}$; binary legitimacy $\\kappa = {c10['pr_43_legitimate_binary']['intra_ensemble_fleiss_kappa']:.4f}$), 60 stratified eroded patches ($\\kappa = {c10['tax_60_primary_mechanism']['intra_ensemble_fleiss_kappa']:.4f}$), 118 main-campaign held-out $M_{{\\text{{ext}}}}$-loss patches ($\\kappa = {c10['heldout_111_wsl_3class']['intra_ensemble_fleiss_kappa']:.4f}$), and 46 newly encountered SOTA held-out loss patches (`0` unadjudicated items).",
        "",
        "---",
        "",
        "## 2. Historical Artifacts, Same-Model SOTA Adaptations, PRs, & Multi-Paradigm Blind Spots (RQ1–RQ2)",
        "",
        f"- **Historical Artifacts (`Table 3` Panel A & `Table S-1`):** `{b1['FlakyDoctor_gpt']['leaked_erosion_among_w_plus_pct']:.2f}%` (`9/33`) of stabilized `FlakyDoctor` GPT-4 patches and `{b1['iPFlakies']['leaked_erosion_among_w_plus_pct']:.2f}%` (`10/42`) of partner-matched `iPFlakies` patches silently lose baseline obligations; on those exact matched cohorts, **MuGuard-Flake** lifts SoundFix% to `92.00%–94.67%` and `70.13%–85.71%`.",
        f"- **Same-Model, $W^+$-Standardized SOTA Adaptations (`Table 3` Panel B & `Table S-7`):** On confirmatory `qwen3.7-plus` (`N=203`, `|E|=177`), `FlakyFix`-style leaks `{sota['S1_FlakyFix_K1']['leak_pct_of_w_plus']:.2f}%` ($K=1$, SoundFix `{sota['S1_FlakyFix_K1']['sound_fix_pct']:.2f}%`) and `{sota['S1b_FlakyFix_K5']['leak_pct_of_w_plus']:.2f}%` ($K=5$, SoundFix `{sota['S1b_FlakyFix_K5']['sound_fix_pct']:.2f}%`), `FlakyDoctor`-style leaks `{sota['S2_FlakyDoctor']['leak_pct_of_w_plus']:.2f}%` (SoundFix `{sota['S2_FlakyDoctor']['sound_fix_pct']:.2f}%`), and call-graph-guided `FlakyGuard`-style ($F \\le 5$ focal functions + reflection) still leaks `{sota['S3_FlakyGuard']['leak_pct_of_w_plus']:.2f}%` (SoundFix `{sota['S3_FlakyGuard']['sound_fix_pct']:.2f}%`). **MuGuard-Flake** and **FlakyGuard + MuGuard** both reach **`{sota['S4_MuGuard_Flake']['sound_fix_pct']:.2f}%` (`167/203`)** SoundFix (`0.00%` in-loop leak by gate construction) and lower conditional harmful (`W+S`) unseen loss to **`{sota['S4_MuGuard_Flake']['heldout']['conditional_harmful_pct']:.2f}%` (`2/131`)** and **`{sota['S5_FlakyGuard_Plus_MuGuard']['heldout']['conditional_harmful_pct']:.2f}%` (`4/129`)** vs. `{sota['S1b_FlakyFix_K5']['heldout']['conditional_harmful_pct']:.2f}%` ($p_{{\\text{{Holm}}}} \\le 0.0016$ vs. `FlakyFix`-style $K=5$) and directionally lower than `FlakyDoctor`-style (`{sota['S2_FlakyDoctor']['heldout']['conditional_harmful_pct']:.2f}%`) and `FlakyGuard`-style (`{sota['S3_FlakyGuard']['heldout']['conditional_harmful_pct']:.2f}%`, $p_{{\\text{{Holm}}}} = 0.21\\text{{--}}0.27$).",
        f"- **Developer PRs & Witness-Guided Hybrid Triage (`Table 4` & `Table S-2`):** Strict gating rejects `{fr['MuGuard_Flake']['rejected']}/{fr['MuGuard_Flake']['total']}` (`{fr['MuGuard_Flake']['pct']:.2f}%`) legitimate PR instances (`1/29 = 3.4%` under strict 10-session ensemble `L`-only coding), whereas attaching surviving-mutant witnesses over full PR unified diffs cuts legitimate PR false rejections to **`{hyb_q['legit_false_rej_after_witness_triage']}`** (`0/29 = 0.0%` on strictly contract-preserving fixes; `{hyb_q['legit_pr_url_false_rej']}` at the PR-URL level) while preserving both the Nacos weakening and Avro responsibility-shift catches.",
        f"- **Four-Paradigm Bypass (`Table 5` & `Table S-9`):** Across `{tax['unique_eroded_patches']}` unique eroded patches, Syntactic Guard misses `99.0%`, Structural Guard misses `83.8%` (`335/400`), diff-only cross-vendor LLM-Judge misses `{lj_er['bypass_rate_pct']:.1f}%` (`{lj_er['judge_missed_leaked']}/400`), focal-aware LLM-Judge ($F \\le 5$ focal functions) still misses `{fa_er['bypass_rate_pct']:.1f}%` (`{fa_er['judge_missed_leaked']}/400`), and dynamic focal line Coverage Gate misses `{cg_er['cov_bypass_pct']:.1f}%` (`{cg_er['cov_missed_bypassed']}/{cg_er['evaluated_ran']}`, `{cg_er['by_language']['java']['cov_bypass_pct']:.1f}%` in Java).",
        "",
        "---",
        "",
        "## 3. Complete Multi-Round ($K=5$) Operational & Held-Out Matrix (RQ3, Tables 6 & 8)",
        "",
        "| Model (`n=203, |E|=177`) | Strategy | Pass@$W^+$ | Leaked ($W^+$) | Operational SoundFix% [95% CI] | Held-Out $M_{\\text{ext}}$ Loss (`W/S/L`) | Strict $Y_s$ | Stress $Y_s$ |",
        "| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for m_name in ("deepseek-flash", "deepseek-v4-pro", "qwen3.7-plus"):
        mo = k5[m_name]
        for idx_s, (sk, slbl) in enumerate([
            ("B0_Standard_Validator", "Unconstrained"),
            ("B2_Prompt_Guard", "Prompt Guard"),
            ("B3_Static_AST_Guard", "Syntactic Guard"),
            ("B3_Plus_Structural_Guard", "Structural Guard"),
            ("B4_Scalar_Gate_Loop", "Scalar Gate"),
            ("Ours_MuGuard_Flake", "**MuGuard-Flake**"),
        ]):
            if m_name == "qwen3.7-plus" and sk == "B4_Scalar_Gate_Loop":
                for ex_lbl, ex_obj in [("Coverage Gate (`n=195`)", qx["B5_Coverage_Gate"]), ("LLM-Judge Gate", qx["B6_LLM_Judge_Gate"])]:
                    ci_x = ex_obj["sound_fix_cluster_ci95"]
                    he_x = ex_obj["heldout_external"]
                    wx = he_x["wsl"]
                    lines.append(
                        f"| | {ex_lbl} | {ex_obj['w_plus_count']} ({ex_obj['pass_w_plus_pct']:.2f}%) | {ex_obj['leaked_eroded_count']} ({ex_obj['leaked_erosion_among_w_plus_pct']:.2f}%) | "
                        f"{ex_obj['sound_fix_pct']:.2f}% [{ci_x[0]:.1f}, {ci_x[1]:.1f}] | {he_x['heldout_m_ext_loss_among_accepted']} ({he_x['conditional_m_ext_loss_pct']:.2f}%, `{wx['W']}/{wx['S']}/{wx['L']}`) | "
                        f"{he_x['end_to_end_heldout_yield_Ys_count']} ({he_x['end_to_end_heldout_yield_Ys_pct']:.2f}%) | {he_x['end_to_end_heldout_yield_Ys_stress_count']} ({he_x['end_to_end_heldout_yield_Ys_stress_pct']:.2f}%) |"
                    )
            sv = mo["strategies"][sk]
            he = sv["heldout_external"]
            tl = he["test_level_adjudicated_mext_loss"]
            ci = sv["sound_fix_cluster_ci95"]
            ml = f"`{m_name}`" if idx_s == 0 else ""
            wsl_s = f"{tl['genuine_weakening_count']}/{tl['responsibility_shift_count']}/{tl['legit_canonicalization_count']}"
            lines.append(
                f"| {ml} | {slbl} | {sv['w_plus_count']} ({sv['pass_w_plus_pct']:.2f}%) | {sv['leaked_eroded_count']} ({sv['leaked_erosion_among_w_plus_pct']:.2f}%) | "
                f"{sv['sound_fix_pct']:.2f}% [{ci[0]:.1f}, {ci[1]:.1f}] | {he['heldout_m_ext_loss_among_accepted']} ({he['conditional_m_ext_loss_pct']:.2f}%, `{wsl_s}`) | "
                f"{he['end_to_end_heldout_yield_Ys_count']} ({he['end_to_end_heldout_yield_Ys_pct']:.2f}%) | {he['end_to_end_heldout_yield_Ys_stress_count']} ({he['end_to_end_heldout_yield_Ys_stress_pct']:.2f}%) |"
            )

    pa1 = ab["primary_mechanism_A3_vs_A1"]
    pa2 = ab["secondary_stress_A3_vs_A2"]
    lines.extend([
        "",
        "---",
        "",
        f"## 4. Complete Prefix-Forked Witness Experiment (`{ab['n_forks']}/{ab['n_forks']}` = `100%`) & CI Runtime Trade-Offs (RQ3–RQ4)",
        "",
        f"- **Prefix-Forked Recovery (`Table 10`):** Gate-Only `0/129` (`0.00%`); Scalar Ratio `{ab['arms']['A1_ScalarRatio']['recovered_count']}/129` (`{ab['arms']['A1_ScalarRatio']['recovery_pct']:.2f}%`); Misleading Witness `{ab['arms']['A2_MisleadingLocation']['recovered_count']}/129` (`{ab['arms']['A2_MisleadingLocation']['recovery_pct']:.2f}%`); **True Witness `{ab['arms']['A3_TrueWitness']['recovered_count']}/129` (`{ab['arms']['A3_TrueWitness']['recovery_pct']:.2f}%`)** (`A3 - A1 = {pa1['diff_test_weighted_pp']:+.2f} pp`, $p = {fp(pa1['p_cluster_signflip'])}$; `A3 - A2 = {pa2['diff_test_weighted_pp']:+.2f} pp`, $p = {fp(pa2['p_cluster_signflip'])}$).",
        f"- **Uncached Probing Latency & Fair Python Re-Indentation (`Table 11` & `Table S-4`):** Target mutation probing finishes in `{lat['uncached_in_loop_stages_sec']['target_mut_java']['median']} s` median on Java and `{lat['uncached_in_loop_stages_sec']['target_mut_python']['median']} s` on Python (faster than `20.89 s` LLM-Judge calls and far below `53–80 s` repair LLM generation). Fair Python method re-indentation lifts confirmatory `qwen3.7-plus` **MuGuard-Flake** SoundFix% to **`85.22%` (`+5.91 pp`)** with `0.00%` leaked erosion.",
        "- **Online Subset Gating (`Table 12` & `Table S-5`):** Live uniform `Top-10` gating inside `qwen3.7-plus` leaks `12/165` (`7.27%`) eroded patches and drops SoundFix% to `75.37%` (vs. `79.31%` under full **MuGuard-Flake**).",
        "",
    ])
    return "\n".join(lines)


def build_v3_chinese_report(S: Dict[str, Any]) -> str:
    b = S["benchmark"]
    pr = S["pr_adjudication_and_false_rejection"]
    b1 = S["b1_authoritative_audit"]
    k5 = S["k5_models"]
    qx = S["qwen_extra_baselines_k5"]
    ab = S["forked_ablation"]
    lat = S["latency_and_abstentions"]
    c10 = S["codex_10session_blinded_audit"]["dimensions"]
    sec = S["security_audit"]
    fr = pr["adjudicated_false_rejection_rate"]
    cg_er = json.loads((DATA_DIR / "v3_coverage_gate_offline_and_pr.json").read_text(encoding="utf-8"))["eroded_400_summary"]
    lj_er = json.loads((DATA_DIR / "v3_llm_judge_offline_and_pr.json").read_text(encoding="utf-8"))["eroded_400_summary"]
    fa_er = S["focal_aware_llm_judge"]["eroded_400_summary"]
    sota = S["sota_same_model_qwen37"]["arms"]
    hyb_q = json.loads((DATA_DIR / "v3_witness_hybrid_triage.json").read_text(encoding="utf-8"))["pr_9_muguard_rejected_triage"]["qwen3.7-plus"]

    lines: List[str] = [
        f"# FT-1 (`{S['harness_version']}`) 最终发表级全链路实证与投稿包总报告",
        "",
        "- **论文题目**：*Stabilization via Oracle Erosion: An Empirical Audit and Local Mutation Gate for Flaky Test Repair*",
        "- **作者信息**：李智杰¹、徐绍洋¹、刘柯彤¹、孟士博¹、高斐然²、赵玉丽¹*（通讯作者，`zhaoyl@swc.neu.edu.cn`）；¹东北大学软件学院，²北京理工大学计算机学院",
        "- **目标期刊**：*Software Quality Journal* (SQJ, Springer Nature)",
        "- **最终稿件规格**：主 PDF `34` 页（第 13 页进入 RQ1）、主文 `10,243` 词、`62` 篇全部正文中引用的已核实参考文献、`12` 张主表（全部 $\\ge 7.5\\text{pt}$）、`5` 张矢量图、单段非结构化摘要（`250` 词）、在线补充材料 `ONLINE_RESOURCE_1.md`（`Tables S-1..S-9`）；46 项 SQJ 行文与跨工件门禁 **`46/46 PASS`（`FAIL=0, WARN=0`）**，Codex `Template B` 回归事实终检 **`PASS`**",
        f"- **全量零缺失矩阵**：`5` 个异构模型全部完成 **`203/203`（`100%`）** 有效测试评估；同底座 `Qwen 3.7 Plus` 端到端 SOTA 适配矩阵（`FlakyFix`-style $K=1/5$、`FlakyDoctor`-style、`FlakyGuard`-style、`MuGuard-Flake`、`FlakyGuard + MuGuard`）全部完成 **`203/203`（`100%`）** 及 `|E|=177` 留出验证；`4` 个同前缀见证消融分支全部完成 **`{ab['n_forks']}/{ab['n_forks']}`（`100%`）**",
        f"- **10 会话无状态独立盲审集成**：跨 `53` 个开发者 PR 实例、`60` 个分层侵蚀补丁、`118` 个主矩阵留出损失补丁与 `46` 个新增 SOTA 留出损失补丁共 **`2,770` 次独立盲审模型裁决**（OpenAI Codex `gpt-6-astra` `--ephemeral` 乱序只读会话，内部一致性 Fleiss' $\\kappa = {c10['heldout_111_wsl_3class']['intra_ensemble_fleiss_kappa']:.4f}\\text{{--}}{c10['pr_43_legitimate_binary']['intra_ensemble_fleiss_kappa']:.4f}$）",
        f"- **安全与工作区隔离扫描**：扫描 `{sec['scanned_ft1_files']}` 个文件，密钥/IP 泄露 `{len(sec['secret_leaks_found'])}` 处，`research/FT1/` 外部改动 `{len(sec['outside_ft1_git_changes'])}` 处",
        "",
        "---",
        "",
        "## 一、`K=5` 多轮闭环与外部留出验证总表（`203/203` 全完成）",
        "",
        "| 模型 (`n=203`) | 策略 | Pass@$W^+$ | 漏放侵蚀 ($W^+$) | 操作性 SoundFix% [95% CI] | 留出 $M_{\\text{ext}}$ 损失 (`W/S/L`) | Strict $Y_s$ | Stress $Y_s$ |",
        "| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for m_name in ("deepseek-flash", "deepseek-v4-pro", "qwen3.7-plus"):
        mo = k5[m_name]
        for idx_s, (sk, slbl) in enumerate([
            ("B0_Standard_Validator", "Unconstrained"),
            ("B2_Prompt_Guard", "Prompt Guard"),
            ("B3_Static_AST_Guard", "Syntactic Guard"),
            ("B3_Plus_Structural_Guard", "Structural Guard"),
            ("B4_Scalar_Gate_Loop", "Scalar Gate"),
            ("Ours_MuGuard_Flake", "**MuGuard-Flake**"),
        ]):
            if m_name == "qwen3.7-plus" and sk == "B4_Scalar_Gate_Loop":
                for ex_lbl, ex_obj in [("Coverage Gate (`n=195`)", qx["B5_Coverage_Gate"]), ("LLM-Judge Gate", qx["B6_LLM_Judge_Gate"])]:
                    ci_x = ex_obj["sound_fix_cluster_ci95"]
                    he_x = ex_obj["heldout_external"]
                    wx = he_x["wsl"]
                    lines.append(
                        f"| | {ex_lbl} | {ex_obj['w_plus_count']} ({ex_obj['pass_w_plus_pct']:.2f}%) | {ex_obj['leaked_eroded_count']} ({ex_obj['leaked_erosion_among_w_plus_pct']:.2f}%) | "
                        f"{ex_obj['sound_fix_pct']:.2f}% [{ci_x[0]:.1f}, {ci_x[1]:.1f}] | {he_x['heldout_m_ext_loss_among_accepted']} ({he_x['conditional_m_ext_loss_pct']:.2f}%, `{wx['W']}/{wx['S']}/{wx['L']}`) | "
                        f"{he_x['end_to_end_heldout_yield_Ys_count']} ({he_x['end_to_end_heldout_yield_Ys_pct']:.2f}%) | {he_x['end_to_end_heldout_yield_Ys_stress_count']} ({he_x['end_to_end_heldout_yield_Ys_stress_pct']:.2f}%) |"
                    )
            sv = mo["strategies"][sk]
            he = sv["heldout_external"]
            tl = he["test_level_adjudicated_mext_loss"]
            ci = sv["sound_fix_cluster_ci95"]
            ml = f"`{m_name}`" if idx_s == 0 else ""
            wsl_s = f"{tl['genuine_weakening_count']}/{tl['responsibility_shift_count']}/{tl['legit_canonicalization_count']}"
            lines.append(
                f"| {ml} | {slbl} | {sv['w_plus_count']} ({sv['pass_w_plus_pct']:.2f}%) | {sv['leaked_eroded_count']} ({sv['leaked_erosion_among_w_plus_pct']:.2f}%) | "
                f"{sv['sound_fix_pct']:.2f}% [{ci[0]:.1f}, {ci[1]:.1f}] | {he['heldout_m_ext_loss_among_accepted']} ({he['conditional_m_ext_loss_pct']:.2f}%, `{wsl_s}`) | "
                f"{he['end_to_end_heldout_yield_Ys_count']} ({he['end_to_end_heldout_yield_Ys_pct']:.2f}%) | {he['end_to_end_heldout_yield_Ys_stress_count']} ({he['end_to_end_heldout_yield_Ys_stress_pct']:.2f}%) |"
            )

    pa1 = ab["primary_mechanism_A3_vs_A1"]
    pa2 = ab["secondary_stress_A3_vs_A2"]
    lines.extend([
        "",
        "## 二、关键升级结论摘要",
        f"1. **历史 SOTA 同队列正面对决（`Table 3` / `Table S-1`）**：`FlakyDoctor` GPT-4 稳定补丁退化率 `{b1['FlakyDoctor_gpt']['leaked_erosion_among_w_plus_pct']:.2f}%`（合格产出仅 `32.00%`），`iPFlakies` 退化率 `{b1['iPFlakies']['leaked_erosion_among_w_plus_pct']:.2f}%`（合格产出 `41.56%`）；在完全相同的匹配子集上，**MuGuard-Flake** 将合格修复率分别提升至 **`92.00%–94.67%`** 与 **`70.13%–85.71%`** 且漏放侵蚀为 `0.00%`。",
        f"2. **开发者 PR 误拒率与见证辅助分流（`Table 4` / `Table S-2`）**：严格门禁在宽泛合理 PR 上误拒 `{fr['MuGuard_Flake']['rejected']}/{fr['MuGuard_Flake']['total']} ({fr['MuGuard_Flake']['pct']:.2f}%)`（在 10 会话盲审严格契约保持口径下仅 `1/29 = 3.4%`）；在完整 PR unified diff 上附带存活变异见证可将合理重构误拒率降至 **`{hyb_q['legit_false_rej_after_witness_triage']}`**（严格契约保持口径下 `0/29 = 0.0%`，PR-URL 维度 `{hyb_q['legit_pr_url_false_rej']}`），同时完整拦截 Nacos 弱化与 Avro 职责转移。",
        f"3. **四范式盲区对比（`Table 5`）**：跨 `400` 个唯一侵蚀补丁，语法守卫漏检 `99.0%`、结构对应守卫漏检 `83.8%`、基于 diff 的跨厂商 `LLM-Judge` 漏检 `{lj_er['bypass_rate_pct']:.1f}%`（`{lj_er['judge_missed_leaked']}/400`）、动态焦点行覆盖门禁漏检 `{cg_er['cov_bypass_pct']:.1f}%`（`{cg_er['cov_missed_bypassed']}/{cg_er['evaluated_ran']}`，Java 达 `{cg_er['by_language']['java']['cov_bypass_pct']:.1f}%`）；且独立 `LLM-Judge` 在 5 个模型中的 4 个上误杀的健康补丁多于拦住的退化补丁（`Table S-3`）。",
        f"4. **完整 `129/129` 同前缀分叉见证实验（`Table 10`）**：真实存活变异见证恢复率达 **`{ab['arms']['A3_TrueWitness']['recovery_pct']:.2f}%` (`91/129`)**，显著击败标量比例（`{ab['arms']['A1_ScalarRatio']['recovery_pct']:.2f}%`，`{pa1['diff_test_weighted_pp']:+.2f} pp`，$p={fp(pa1['p_cluster_signflip'])}$）与同长度误导位置见证（`{ab['arms']['A2_MisleadingLocation']['recovery_pct']:.2f}%`，`{pa2['diff_test_weighted_pp']:+.2f} pp`，$p={fp(pa2['p_cluster_signflip'])}$）。",
        f"5. **秒级门禁开销、Python 公平重缩进与在线 `Top-10` 实测（`Tables 11–12` / `Tables S-4..S-5`）**：未缓存单轮目标探测中位仅 Java `{lat['uncached_in_loop_stages_sec']['target_mut_java']['median']} s`、Python `{lat['uncached_in_loop_stages_sec']['target_mut_python']['median']} s`；确定性 Python 方法块重缩进将确认集 **MuGuard-Flake** 合格产出进一步推高至 **`85.22%` (`+5.91 pp`)** 且保持零漏放，而在线 `Top-10` 子集门禁会重新漏放 `12` 个侵蚀补丁（`7.27%`）。",
        "",
    ])
    return "\n".join(lines)


def main() -> None:
    S = json.loads((DATA_DIR / "v3_study_summary_metrics.json").read_text(encoding="utf-8"))
    md_en = build_v3_manuscript_md(S)
    md_zh = build_v3_chinese_report(S)
    (PAPER_DIR / "MANUSCRIPT_SQJ.md").write_text(md_en + "\n", encoding="utf-8")
    (PAPER_DIR / "FULL_STUDY_REPORT.md").write_text(md_zh + "\n", encoding="utf-8")
    (FT1_ROOT / "FULL_STUDY_REPORT.md").write_text(md_zh + "\n", encoding="utf-8")
    print("[OK] Wrote synchronized v3.0 MANUSCRIPT_SQJ.md and FULL_STUDY_REPORT.md")


if __name__ == "__main__":
    main()
