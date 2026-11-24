"""
FT-1 v2.0.0-frozen Comprehensive Statistical Inference, PR Semantic Adjudication,
Dual-Axis Patch-Mechanism Codebook, Cost/Abstention Breakdown, and Figure Generator.
"""
import hashlib
import json
import math
import random
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy import stats as sp_stats

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analyze_and_security_audit import run_security_and_isolation_audit
from ast_guard_and_prompts import check_static_ast_guard
from python_ast_guard_and_prompts import check_py_static_ast_guard
from remote_and_llm import DATA_DIR, FT1_ROOT
from run_full_study_matrix import candidate_fingerprint, safe_model_tag
from run_v2_confirmatory_campaign import V2_STRATEGIES_K1, V2_STRATEGIES_K5
from v2_guards_and_attribution import (
    HARNESS_VERSION,
    check_java_b3_plus,
    check_py_b3_plus,
    compute_v2_topk_sensitivity,
)

FIG_DIR = FT1_ROOT / "paper" / "figures"


# ============================================================================
# 1. Project-Clustered Paired Statistical Estimators
# ============================================================================

def paired_project_cluster_inference(
    records: List[Dict[str, Any]],
    key_a: str,
    key_b: str,
    n_boot: int = 20000,
    n_perm: int = 20000,
    seed: int = 20261117,
) -> Dict[str, Any]:
    """
    Compute paired risk difference `A - B` (in percentage points) preserving whole-project clustering:
      - Primary: test-weighted paired risk difference + 20,000 whole-project cluster bootstrap 95% CI.
      - Sensitivity: equal-project-weighted mean of project differences + 20,000 bootstrap 95% CI.
      - Hypothesis test: project-level Rademacher sign-flip permutation test under H0 of symmetric
        project-level paired differences across independent repositories, plus exact McNemar reference.
    Each item in `records` has `{"project": str, key_a: 0|1, key_b: 0|1}`.
    """
    if not records:
        return {}
    rng = np.random.default_rng(seed)

    by_proj: Dict[str, List[Tuple[int, int]]] = defaultdict(list)
    for r in records:
        by_proj[r["project"]].append((int(bool(r[key_a])), int(bool(r[key_b]))))

    proj_names = sorted(by_proj.keys())
    P = len(proj_names)
    N = len(records)

    # Precompute per-project counts `(n_p, sum_a_p, sum_b_p, diff_sum_p, mean_diff_p)`
    n_p = np.array([len(by_proj[p]) for p in proj_names], dtype=np.float64)
    sa_p = np.array([sum(x[0] for x in by_proj[p]) for p in proj_names], dtype=np.float64)
    sb_p = np.array([sum(x[1] for x in by_proj[p]) for p in proj_names], dtype=np.float64)
    dsum_p = sa_p - sb_p
    dmean_p = dsum_p / n_p

    obs_rate_a = float(np.sum(sa_p) / N) * 100.0
    obs_rate_b = float(np.sum(sb_p) / N) * 100.0
    obs_diff_test_wt = obs_rate_a - obs_rate_b
    obs_diff_proj_wt = float(np.mean(dmean_p)) * 100.0

    # 20,000 whole-project cluster bootstrap resamples
    idx_mat = rng.integers(0, P, size=(n_boot, P))
    boot_n = np.sum(n_p[idx_mat], axis=1)
    boot_dsum = np.sum(dsum_p[idx_mat], axis=1)
    boot_test_wt = (boot_dsum / np.maximum(1.0, boot_n)) * 100.0
    boot_proj_wt = np.mean(dmean_p[idx_mat], axis=1) * 100.0

    ci_test_lo, ci_test_hi = np.percentile(boot_test_wt, [2.5, 97.5])
    ci_proj_lo, ci_proj_hi = np.percentile(boot_proj_wt, [2.5, 97.5])

    # Project-level Rademacher sign-flip permutation test on `dsum_p`
    nz_diffs = dsum_p[dsum_p != 0]
    P_nz = len(nz_diffs)
    obs_abs_sum = abs(float(np.sum(dsum_p)))
    if P_nz == 0:
        p_cluster_perm = 1.0
    elif P_nz <= 16:
        # Exact enumeration of all 2^P_nz sign configurations!
        signs = np.array(np.meshgrid(*[[-1.0, 1.0]] * P_nz)).T.reshape(-1, P_nz)
        perm_sums = np.abs(signs @ nz_diffs)
        p_cluster_perm = float(np.mean(perm_sums >= obs_abs_sum - 1e-12))
    else:
        signs = rng.choice([-1.0, 1.0], size=(n_perm, P_nz))
        perm_sums = np.abs(signs @ nz_diffs)
        p_cluster_perm = float((1 + np.sum(perm_sums >= obs_abs_sum - 1e-12)) / (n_perm + 1))

    # Paired discordant counts & exact binomial McNemar (unclustered reference)
    b_only = sum(1 for r in records if not r[key_a] and r[key_b])
    a_only = sum(1 for r in records if r[key_a] and not r[key_b])
    n_disc = a_only + b_only
    if n_disc == 0:
        p_mcnemar = 1.0
        paired_rb = 0.0
    else:
        p_mcnemar = float(sp_stats.binomtest(a_only, n_disc, 0.5, alternative="two-sided").pvalue)
        paired_rb = (a_only - b_only) / n_disc  # Paired rank-biserial on discordant pairs `[-1, +1]`

    return {
        "n_tests": N,
        "n_projects": P,
        "n_nonzero_diff_projects": int(P_nz),
        "rate_a_pct": round(obs_rate_a, 2),
        "rate_b_pct": round(obs_rate_b, 2),
        "diff_test_weighted_pp": round(obs_diff_test_wt, 2),
        "cluster_ci95_test_weighted": [round(float(ci_test_lo), 2), round(float(ci_test_hi), 2)],
        "diff_equal_project_pp": round(obs_diff_proj_wt, 2),
        "cluster_ci95_equal_project": [round(float(ci_proj_lo), 2), round(float(ci_proj_hi), 2)],
        "p_cluster_signflip": round(p_cluster_perm, 6),
        "discordant_b_only_a_only": [b_only, a_only],
        "paired_rank_biserial": round(paired_rb, 4),
        "p_unclustered_mcnemar_ref": round(p_mcnemar, 6),
    }


def holm_bonferroni(p_dict: Dict[str, float]) -> Dict[str, float]:
    items = sorted(p_dict.items(), key=lambda kv: kv[1])
    m = len(items)
    adj: Dict[str, float] = {}
    running_max = 0.0
    for idx, (k, p) in enumerate(items):
        val = min(1.0, p * (m - idx))
        running_max = max(running_max, val)
        adj[k] = round(running_max, 6)
    return adj


def cluster_rate_ci(records: List[Tuple[str, int]], n_boot: int = 20000, seed: int = 42) -> Tuple[float, List[float]]:
    if not records:
        return 0.0, [0.0, 0.0]
    by_p: Dict[str, List[int]] = defaultdict(list)
    for p, v in records:
        by_p[p].append(int(bool(v)))
    pnames = sorted(by_p.keys())
    P = len(pnames)
    N = len(records)
    np_arr = np.array([len(by_p[p]) for p in pnames], dtype=np.float64)
    sp_arr = np.array([sum(by_p[p]) for p in pnames], dtype=np.float64)
    obs = float(np.sum(sp_arr) / N) * 100.0
    if P <= 1:
        return round(obs, 2), [round(obs, 2), round(obs, 2)]
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, P, size=(n_boot, P))
    boots = (np.sum(sp_arr[idx], axis=1) / np.maximum(1.0, np.sum(np_arr[idx], axis=1))) * 100.0
    lo, hi = np.percentile(boots, [2.5, 97.5])
    return round(obs, 2), [round(float(lo), 2), round(float(hi), 2)]


# ============================================================================
# 2. Independent PR Semantic Adjudication & B3/B3+/MuGuard False-Rejection Audit
# ============================================================================

def build_pr_adjudication_and_false_rejection(samples: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Construct `data/pr_semantic_adjudication.json` strictly reading independent, diff-grounded
    adjudications from `data/pr_independent_adjudication_input.json` (ZERO circular dependence
    on mutation retention!).
      - Requires strict `CLEAN_FULL_TEST_APPLY` (partial multi-file applies excluded & logged).
      - Evaluates `B3` / `B3+` strictly on `applied_victim_code` (0 fallbacks to `orig_test_code`!).
      - Computes primary `Adjudicated Legitimate-Fix False-Rejection Rate` excluding
        `TEST_RESPONSIBILITY_SHIFT` + explicit sensitivity including `TEST_RESPONSIBILITY_SHIFT`.
    """
    pr_samples = [s for s in samples if s.get("v2_pr_manifest")]
    distinct_urls = sorted({s["v2_pr_manifest"]["pr_url"] for s in pr_samples})

    indep_path = DATA_DIR / "pr_independent_adjudication_input.json"
    if not indep_path.exists():
        raise FileNotFoundError(f"Missing required external independent PR adjudication file: {indep_path}")
    indep_entries = json.loads(indep_path.read_text(encoding="utf-8")).get("entries", {})

    buckets = Counter()
    apply_outcomes = Counter()
    adjudications: List[Dict[str, Any]] = []
    gate_eval_rows: List[Dict[str, Any]] = []
    b3_fallback_count = 0

    for s in pr_samples:
        pm = s["v2_pr_manifest"]
        ev = s.get("v2_dev_pr_eval")
        st = pm["idoft_status"]
        sc_type = pm["pr_scope_type"]
        ao = pm.get("pr_apply_outcome") or ("CLEAN_FULL_TEST_APPLY" if pm.get("all_test_files_applied", ev is not None) else "ZERO_FILES_APPLIED")
        apply_outcomes[ao] += 1

        if st == "Accepted" and sc_type == "TEST_ONLY_PR":
            b_cat = "PR_Accepted_TestOnly"
        elif st == "Accepted":
            b_cat = "PR_Accepted_WithProd"
        elif st == "Opened":
            b_cat = "PR_Opened"
        else:
            b_cat = f"PR_{st}"
        buckets[b_cat] += 1

        if not ev or ao != "CLEAN_FULL_TEST_APPLY":
            continue

        ret3 = ev["retention_m_det_3x"]
        sid = s["sample_id"]

        # Evaluate B3/B3+ strictly on actual applied victim method code (`applied_victim_code`)!
        applied_v = (ev.get("applied_victim_code") or pm.get("applied_victim_code") or "").strip()
        if not applied_v and ev["w_plus_passed"]:
            # True unexpected fallback on a W+-passing PR (must be 0 in v2.1.0-audited!)
            b3_fallback_count += 1
            applied_v = ""
        orig_m = {"victim": s["orig_test_code"]}
        cand_m = {"victim": applied_v}
        if s["test_type"] == "OD" and s.get("orig_polluter_code") and (ev.get("applied_polluter_code") or pm.get("applied_polluter_code")):
            orig_m["polluter"] = s["orig_polluter_code"]
            cand_m["polluter"] = ev.get("applied_polluter_code") or pm.get("applied_polluter_code")

        b3_ok, _ = check_static_ast_guard(orig_m, cand_m)
        b3p_ok, b3p_rs = check_java_b3_plus(orig_m, cand_m)
        t_unk_3x = int(ev.get("target_unknown_count", ev.get("m_det_unknown_across_3x", 0)))
        muguard_target_only_ok = bool(ev["w_plus_passed"] and ret3 == 1.0 and t_unk_3x == 0)
        fg = ev.get("pr_full_gate")
        muguard_ok = bool(fg["pr_full_gate_accepts"]) if isinstance(fg, dict) else muguard_target_only_ok

        # Read external diff-grounded adjudication strictly (NO retention-based auto-labeling!)
        if sid not in indep_entries:
            raise KeyError(f"Evaluated PR `{sid}` missing from independent adjudication input `{indep_path}`")
        adj = dict(indep_entries[sid])
        adj["observed_retention_3x"] = ret3
        adj["observed_lost_mutants_count"] = len(ev.get("lost_mutants") or [])

        row = {
            "sample_id": sid,
            "pr_url": pm["pr_url"],
            "category": b_cat,
            "idoft_status": st,
            "pr_scope_type": sc_type,
            "pr_apply_outcome": ao,
            "applied_tree_sha256": ev.get("applied_tree_sha256") or pm.get("applied_tree_sha256", ""),
            "w_plus_passed": ev["w_plus_passed"],
            "w_plus_heldout_passed": ev["w_plus_heldout_passed"],
            "retention_3x": ret3,
            "retention_union": ev["retention_m_det_union"],
            "m_det_unknown_across_3x": ev.get("m_det_unknown_across_3x", 0),
            "lost_mutants": ev["lost_mutants"],
            "b3_accepts": b3_ok,
            "b3_plus_accepts": b3p_ok,
            "b3_plus_reasons": [r["code"] for r in b3p_rs],
            "muguard_target_only_accepts": muguard_target_only_ok,
            "muguard_accepts": muguard_ok,
            "pr_full_gate": fg,
            "adjudication": adj,
        }
        adjudications.append(row)
        gate_eval_rows.append(row)

    # Metric A: Accepted-PR Gate Rejection Rate (strictly on `PR_Accepted_TestOnly` passing W+)
    acc_to = [r for r in gate_eval_rows if r["category"] == "PR_Accepted_TestOnly" and r["w_plus_passed"]]
    # Metric B (Primary): Independently adjudicated legitimate repairs (`is_legitimate_repair == True`, EXCLUDING `TEST_RESPONSIBILITY_SHIFT`)
    legit_repairs = [
        r for r in gate_eval_rows
        if r["pr_scope_type"] == "TEST_ONLY_PR" and r["w_plus_passed"] and r["adjudication"]["is_legitimate_repair"]
    ]
    # Metric B (Sensitivity): Including `TEST_RESPONSIBILITY_SHIFT` in denominator
    legit_plus_shift = [
        r for r in gate_eval_rows
        if r["pr_scope_type"] == "TEST_ONLY_PR" and r["w_plus_passed"] and (
            r["adjudication"]["is_legitimate_repair"] or r["adjudication"]["label"] == "TEST_RESPONSIBILITY_SHIFT"
        )
    ]
    # Genuine oracle weakenings among W+-passing TEST_ONLY_PRs (for recall/true-positive check!)
    weak_prs = [
        r for r in gate_eval_rows
        if r["pr_scope_type"] == "TEST_ONLY_PR" and r["w_plus_passed"] and r["adjudication"]["label"] == "GENUINE_ORACLE_WEAKENING"
    ]

    def _rej_block(cohort: List[Dict[str, Any]]) -> Dict[str, Any]:
        return {
            "B3": {
                "rejected": sum(1 for r in cohort if not r["b3_accepts"]),
                "total": len(cohort),
                "pct": round(100.0 * sum(1 for r in cohort if not r["b3_accepts"]) / max(1, len(cohort)), 2),
            },
            "B3_Plus": {
                "rejected": sum(1 for r in cohort if not r["b3_plus_accepts"]),
                "total": len(cohort),
                "pct": round(100.0 * sum(1 for r in cohort if not r["b3_plus_accepts"]) / max(1, len(cohort)), 2),
            },
            "MuGuard_Flake": {
                "rejected": sum(1 for r in cohort if not r["muguard_accepts"]),
                "total": len(cohort),
                "pct": round(100.0 * sum(1 for r in cohort if not r["muguard_accepts"]) / max(1, len(cohort)), 2),
            },
        }

    label_dist_eval = Counter(r["adjudication"]["label"] for r in gate_eval_rows if r["pr_scope_type"] == "TEST_ONLY_PR" and r["w_plus_passed"])

    summary = {
        "independent_input_file": "data/pr_independent_adjudication_input.json",
        "total_pr_linked_instances": len(pr_samples),
        "distinct_pr_urls": len(distinct_urls),
        "category_counts": dict(buckets),
        "apply_outcome_counts": dict(apply_outcomes),
        "evaluated_count": len(gate_eval_rows),
        "b3_code_fallback_count": b3_fallback_count,
        "test_only_wplus_label_distribution": dict(label_dist_eval),
        "accepted_test_only_wplus_count": len(acc_to),
        "accepted_pr_rejection_rate": {
            k: f"{v['rejected']}/{v['total']} ({v['pct']:.2f}%)"
            for k, v in _rej_block(acc_to).items()
        },
        "adjudicated_legitimate_wplus_count": len(legit_repairs),
        "adjudicated_false_rejection_rate": _rej_block(legit_repairs),
        "sensitivity_including_responsibility_shift_count": len(legit_plus_shift),
        "sensitivity_false_rejection_including_shift": _rej_block(legit_plus_shift),
        "genuine_weakening_prs_in_test_only_wplus": {
            "count": len(weak_prs),
            "sample_ids": [r["sample_id"] for r in weak_prs],
            "caught_by_gate": _rej_block(weak_prs),
        },
        "divergent_cases_detail": [r for r in adjudications if r["retention_3x"] < 1.0 or r["adjudication"]["label"] == "GENUINE_ORACLE_WEAKENING"],
    }

    (DATA_DIR / "pr_semantic_adjudication.json").write_text(
        json.dumps({"summary": summary, "all_evaluated_entries": adjudications}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return summary


# ============================================================================
# 3. Dual-Axis Patch-Mechanism x Fault-Consequence Codebook (`v2.1` Non-Fallback)
# ============================================================================

C1_BRANCH_MUTATORS = {
    "NegateConditionalsMutator",
    "ConditionalsBoundaryMutator",
    "NegateIfConditionMutator",
    "RemoveConditionalMutator_EQUAL_IF",
    "RemoveConditionalMutator_EQUAL_ELSE",
    "RemoveConditionalMutator_ORDER_IF",
    "RemoveConditionalMutator_ORDER_ELSE",
    "BooleanComparisonMutator",
}
C3_CALL_MUTATORS = {
    "VoidMethodCallMutator",
    "NonVoidMethodCallMutator",
    "ConstructorCallMutator",
    "CallKwargDropMutator",
    "CallArgDropMutator",
}
C2_VALUE_MUTATORS = {
    "EmptyObjectReturnValsMutator",
    "NullReturnValsMutator",
    "PrimitiveReturnsMutator",
    "BooleanFalseReturnValsMutator",
    "BooleanTrueReturnValsMutator",
    "MathMutator",
    "IncrementsMutator",
    "InvertNegsMutator",
    "InlineConstantMutator",
    "MemberVariableMutator",
    "NumberMutator",
    "StringMutator",
    "BooleanMutator",
    "AugAssignMutator",
    "UnaryOpMutator",
    "DictEntryDropMutator",
    "SliceBoundMutator",
}


def _extract_mutator_class_from_summary(sm: str) -> str:
    """Parse exact mutator class name from summary `[role] Cls.meth:Lxx (MutatorClass: ...)`."""
    m = re.search(r"\(([A-Za-z0-9_]+):", sm)
    return m.group(1) if m else ""


def classify_patch_mechanisms_and_consequences(
    sample: Dict[str, Any],
    cand: Dict[str, Any],
    ev: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Non-fallback, diff-grounded dual-axis automated coding (auxiliary to 60-patch manual audit):
      Axis A — Patch Edit Mechanisms (derived strictly from *added/removed* diff between `orig` and `cand`):
        - `M1_PredicateProjectionOrRelaxation`
        - `M2_StateResetOrClobber`
        - `M3_ControlFlowOrExceptionChange`
        - `M4_SUTCallAlterationOrBypass`
        - `M5_PartnerOrSharedScopeEdit`
        - `M6_DelimiterOrFormatNormalization` (explicit diff pattern: substring/split/trim/regex/readTree/parseObject/float coercion)
        - `M7_PreconditionOrSingletonSetupAlteration` (explicit diff pattern: auto_assign_id/reflection setAccessible/responses.add/force_level/shutdown/setConnectionFactory)
        - `U_UnclassifiedAutomated` (honest unclassified residual — NEVER a thematic catch-all!)
      Axis B — Lost Obligation Consequences (parsed strictly from exact mutator class name, NO `"if"` grep!).
    """
    lang = sample.get("language", "java")
    orig_v = sample["orig_test_code"]
    cand_v = (cand.get("test_code") or cand.get("victim_code") or "")

    mechs: List[str] = []
    if lang == "java":
        _, b3p_rs = check_java_b3_plus({"victim": orig_v}, {"victim": cand_v})
    else:
        _, b3p_rs = check_py_b3_plus({"victim": orig_v}, {"victim": cand_v})
    rcodes = {r["code"] for r in b3p_rs}

    def _added_pattern(pat: str) -> bool:
        return len(re.findall(pat, cand_v)) > len(re.findall(pat, orig_v))

    def _removed_pattern(pat: str) -> bool:
        return len(re.findall(pat, cand_v)) < len(re.findall(pat, orig_v))

    surv_sums = ev.get("survived_mutant_summaries") or []
    if any(s.startswith("[partner]") for s in surv_sums) or ev.get("scope_kind") != "TARGET_ONLY":
        mechs.append("M5_PartnerOrSharedScopeEdit")

    if (
        "R_SUT_CALL_REMOVED" in rcodes
        or _added_pattern(r"MapSortField|ORDER_MAP_ENTRIES_BY_KEYS|Schema\.Parser|ReflectData\.get\(\)|mock|patch\(")
        or (_removed_pattern(r"\bcheck\s*\(") and _added_pattern(r"\bgetSchema\s*\("))
        or (_removed_pattern(r"\.toJSONString\s*\(") and not _added_pattern(r"JSON\.toJSONString\s*\("))
    ):
        mechs.append("M4_SUTCallAlterationOrBypass")

    if (
        _added_pattern(
            r"auto_assign_id|auto_id_type|getDeclaredField|setAccessible|responses\.add|Webservice\.getInstance|"
            r"force_level|JobRegistry\.getInstance\(\)\.shutdown|setConnectionFactory|URLDecoder\.decode"
        )
    ):
        mechs.append("M7_PreconditionOrSingletonSetupAlteration")

    if (
        _added_pattern(
            r"\.substring\s*\(|\.split\s*\(|\.trim\s*\(|Pattern\.compile|readTree\s*\(|readValue\s*\(|"
            r"parseObject\s*\(|parseArray\s*\(|org\.junit\.Assert|org\.springframework\.util\.Assert|\bfloat\s*\("
        )
    ):
        mechs.append("M6_DelimiterOrFormatNormalization")

    if (
        rcodes & {"R_ASSERT_COUNT_DROP", "R_PROJECTION_OR_WEAK_DOWNGRADE", "R_STRONG_PREDICATE_DROP", "R_TAUTOLOGY_OR_DUAL_SUT"}
        or _added_pattern(r"\.(?:contains|keySet|keys|startswith|size)\s*\(|\bassertNotNull\b|\bis not None\b|\b in \b")
    ):
        mechs.append("M1_PredicateProjectionOrRelaxation")

    if (
        "R_POST_ACT_STATE_CLOBBER" in rcodes
        or _added_pattern(r"\.(?:clear|reset|cache_clear)\s*\(|\bsetattr\s*\(|\bdel\s+|clearProperty\s*\(")
    ):
        mechs.append("M2_StateResetOrClobber")

    if (
        "R_EXCEPTION_SWALLOW" in rcodes
        or _added_pattern(r"\btry\b|\bcatch\b|\bexcept\b|\braises\b|Boolean\.getBoolean")
    ):
        mechs.append("M3_ControlFlowOrExceptionChange")

    if not mechs:
        mechs.append("U_UnclassifiedAutomated")

    # Strict exact mutator-class mapping (ZERO substring `"if"` grep!)
    conseqs: Set[str] = set()
    for sm in surv_sums:
        mcls = _extract_mutator_class_from_summary(sm)
        if mcls in C1_BRANCH_MUTATORS:
            conseqs.add("C1_BranchConditionBlindness")
        elif mcls in C3_CALL_MUTATORS:
            conseqs.add("C3_SideEffectCallBlindness")
        elif mcls in C2_VALUE_MUTATORS:
            conseqs.add("C2_ValueOrReturnBlindness")
        elif mcls:
            conseqs.add("C4_OtherMutatorClass")
    if not conseqs:
        conseqs.add("C4_OtherMutatorClass")

    return {
        "primary_mechanism": mechs[0],
        "all_mechanisms": mechs,
        "consequences": sorted(conseqs),
        "b3_plus_reason_codes": sorted(rcodes),
    }


def build_taxonomy_codebook(
    samples_by_id: Dict[str, Dict[str, Any]],
    k5_model_files: List[Path],
) -> Dict[str, Any]:
    appearances = 0
    unique_patches: Dict[str, Dict[str, Any]] = {}
    affected_tests: Set[str] = set()
    affected_projects: Set[str] = set()

    b3_bypass_unique = 0
    b3_plus_bypass_unique = 0

    for fp in k5_model_files:
        if not fp.exists():
            continue
        data = json.loads(fp.read_text(encoding="utf-8"))
        model = data["model"]
        for r in data.get("results", []):
            sid = r["sample_id"]
            smp = samples_by_id.get(sid)
            if not smp or not smp.get("v2_eligible", True):
                continue
            for st_name, st_rec in r["strategies"].items():
                for rd in st_rec["rounds"]:
                    ev = rd.get("eval")
                    if not ev or not ev.get("w_plus_passed") or not ev.get("eroded"):
                        continue
                    appearances += 1
                    cand = rd["candidate"]
                    cfp = candidate_fingerprint(cand)
                    ukey = f"{smp['project_name']}::{smp['sha'][:8]}::{sid}::{cfp}"
                    affected_tests.add(sid)
                    affected_projects.add(smp["project_name"])

                    if ukey not in unique_patches:
                        coding = classify_patch_mechanisms_and_consequences(smp, cand, ev)
                        lang = smp.get("language", "java")
                        orig_m = {"victim": smp["orig_test_code"]}
                        if smp.get("orig_polluter_code"):
                            orig_m["polluter"] = smp["orig_polluter_code"]
                        cand_m = {"victim": cand.get("test_code") or cand.get("victim_code") or ""}
                        if cand.get("polluter_code"):
                            cand_m["polluter"] = cand["polluter_code"]

                        b3_pass = (check_py_static_ast_guard(orig_m, cand_m)[0] if lang == "python" else check_static_ast_guard(orig_m, cand_m)[0])
                        b3p_pass = (check_py_b3_plus(orig_m, cand_m)[0] if lang == "python" else check_java_b3_plus(orig_m, cand_m)[0])
                        if b3_pass:
                            b3_bypass_unique += 1
                        if b3p_pass:
                            b3_plus_bypass_unique += 1

                        unique_patches[ukey] = {
                            "unique_patch_key": ukey,
                            "sample_id": sid,
                            "project_name": smp["project_name"],
                            "language": lang,
                            "od_subtype": smp.get("od_subtype", smp["test_type"]),
                            "first_seen_model": model,
                            "first_seen_strategy": st_name,
                            "joint_retention": ev.get("joint_retention"),
                            "survived_summaries": (ev.get("survived_mutant_summaries") or [])[:5],
                            "passes_b3": b3_pass,
                            "passes_b3_plus": b3p_pass,
                            "coding": coding,
                            "appearance_count": 1,
                        }
                    else:
                        unique_patches[ukey]["appearance_count"] += 1

    prim_cnt = Counter(p["coding"]["primary_mechanism"] for p in unique_patches.values())
    multi_cnt = Counter(m for p in unique_patches.values() for m in p["coding"]["all_mechanisms"])
    cons_cnt = Counter(c for p in unique_patches.values() for c in p["coding"]["consequences"])
    b3p_bypass_by_mech = Counter(
        p["coding"]["primary_mechanism"] for p in unique_patches.values() if p["passes_b3_plus"]
    )

    # Cross-check automated codebook against the 60-patch manual stratified ground-truth audit
    manual_audit_summary: Dict[str, Any] = {}
    manual_p = DATA_DIR / "taxonomy_manual_audit.json"
    if manual_p.exists():
        ma = json.loads(manual_p.read_text(encoding="utf-8"))
        m_entries = ma.get("entries", [])
        n_matched = 0
        n_any_label = 0
        n_checked = 0
        for me in m_entries:
            uk = me["unique_patch_key"]
            if uk in unique_patches:
                n_checked += 1
                auto_c = unique_patches[uk]["coding"]
                if auto_c["primary_mechanism"] == me["manual_primary_mechanism"]:
                    n_matched += 1
                if me["manual_primary_mechanism"] in auto_c["all_mechanisms"]:
                    n_any_label += 1
        manual_audit_summary = {
            "sample_size": ma.get("sample_size", len(m_entries)),
            "stratification": ma.get("stratification", {}),
            "manual_mechanism_counts": ma.get("manual_mechanism_counts", {}),
            "manual_severity_counts": ma.get("manual_severity_counts", {}),
            "auto_vs_manual_checked": n_checked,
            "auto_primary_exact_match_pct": round(100.0 * n_matched / max(1, n_checked), 2),
            "auto_multilabel_contains_manual_pct": round(100.0 * n_any_label / max(1, n_checked), 2),
        }

    out = {
        "total_strategy_round_appearances": appearances,
        "unique_eroded_patches": len(unique_patches),
        "distinct_tests_with_erosion": len(affected_tests),
        "distinct_projects_with_erosion": len(affected_projects),
        "unique_patches_bypassing_B3": b3_bypass_unique,
        "unique_patches_bypassing_B3_Plus": b3_plus_bypass_unique,
        "primary_mechanism_counts_unique_patches": dict(prim_cnt),
        "all_mechanism_multi_label_counts": dict(multi_cnt),
        "consequence_counts_unique_patches": dict(cons_cnt),
        "b3_plus_bypass_by_primary_mechanism": dict(b3p_bypass_by_mech),
        "manual_stratified_audit_60": manual_audit_summary,
    }
    (DATA_DIR / "taxonomy_audit_codebook.json").write_text(
        json.dumps({"summary": out, "entries": list(unique_patches.values())}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return out


# ============================================================================
# 4. Abstention Subtypes, Uncached Stage Latencies, & Offline B3/B3+ Matrix
# ============================================================================

def classify_compile_error_subtype(err_txt: str, lang: str) -> str:
    low = (err_txt or "").lower()
    if not low.strip() or "missing " in low:
        return "MISSING_DELIMITER_OR_EMPTY_PARSE"
    if lang == "python":
        if "syntaxerror" in low or "indentationerror" in low:
            return "PY_SYNTAX_OR_INDENTATION"
        return "PY_IMPORT_OR_NAME_ERROR"
    if "package " in low and "does not exist" in low:
        return "JAVA_MISSING_DEPENDENCY_OR_PACKAGE"
    if "cannot find symbol" in low:
        return "JAVA_MISSING_SYMBOL_OR_IMPORT"
    if "incompatible types" in low or "cannot be applied" in low or "no suitable method" in low:
        return "JAVA_TYPE_OR_SIGNATURE_MISMATCH"
    if "unreported exception" in low or "never thrown" in low:
        return "JAVA_CHECKED_EXCEPTION_MISMATCH"
    return "JAVA_OTHER_COMPILE_ERROR"


def quantiles_summary(vals: List[float]) -> Dict[str, Any]:
    if not vals:
        return {"n": 0, "median": None, "iqr": None, "p90": None, "max": None}
    s = sorted(vals)
    n = len(s)
    p25 = float(np.percentile(s, 25))
    p50 = float(np.percentile(s, 50))
    p75 = float(np.percentile(s, 75))
    p90 = float(np.percentile(s, 90))
    return {
        "n": n,
        "median": round(p50, 2),
        "p25_p75": [round(p25, 2), round(p75, 2)],
        "p90": round(p90, 2),
        "max": round(max(s), 2),
    }


def analyze_latencies_and_abstentions(
    samples: List[Dict[str, Any]],
    k5_model_files: List[Path],
) -> Dict[str, Any]:
    elig_samples = [s for s in samples if s.get("v2_eligible", True)]
    elig_ids = {s["sample_id"] for s in elig_samples}
    # 1. Pre-computation latencies: explicitly separate v1 target-only 3x baseline vs v2 PIT-ALL 3x (which builds M_det + M_ext)
    j_pre_v1 = [sum(s.get("pit_3x_wall_secs", [])) for s in elig_samples if s["language"] == "java"]
    j_pre_v2_all = [sum(s.get("pit_all_3x_secs", [])) for s in elig_samples if s["language"] == "java" and s.get("pit_all_3x_secs")]
    p_pre = [sum(s.get("pit_3x_wall_secs", [])) for s in elig_samples if s["language"] == "python"]
    j_single_base = [s["pit_median_sec"] for s in elig_samples if s["language"] == "java"]
    p_single_base = [s["pit_median_sec"] for s in elig_samples if s["language"] == "python"]

    # 2. Uncached in-loop stage latencies (`not rd["eval_cached"]` and `not rd["llm_cached"]`!)
    uncached_compile: Dict[str, List[float]] = {"java": [], "python": []}
    uncached_wplus: Dict[str, List[float]] = {"java": [], "python": []}
    uncached_target_mut: Dict[str, List[float]] = {"java": [], "python": []}
    uncached_partner_mut: Dict[str, List[float]] = {"java": [], "python": []}
    uncached_llm: Dict[str, List[float]] = defaultdict(list)
    reconstructed_stage_sum_muguard: Dict[str, List[float]] = defaultdict(list)
    cache_hit_rates: Dict[str, Dict[str, float]] = {}

    abstentions_by_model: Dict[str, Any] = {}

    for fp in k5_model_files:
        if not fp.exists():
            continue
        data = json.loads(fp.read_text(encoding="utf-8"))
        model = data["model"]
        term_counts = Counter()
        comp_subtypes = Counter()

        for r in data.get("results", []):
            if r.get("sample_id") not in elig_ids:
                continue
            lang = r["language"]
            for st_name, st_rec in r["strategies"].items():
                for rd in st_rec["rounds"]:
                    if not rd.get("llm_cached", False) and rd.get("llm_sec", 0) > 0:
                        uncached_llm[model].append(rd["llm_sec"])
                    ev = rd.get("eval")
                    # Strictly check `not rd.get("eval_cached", False)` on outer round!
                    if ev and not rd.get("eval_cached", False):
                        if ev.get("compile_sec", 0) > 0:
                            uncached_compile[lang].append(ev["compile_sec"])
                        if ev.get("compiled") and ev.get("w_plus_sec", 0) > 0:
                            uncached_wplus[lang].append(ev["w_plus_sec"])
                        if ev.get("w_plus_passed") and ev.get("target_pit_sec", 0) > 0:
                            uncached_target_mut[lang].append(ev["target_pit_sec"])
                        if ev.get("partner_pit_sec", 0) > 0:
                            uncached_partner_mut[lang].append(ev["partner_pit_sec"])

            # Reconstructed stage-sum service time (`sum(llm_sec + eval_total_sec)` over MuGuard rounds;
            # excludes failed API retries / network backoff and mixes cached recorded stage durations).
            ours = r["strategies"]["Ours_MuGuard_Flake"]
            sess_t = 0.0
            for rd in ours["rounds"]:
                sess_t += rd.get("llm_sec", 0.0)
                if rd.get("eval"):
                    sess_t += rd["eval"].get("total_eval_sec", 0.0)
            reconstructed_stage_sum_muguard[model].append(sess_t)

            if not ours["accepted"]:
                gs = ours["gate_state"]
                term_counts[gs] += 1
                if gs == "ABSTAIN_COMPILE":
                    last_ev = ours["rounds"][-1].get("eval") or {}
                    sub = classify_compile_error_subtype(last_ev.get("compile_error", ""), lang)
                    comp_subtypes[sub] += 1

        abstentions_by_model[model] = {
            "total_abstained": sum(term_counts.values()),
            "terminal_states": dict(term_counts),
            "compile_failure_subtypes": dict(comp_subtypes),
        }

    return {
        "selection_timeout_disclosure": "Benchmark construction filtered single-run clean-e_0 mutation probes at <= 90s (Java) and <= 45s (Python).",
        "cost_accounting_disclosure": (
            "Pre-computation reports v1 3x target-only baseline (`pit_3x_wall_secs`) and v2 3x Java PIT-ALL freeze (`pit_all_3x_secs`; "
            "partner 3x construction is not included in `pit_3x_wall_secs`). Session totals are reconstructed sums of recorded "
            "`llm_sec + total_eval_sec` stage durations (excluding failed API retries/backoff), NOT directly timed cold-start wall-clock."
        ),
        "precomputation_3x_baseline_sec": {
            "java_v1_target_only": quantiles_summary(j_pre_v1),
            "java_v2_pit_all": quantiles_summary(j_pre_v2_all),
            "java": quantiles_summary(j_pre_v1),
            "python": quantiles_summary(p_pre),
        },
        "baseline_single_probe_sec": {
            "java": quantiles_summary(j_single_base),
            "python": quantiles_summary(p_single_base),
            "combined": quantiles_summary(j_single_base + p_single_base),
        },
        "uncached_in_loop_stages_sec": {
            "compile_java": quantiles_summary(uncached_compile["java"]),
            "compile_python": quantiles_summary(uncached_compile["python"]),
            "w_plus_java": quantiles_summary(uncached_wplus["java"]),
            "w_plus_python": quantiles_summary(uncached_wplus["python"]),
            "target_mut_java": quantiles_summary(uncached_target_mut["java"]),
            "target_mut_python": quantiles_summary(uncached_target_mut["python"]),
            "partner_mut_java": quantiles_summary(uncached_partner_mut["java"]),
            "partner_mut_python": quantiles_summary(uncached_partner_mut["python"]),
        },
        "uncached_llm_call_sec": {m: quantiles_summary(v) for m, v in uncached_llm.items()},
        "reconstructed_stage_sum_muguard_sec": {m: quantiles_summary(v) for m, v in reconstructed_stage_sum_muguard.items()},
        "abstentions_breakdown": abstentions_by_model,
    }


# ============================================================================
# 5. Publication Figures (SQJ Clean Multi-Panel & Forest Plots)
# ============================================================================

def generate_v2_figures(summary: Dict[str, Any]) -> List[str]:
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({
        "font.family": "DejaVu Sans",
        "font.size": 11.0,
        "axes.titlesize": 11.5,
        "axes.labelsize": 11.0,
        "xtick.labelsize": 10.5,
        "ytick.labelsize": 10.5,
        "legend.fontsize": 10.0,
    })
    generated = []

    # --- Fig 1: Separated Historical Artifacts vs K=5 Models vs K=1 Gate (Readable >= 7.5pt in PDF!) ---
    fig, axes = plt.subplots(1, 3, figsize=(9.0, 3.8), sharey=True, gridspec_kw={"width_ratios": [1.05, 1.35, 0.9]})
    b1 = summary["b1_authoritative_audit"]
    b1_keys = ["FlakyDoctor_gpt", "FlakyDoctor_magicoder", "iPFlakies"]
    b1_labels = [
        f"FD-GPT\n(n={b1['FlakyDoctor_gpt']['official_available']})",
        f"FD-Mag\n(n={b1['FlakyDoctor_magicoder']['official_available']})",
        f"iPFlakies\n(n={b1['iPFlakies']['official_available']})",
    ]
    x0 = np.arange(len(b1_keys))
    w0 = [b1[k]["pass_w_plus_pct"] for k in b1_keys]
    s0 = [b1[k]["sound_fix_pct"] for k in b1_keys]
    axes[0].bar(x0 - 0.18, w0, 0.36, label="Pass@W+", color="#7aa6c2", edgecolor="black", linewidth=0.6)
    axes[0].bar(x0 + 0.18, s0, 0.36, label="SoundFix%", color="#2a7f62", edgecolor="black", linewidth=0.6)
    axes[0].set_xticks(x0)
    axes[0].set_xticklabels(b1_labels, fontsize=10.5)
    axes[0].set_ylabel("Cohort Rate (%)", fontsize=11.0)
    axes[0].set_title("(a) Historical Artifacts", fontsize=11.5)
    axes[0].set_ylim(0, 134)
    axes[0].set_yticks([0, 20, 40, 60, 80, 100])
    axes[0].grid(axis="y", linestyle="--", alpha=0.35)
    axes[0].legend(loc="upper left", fontsize=9.8, frameon=True)

    short_m = {
        "deepseek-flash": "DS-Flash",
        "deepseek-v4-pro": "DS-V4-Pro",
        "qwen3.7-plus": "Qwen3.7+",
        "kimi-k2.5": "Kimi K2.5",
        "glm-5": "GLM-5",
    }
    k5_models = [m for m in ("deepseek-flash", "deepseek-v4-pro", "qwen3.7-plus") if m in summary["k5_models"]]
    x1 = np.arange(len(k5_models))
    b0_w = [summary["k5_models"][m]["strategies"]["B0_Standard_Validator"]["pass_w_plus_pct"] for m in k5_models]
    b0_s = [summary["k5_models"][m]["strategies"]["B0_Standard_Validator"]["sound_fix_pct"] for m in k5_models]
    mg_s = [summary["k5_models"][m]["strategies"]["Ours_MuGuard_Flake"]["sound_fix_pct"] for m in k5_models]
    axes[1].bar(x1 - 0.26, b0_w, 0.26, label="Unconst. W+", color="#d9822b", edgecolor="black", linewidth=0.6)
    axes[1].bar(x1, b0_s, 0.26, label="Unconst. SF%", color="#8c969e", edgecolor="black", linewidth=0.6)
    axes[1].bar(x1 + 0.26, mg_s, 0.26, label="MuGuard", color="#1f6f8b", edgecolor="black", linewidth=0.6)
    axes[1].set_xticks(x1)
    axes[1].set_xticklabels([f"{short_m[m]}\n(n={summary['k5_models'][m]['n_eligible']})" for m in k5_models], fontsize=10.5)
    axes[1].set_title("(b) Multi-Round (K=5)", fontsize=11.5)
    axes[1].grid(axis="y", linestyle="--", alpha=0.35)
    axes[1].legend(loc="upper center", ncol=3, fontsize=8.4, frameon=True, handlelength=0.9, handletextpad=0.3, columnspacing=0.45)

    k1_models = [m for m in ("kimi-k2.5", "glm-5") if m in summary["k1_models"]]
    if k1_models:
        x2 = np.arange(len(k1_models))
        k1_b0w = [summary["k1_models"][m]["strategies"]["B0_Standard_Validator"]["pass_w_plus_pct"] for m in k1_models]
        k1_b0s = [summary["k1_models"][m]["strategies"]["B0_Standard_Validator"]["sound_fix_pct"] for m in k1_models]
        k1_mgs = [summary["k1_models"][m]["strategies"]["Ours_MuGuard_Flake"]["sound_fix_pct"] for m in k1_models]
        axes[2].bar(x2 - 0.26, k1_b0w, 0.26, color="#d9822b", edgecolor="black", linewidth=0.6)
        axes[2].bar(x2, k1_b0s, 0.26, color="#8c969e", edgecolor="black", linewidth=0.6)
        axes[2].bar(x2 + 0.26, k1_mgs, 0.26, color="#1f6f8b", edgecolor="black", linewidth=0.6)
        axes[2].set_xticks(x2)
        axes[2].set_xticklabels([f"{short_m.get(m, m)}\n(n={summary['k1_models'][m]['n_eligible']})" for m in k1_models], fontsize=10.5)
    axes[2].set_title("(c) Single-Pass (K=1)", fontsize=11.5)
    axes[2].grid(axis="y", linestyle="--", alpha=0.35)

    fig.tight_layout(w_pad=0.9)
    for ext in ("pdf", "png"):
        fig.savefig(FIG_DIR / f"fig1_inflation_gap_models.{ext}", dpi=300)
    plt.close(fig)
    generated.append("fig1_inflation_gap_models")

    # --- Fig 2: Paired Project-Clustered Effect-Size Forest Plot (Readable >= 8pt PDF Typography!) ---
    fig, axes = plt.subplots(1, 2, figsize=(9.4, 5.0), sharey=True)
    rows_op = []
    rows_ext = []
    for m in k5_models:
        inf_op = summary["k5_models"][m]["clustered_inference_operational"]
        inf_ex = summary["k5_models"][m]["clustered_inference_heldout_yield"]
        sm = short_m.get(m, m)
        for comp_key, short_lbl in [
            ("Ours_vs_B0", "vs. Unconst."),
            ("Ours_vs_B3", "vs. Syntactic"),
            ("Ours_vs_B3Plus", "vs. Structural"),
            ("Ours_vs_B4Scalar", "vs. Scalar"),
        ]:
            if comp_key in inf_op:
                d = inf_op[comp_key]
                rows_op.append((f"{sm} {short_lbl}", d["diff_test_weighted_pp"], d["cluster_ci95_test_weighted"]))
            if comp_key in inf_ex:
                d2 = inf_ex[comp_key]
                rows_ext.append((f"{sm} {short_lbl}", d2["diff_test_weighted_pp"], d2["cluster_ci95_test_weighted"]))

    for ax, rlist, title_s in [
        (axes[0], rows_op, "(a) Operational SoundFix% Gain (pp)"),
        (axes[1], rows_ext, "(b) Fixed-Cohort Held-Out $Y_s$ Diff. (pp)"),
    ]:
        y_pos = np.arange(len(rlist))[::-1]
        ax.axvline(0.0, color="black", linestyle="-", linewidth=1.0)
        ax.axvline(5.0, color="#b22222", linestyle=":", linewidth=1.1, label="Min. practical (+5 pp)")
        for sep_y in (3.5, 7.5):
            if sep_y < len(rlist) - 0.5:
                ax.axhline(sep_y, color="#cbd5e1", linestyle="-", linewidth=0.95, zorder=1)
        for yp, (lbl, est, ci) in zip(y_pos, rlist):
            ax.errorbar(est, yp, xerr=[[est - ci[0]], [ci[1] - est]], fmt="o", color="#1f6f8b", capsize=4.0, markersize=6.0, linewidth=1.6)
        ax.set_yticks(y_pos)
        ax.set_yticklabels([r[0] for r in rlist], fontsize=10.5)
        ax.set_ylim(-1.6, len(rlist) - 0.3)
        ax.tick_params(axis="x", labelsize=10.5)
        ax.set_xlabel("Paired Diff. & 95% Cluster CI (pp)", fontsize=10.8)
        ax.set_title(title_s, fontsize=11.5)
        ax.grid(axis="x", linestyle="--", alpha=0.35)
        ax.legend(loc="lower right" if ax is axes[0] else "lower left", fontsize=9.8, frameon=True)

    fig.tight_layout()
    for ext in ("pdf", "png"):
        fig.savefig(FIG_DIR / f"fig2_round_trajectories.{ext}", dpi=300)
    plt.close(fig)
    generated.append("fig2_round_trajectories")

    # --- Fig 3: Prefix-Forked Witness Experiment (Human-Readable Arm Names) ---
    ab = summary.get("forked_ablation", {})
    if ab and ab.get("n_forks", 0) > 0:
        fig, ax = plt.subplots(figsize=(6.4, 3.9))
        arms = ["A1_ScalarRatio", "A2_MisleadingLocation", "A3_TrueWitness"]
        n_a2 = ab["arms"]["A2_MisleadingLocation"]["total_forks"]
        arm_lbls = [
            f"Scalar Ratio\n(k/M, n={ab['n_forks']})",
            f"Misleading Witness\n(Stress, n={n_a2})",
            f"True Witness\n(Surv., n={ab['n_forks']})",
        ]
        rates = [ab["arms"][a]["recovery_pct"] for a in arms]
        cis = [ab["arms"][a]["cluster_ci95"] for a in arms]
        errs_lo = [r - c[0] for r, c in zip(rates, cis)]
        errs_hi = [c[1] - r for r, c in zip(rates, cis)]
        bars = ax.bar(np.arange(3), rates, width=0.5, yerr=[errs_lo, errs_hi], capsize=4,
                      color=["#6c8ebf", "#c97a63", "#2a7f62"], edgecolor="black", linewidth=0.7)
        ax.axhline(0.0, color="gray", linewidth=0.8)
        ax.set_xticks(np.arange(3))
        ax.set_xticklabels(arm_lbls, fontsize=10.8)
        ax.tick_params(axis="y", labelsize=10.8)
        ax.set_ylabel("Post-Fork Recovery Rate (%)", fontsize=11.0)
        ax.set_ylim(0, 110)
        ax.set_yticks([0, 20, 40, 60, 80, 100])
        ax.set_title("Prefix-Forked Recovery (Gate-Only = 0%)", fontsize=11.5)
        ax.grid(axis="y", linestyle="--", alpha=0.35)
        for b, r_val, ci_pair in zip(bars, rates, cis):
            ax.text(b.get_x() + b.get_width() / 2.0, ci_pair[1] + 2.2, f"{r_val:.1f}%", ha="center", va="bottom", fontsize=11.0, fontweight="bold")
        fig.tight_layout()
        for ext in ("pdf", "png"):
            fig.savefig(FIG_DIR / f"fig4_n14_ablation_and_pareto.{ext}", dpi=300)
        plt.close(fig)
        generated.append("fig4_n14_ablation_and_pareto")

    # --- Fig 4: Uncached Stage Latency & Offline Subset-Sampling Sensitivity (>= 7.8pt in PDF!) ---
    lat = summary["latency_and_abstentions"]["uncached_in_loop_stages_sec"]
    tk_joint = summary["topk_offline_sensitivity"]["joint_pool"]["rows"]
    fig, axes = plt.subplots(1, 2, figsize=(8.8, 3.8))

    st_names = ["Compile\n(Java)", "W+\n(Java)", "Tgt Mut\n(Java)", "W+\n(Py)", "Tgt Mut\n(Py)"]
    meds = [
        lat["compile_java"]["median"] or 0,
        lat["w_plus_java"]["median"] or 0,
        lat["target_mut_java"]["median"] or 0,
        lat["w_plus_python"]["median"] or 0,
        lat["target_mut_python"]["median"] or 0,
    ]
    p90s = [
        lat["compile_java"]["p90"] or 0,
        lat["w_plus_java"]["p90"] or 0,
        lat["target_mut_java"]["p90"] or 0,
        lat["w_plus_python"]["p90"] or 0,
        lat["target_mut_python"]["p90"] or 0,
    ]
    x_s = np.arange(len(st_names))
    axes[0].bar(x_s - 0.18, meds, 0.36, label="Median (s)", color="#2a7f62", edgecolor="black", linewidth=0.6)
    axes[0].bar(x_s + 0.18, p90s, 0.36, label="P90 (s)", color="#e09f3e", edgecolor="black", linewidth=0.6)
    axes[0].set_xticks(x_s)
    axes[0].set_xticklabels(st_names, fontsize=10.5)
    axes[0].tick_params(axis="y", labelsize=10.5)
    axes[0].set_ylabel("Uncached Time (s)", fontsize=11.0)
    axes[0].set_title("(a) Uncached Stage Latencies", fontsize=11.5)
    axes[0].legend(fontsize=10.2, frameon=True)
    axes[0].grid(axis="y", linestyle="--", alpha=0.35)

    if tk_joint:
        xlbl = [r["budget_label"].replace("Top-", "k=") for r in tk_joint]
        xp = np.arange(len(xlbl))
        axes[1].plot(xp, [r["uniform_random_recall_pct"] for r in tk_joint], "o--", label="Uniform Random", color="#6c757d")
        axes[1].plot(xp, [r["file_line_dispersion_recall_pct"] for r in tk_joint], "s-", label="File/Line Disp.", color="#1f6f8b")
        axes[1].plot(xp, [r["operator_priority_recall_pct"] for r in tk_joint], "^-", label="Op.-Priority", color="#d9822b")
        axes[1].set_xticks(xp)
        axes[1].set_xticklabels(xlbl, fontsize=10.5)
        axes[1].tick_params(axis="y", labelsize=10.5)
        axes[1].set_ylim(0, 106)
        axes[1].set_ylabel("Detection Recall (%)", fontsize=11.0)
        axes[1].set_title("(b) Offline Subset Sensitivity (Joint)", fontsize=11.5)
        axes[1].legend(fontsize=10.0, loc="lower right", frameon=True)
        axes[1].grid(True, linestyle="--", alpha=0.35)

    fig.tight_layout()
    for ext in ("pdf", "png"):
        fig.savefig(FIG_DIR / f"fig5_latency_distributions.{ext}", dpi=300)
    plt.close(fig)
    generated.append("fig5_latency_distributions")

    return generated


# ============================================================================
# 6. Master End-to-End Aggregation (`v2_study_summary_metrics.json`)
# ============================================================================

def summarize_b1_v2(samples: List[Dict[str, Any]]) -> Dict[str, Any]:
    elig = [s for s in samples if s.get("v2_eligible", True)]
    out: Dict[str, Any] = {}
    for key, lang_f, csv_flag in [
        ("FlakyDoctor_gpt", "java", "in_official_csv_P"),
        ("FlakyDoctor_magicoder", "java", "in_official_csv_P"),
        ("iPFlakies", "python", "in_official_patches_csv"),
    ]:
        sub_key = "gpt" if key == "FlakyDoctor_gpt" else ("magicoder" if key == "FlakyDoctor_magicoder" else "ipflakies")
        all_recs = []
        for s in elig:
            if s["language"] != lang_f:
                continue
            b = (s.get("v2_b1_audit") or {}).get(sub_key)
            if b:
                all_recs.append(b)
        if key == "iPFlakies":
            # Strictly require BOTH official CSV membership AND benchmark target-partner match!
            off = [r for r in all_recs if r.get(csv_flag) and r.get("csv_partner_matches_benchmark")]
            partner_mismatch = [r for r in all_recs if r.get(csv_flag) and not r.get("csv_partner_matches_benchmark")]
            unmatched = [r for r in all_recs if not r.get(csv_flag)]
        else:
            off = [r for r in all_recs if r.get(csv_flag)]
            partner_mismatch = []
            unmatched = [r for r in all_recs if not r.get(csv_flag)]
        stages = Counter(r.get("stage_outcome", "UNKNOWN") for r in off)
        comp = [r for r in off if r.get("compiled")]
        w_ok = [r for r in comp if r.get("w_plus_passed")]
        eroded = [r for r in w_ok if r.get("eroded")]
        sound = [r for r in w_ok if r.get("stage_outcome") == "OPERATIONAL_SOUND_FIX"]
        salv = sum(1 for r in off if r.get("salvage_compile_would_pass"))

        out[key] = {
            "official_available": len(off),
            "excluded_unmatched_archive": len(unmatched),
            "excluded_partner_mismatch": len(partner_mismatch),
            "stage_outcomes": dict(stages),
            "verbatim_compiled": len(comp),
            "import_salvage_only_count": salv,
            "w_plus_count": len(w_ok),
            "pass_w_plus_pct": round(100.0 * len(w_ok) / max(1, len(off)), 2),
            "eroded_count": len(eroded),
            "leaked_erosion_among_w_plus_pct": round(100.0 * len(eroded) / max(1, len(w_ok)), 2) if w_ok else 0.0,
            "sound_fix_count": len(sound),
            "sound_fix_pct": round(100.0 * len(sound) / max(1, len(off)), 2),
        }
    return out


def summarize_model_v2(
    model_json: Dict[str, Any],
    samples_by_id: Dict[str, Dict[str, Any]],
    heldout_map: Dict[str, Any],
    is_k5: bool,
) -> Dict[str, Any]:
    rows_all = model_json["results"]
    rows_by_sid = {r["sample_id"]: r for r in rows_all}
    rows_elig = [r for r in rows_all if samples_by_id.get(r["sample_id"], {}).get("v2_eligible", True)]
    n_elig = len(rows_elig)
    n_itt = len(rows_all)

    # FIXED frozen pre-defined external cohort `E`: ALL frozen eligible samples possessing `|M_ext| >= 1`
    # (NEVER shrinks when an LLM hits API cap on a sample — missing runs are counted as 0 in fixed-cohort Y_s
    # and reported explicitly alongside completer-only sensitivity!)
    frozen_elig_ids = sorted(sid for sid, s in samples_by_id.items() if s.get("v2_eligible", True))
    ext_cohort_ids = sorted(
        sid for sid in frozen_elig_ids
        if samples_by_id[sid].get("m_ext_total_count", 0) >= 1
    )
    n_ext = len(ext_cohort_ids)
    completed_ext_ids = [sid for sid in ext_cohort_ids if sid in rows_by_sid]
    incomplete_api_in_E = n_ext - len(completed_ext_ids)

    # Stratified sub-cohorts (Java mutator-family value-flow vs Python assertion-kill-only)
    vf_cohort_ids = sorted(
        sid for sid in frozen_elig_ids
        if (
            samples_by_id[sid]["language"] == "java"
            and len(samples_by_id[sid].get("m_ext_op_value_flow") or []) >= 1
        ) or (
            samples_by_id[sid]["language"] == "python"
            and samples_by_id[sid].get("m_ext_total_count", 0) >= 1
        )
    )
    py_assert_cohort_ids = sorted(
        sid for sid in frozen_elig_ids
        if samples_by_id[sid]["language"] == "python" and samples_by_id[sid].get("m_ext_total_count", 0) >= 1
    )

    strats = V2_STRATEGIES_K5 if is_k5 else V2_STRATEGIES_K1
    strat_summaries: Dict[str, Any] = {}

    for st in strats:
        s_elig = [(r, r["strategies"][st]) for r in rows_elig if st in r["strategies"]]
        w_ok = [(r, x) for r, x in s_elig if x["w_plus_passed"]]
        eroded = [(r, x) for r, x in w_ok if x["eroded"]]
        sound = [(r, x) for r, x in s_elig if x["sound_fix"]]
        unver_part = [
            (r, x) for r, x in w_ok
            if (not x["eroded"]) and (not x["sound_fix"]) and ((x.get("final_eval") or {}).get("gate_status_override") == "ABSTAIN_PARTNER_UNCOVERED")
        ]
        # FIX ITT-208% numerator: strictly count eligible sound fixes only (excluded attrition tests count 0!)
        sf_itt_elig = len(sound)

        sf_rate, sf_ci = cluster_rate_ci([(r["project_name"], int(x["sound_fix"])) for r, x in s_elig])
        t_rets = [x["target_retention"] for _, x in w_ok if x.get("target_retention") is not None]
        p_rets = [x["partner_retention"] for _, x in w_ok if x.get("partner_retention") is not None]
        j_rets = [x["joint_retention"] for _, x in w_ok if x.get("joint_retention") is not None]
        g_rets = [x["retention_m_gold"] for _, x in w_ok if x.get("retention_m_gold") is not None]

        # Language breakdown
        by_lang = {}
        for lg in ("java", "python"):
            sub = [(r, x) for r, x in s_elig if r["language"] == lg]
            if sub:
                sw = [x for _, x in sub if x["w_plus_passed"]]
                se = [x for x in sw if x["eroded"]]
                ss = [x for _, x in sub if x["sound_fix"]]
                by_lang[lg] = {
                    "n": len(sub),
                    "w_plus": len(sw),
                    "pass_w_plus_pct": round(100.0 * len(sw) / len(sub), 2),
                    "leaked_eroded": len(se),
                    "leaked_pct": round(100.0 * len(se) / max(1, len(sw)), 2) if sw else 0.0,
                    "sound_fix": len(ss),
                    "sound_fix_pct": round(100.0 * len(ss) / len(sub), 2),
                }

        # Held-out external validation (`Y_s` over FIXED frozen cohort `E`)
        ext_info: Dict[str, Any] = {}
        if is_k5 and heldout_map and n_ext > 0:
            mext_adj_path = DATA_DIR / "mext_loss_adjudication_input.json"
            mext_adj_entries = json.loads(mext_adj_path.read_text(encoding="utf-8")).get("entries", {}) if mext_adj_path.exists() else {}
            y_strict_cnt = 0
            y_stress_cnt = 0
            y_vf_cnt = 0
            y_vf_stress_cnt = 0
            y_py_assert_cnt = 0
            unknown_mext_cnt = 0
            total_unknown_mutants = 0
            acc_in_E = 0
            ho_wplus_fail_strict_in_acc = 0
            ho_wplus_fail_stress_in_acc = 0
            ext_loss_in_acc = 0
            sem_counts = Counter()
            ext_rets = []
            vf_rets = []

            for sid in ext_cohort_ids:
                r = rows_by_sid.get(sid)
                if not r or st not in r["strategies"]:
                    continue
                x = r["strategies"][st]
                if not x["accepted"] or not x.get("final_candidate"):
                    continue
                acc_in_E += 1
                cfp = candidate_fingerprint(x["final_candidate"])
                ck = f"{sid}::{cfp}"
                hev = heldout_map.get(ck)
                if not hev or not hev.get("m_ext_eval_ok") or hev.get("m_ext_all_unknown", 0) > 0:
                    unknown_mext_cnt += 1
                    total_unknown_mutants += int((hev or {}).get("m_ext_all_unknown", 1))
                    continue
                who_strict = bool(hev.get("w_plus_heldout_passed_strict", hev.get("w_plus_heldout_passed")))
                who_stress = bool(hev.get("w_plus_heldout_passed_target_stress", who_strict))
                if not who_strict:
                    ho_wplus_fail_strict_in_acc += 1
                if not who_stress:
                    ho_wplus_fail_stress_in_acc += 1
                r_all = hev.get("m_ext_all_retention")
                if r_all is not None:
                    ext_rets.append(r_all)
                    if r_all < 1.0:
                        ext_loss_in_acc += 1
                        adj_e = mext_adj_entries.get(ck)
                        sem_counts[adj_e["label"] if adj_e else "UNADJUDICATED"] += 1
                    if who_strict and r_all == 1.0:
                        y_strict_cnt += 1
                    if who_stress and r_all == 1.0:
                        y_stress_cnt += 1

                if sid in vf_cohort_ids:
                    r_vf = hev.get("op_value_flow_retention") if r["language"] == "java" else hev.get("m_ext_assert_only_retention", r_all)
                    if r_vf is not None:
                        vf_rets.append(r_vf)
                        if who_strict and r_vf == 1.0:
                            y_vf_cnt += 1
                        if who_stress and r_vf == 1.0:
                            y_vf_stress_cnt += 1
                if sid in py_assert_cohort_ids:
                    r_pa = hev.get("m_ext_assert_only_retention")
                    if who_strict and r_pa == 1.0:
                        y_py_assert_cnt += 1

            denom_known_acc = max(1, acc_in_E - unknown_mext_cnt)
            gw_c = sem_counts.get("GENUINE_ORACLE_WEAKENING", 0)
            rs_c = sem_counts.get("TEST_RESPONSIBILITY_SHIFT", 0)
            lc_c = sem_counts.get("LEGITIMATE_RELAXATION_OR_CANONICALIZATION", 0)
            un_c = sem_counts.get("UNADJUDICATED", 0)
            ext_info = {
                "cohort_E_size_fixed": n_ext,
                "completed_in_E": len(completed_ext_ids),
                "incomplete_api_in_E": incomplete_api_in_E,
                "accepted_in_E": acc_in_E,
                "accepted_in_E_pct": round(100.0 * acc_in_E / n_ext, 2),
                "unknown_eval_count": unknown_mext_cnt,
                "total_unknown_mutants_in_accepted": total_unknown_mutants,
                "heldout_w_plus_failures_among_accepted": ho_wplus_fail_strict_in_acc,
                "heldout_w_plus_failures_strict_among_accepted": ho_wplus_fail_strict_in_acc,
                "heldout_w_plus_failures_stress_among_accepted": ho_wplus_fail_stress_in_acc,
                "heldout_m_ext_loss_among_accepted": ext_loss_in_acc,
                "conditional_m_ext_loss_pct": round(100.0 * ext_loss_in_acc / denom_known_acc, 2) if acc_in_E > unknown_mext_cnt else 0.0,
                "test_level_adjudicated_mext_loss": {
                    "genuine_weakening_count": gw_c,
                    "genuine_weakening_cond_pct": round(100.0 * gw_c / denom_known_acc, 2) if acc_in_E > unknown_mext_cnt else 0.0,
                    "responsibility_shift_count": rs_c,
                    "responsibility_shift_cond_pct": round(100.0 * rs_c / denom_known_acc, 2) if acc_in_E > unknown_mext_cnt else 0.0,
                    "legit_canonicalization_count": lc_c,
                    "legit_canonicalization_cond_pct": round(100.0 * lc_c / denom_known_acc, 2) if acc_in_E > unknown_mext_cnt else 0.0,
                    "unadjudicated_count": un_c,
                },
                "mean_m_ext_retention": round(statistics.mean(ext_rets), 4) if ext_rets else None,
                "mean_value_flow_or_assert_retention": round(statistics.mean(vf_rets), 4) if vf_rets else None,
                "end_to_end_heldout_yield_Ys_count": y_strict_cnt,
                "end_to_end_heldout_yield_Ys_pct": round(100.0 * y_strict_cnt / n_ext, 2),
                "end_to_end_heldout_yield_Ys_stress_count": y_stress_cnt,
                "end_to_end_heldout_yield_Ys_stress_pct": round(100.0 * y_stress_cnt / n_ext, 2),
                "completer_only_Ys_pct": round(100.0 * y_strict_cnt / max(1, len(completed_ext_ids)), 2),
                "completer_only_Ys_stress_pct": round(100.0 * y_stress_cnt / max(1, len(completed_ext_ids)), 2),
                "worst_case_Ys_pct": round(100.0 * y_strict_cnt / n_ext, 2),
                "best_case_Ys_pct": round(100.0 * (y_strict_cnt + unknown_mext_cnt + incomplete_api_in_E) / n_ext, 2),
                "best_case_eval_unknown_only_Ys_pct": round(100.0 * (y_strict_cnt + unknown_mext_cnt) / n_ext, 2),
                "value_flow_cohort_size": len(vf_cohort_ids),
                "value_flow_Ys_pct": round(100.0 * y_vf_cnt / max(1, len(vf_cohort_ids)), 2),
                "value_flow_Ys_stress_pct": round(100.0 * y_vf_stress_cnt / max(1, len(vf_cohort_ids)), 2),
                "python_assert_only_cohort_size": len(py_assert_cohort_ids),
                "python_assert_only_Ys_count": y_py_assert_cnt,
            }

        strat_summaries[st] = {
            "n_eligible_completed": n_elig,
            "n_eligible_frozen": len(frozen_elig_ids),
            "w_plus_count": len(w_ok),
            "pass_w_plus_pct": round(100.0 * len(w_ok) / max(1, n_elig), 2),
            "leaked_eroded_count": len(eroded),
            "leaked_erosion_among_w_plus_pct": round(100.0 * len(eroded) / max(1, len(w_ok)), 2) if w_ok else 0.0,
            "sound_fix_count": len(sound),
            "unverified_partner_accepted_count": len(unver_part),
            "sound_fix_pct": sf_rate,
            "sound_fix_cluster_ci95": sf_ci,
            "itt_208_sound_fix_count": sf_itt_elig,
            "itt_208_sound_fix_pct": round(100.0 * sf_itt_elig / 208.0, 2),
            "mean_target_retention": round(statistics.mean(t_rets), 4) if t_rets else None,
            "mean_partner_retention": round(statistics.mean(p_rets), 4) if p_rets else None,
            "mean_joint_retention": round(statistics.mean(j_rets), 4) if j_rets else None,
            "mean_accepted_pr_gold_retention": round(statistics.mean(g_rets), 4) if g_rets else None,
            "ast_rejections": sum(x["ast_rejections"] for _, x in s_elig),
            "erosion_retries": sum(x["erosion_retries"] for _, x in s_elig),
            "by_language": by_lang,
            "heldout_external": ext_info,
        }

    # Add exact relative conditional M_ext loss reduction rates for Ours vs B0 / B3+ / B4
    if is_k5 and heldout_map and n_ext > 0 and "Ours_MuGuard_Flake" in strat_summaries:
        ours_cl = strat_summaries["Ours_MuGuard_Flake"]["heldout_external"]["conditional_m_ext_loss_pct"]
        rel_red = {}
        for ref_k in ("B0_Standard_Validator", "B3_Plus_Structural_Guard", "B4_Scalar_Gate_Loop"):
            ref_cl = strat_summaries[ref_k]["heldout_external"]["conditional_m_ext_loss_pct"]
            rel_red[ref_k] = round(100.0 * (ref_cl - ours_cl) / ref_cl, 2) if ref_cl > 0 else 0.0
        strat_summaries["Ours_MuGuard_Flake"]["heldout_external"]["relative_conditional_loss_reduction_pct"] = rel_red

    # Clustered paired inference for K=5 (on FIXED frozen cohort E for Y_s, with incomplete API = 0!)
    inf_op: Dict[str, Any] = {}
    inf_ext: Dict[str, Any] = {}
    inf_ext_completers: Dict[str, Any] = {}
    inf_ext_stress: Dict[str, Any] = {}
    if is_k5:
        op_recs = []
        for r in rows_elig:
            st_d = r["strategies"]
            op_recs.append({
                "project": r["project_name"],
                "Ours": int(st_d["Ours_MuGuard_Flake"]["sound_fix"]),
                "B0": int(st_d["B0_Standard_Validator"]["sound_fix"]),
                "B2": int(st_d["B2_Prompt_Guard"]["sound_fix"]),
                "B3": int(st_d["B3_Static_AST_Guard"]["sound_fix"]),
                "B3Plus": int(st_d["B3_Plus_Structural_Guard"]["sound_fix"]),
                "B4Scalar": int(st_d["B4_Scalar_Gate_Loop"]["sound_fix"]),
            })
        for cname, kb in [
            ("Ours_vs_B0", "B0"),
            ("Ours_vs_B3", "B3"),
            ("Ours_vs_B3Plus", "B3Plus"),
            ("Ours_vs_B4Scalar", "B4Scalar"),
            ("B3Plus_vs_B3", None),
        ]:
            if cname == "B3Plus_vs_B3":
                inf_op[cname] = paired_project_cluster_inference(op_recs, "B3Plus", "B3")
            else:
                inf_op[cname] = paired_project_cluster_inference(op_recs, "Ours", kb)

        fam1_holm = holm_bonferroni({
            k: inf_op[k]["p_cluster_signflip"]
            for k in ("Ours_vs_B0", "Ours_vs_B3", "Ours_vs_B3Plus")
        })
        for k, ph in fam1_holm.items():
            inf_op[k]["p_holm_family1"] = ph

        if heldout_map and n_ext > 0:
            ext_recs_fixed = []
            ext_recs_comp = []
            ext_recs_stress = []
            for sid in ext_cohort_ids:
                r = rows_by_sid.get(sid)
                proj = samples_by_id[sid]["project_name"]
                row_e = {"project": proj}
                row_s = {"project": proj}
                for short_k, full_k in [
                    ("Ours", "Ours_MuGuard_Flake"),
                    ("B0", "B0_Standard_Validator"),
                    ("B3", "B3_Static_AST_Guard"),
                    ("B3Plus", "B3_Plus_Structural_Guard"),
                    ("B4Scalar", "B4_Scalar_Gate_Loop"),
                ]:
                    ok_strict = 0
                    ok_stress = 0
                    if r and full_k in r["strategies"]:
                        x = r["strategies"][full_k]
                        if x["accepted"] and x.get("final_candidate"):
                            hev = heldout_map.get(f"{sid}::{candidate_fingerprint(x['final_candidate'])}")
                            if (
                                hev
                                and hev.get("m_ext_eval_ok")
                                and hev.get("m_ext_all_unknown", 0) == 0
                                and hev.get("m_ext_all_retention") == 1.0
                            ):
                                who_st = bool(hev.get("w_plus_heldout_passed_strict", hev.get("w_plus_heldout_passed")))
                                who_sr = bool(hev.get("w_plus_heldout_passed_target_stress", who_st))
                                ok_strict = int(who_st)
                                ok_stress = int(who_sr)
                    row_e[short_k] = ok_strict
                    row_s[short_k] = ok_stress
                ext_recs_fixed.append(row_e)
                ext_recs_stress.append(row_s)
                if r is not None:
                    ext_recs_comp.append(row_e)

            for cname, kb in [
                ("Ours_vs_B0", "B0"),
                ("Ours_vs_B3", "B3"),
                ("Ours_vs_B3Plus", "B3Plus"),
                ("Ours_vs_B4Scalar", "B4Scalar"),
            ]:
                inf_ext[cname] = paired_project_cluster_inference(ext_recs_fixed, "Ours", kb)
                inf_ext_completers[cname] = paired_project_cluster_inference(ext_recs_comp, "Ours", kb)
                inf_ext_stress[cname] = paired_project_cluster_inference(ext_recs_stress, "Ours", kb)
            fam2_holm = holm_bonferroni({
                k: inf_ext[k]["p_cluster_signflip"]
                for k in ("Ours_vs_B0", "Ours_vs_B3", "Ours_vs_B3Plus")
            })
            for k, ph in fam2_holm.items():
                inf_ext[k]["p_holm_family2"] = ph
            fam2s_holm = holm_bonferroni({
                k: inf_ext_stress[k]["p_cluster_signflip"]
                for k in ("Ours_vs_B0", "Ours_vs_B3", "Ours_vs_B3Plus")
            })
            for k, ph in fam2s_holm.items():
                inf_ext_stress[k]["p_holm_family2_stress"] = ph

    return {
        "n_attempted_itt": n_itt,
        "n_eligible": n_elig,
        "n_eligible_frozen": len(frozen_elig_ids),
        "strategies": strat_summaries,
        "clustered_inference_operational": inf_op,
        "clustered_inference_heldout_yield": inf_ext,
        "clustered_inference_heldout_yield_completers": inf_ext_completers,
        "clustered_inference_heldout_yield_target_stress": inf_ext_stress,
    }


def equal_model_weighted_cluster_bootstrap(
    cases: List[Dict[str, Any]],
    n_boot: int = 20000,
    seed: int = 20261122,
) -> Dict[str, Any]:
    """
    Pre-registered equal-model-weighted cluster bootstrap for `A3 - A1`:
    Resamples whole projects jointly across all 3 models, computes each model's
    `A3 - A1` risk difference (pp), and averages equally across the 3 models.
    """
    models = sorted({c["model"] for c in cases})
    projs = sorted({c["project_name"] for c in cases})
    P = len(projs)
    M = len(models)
    if P == 0 or M == 0:
        return {}

    # Build `(M, P)` arrays of `(n_mp, diff_sum_mp)`
    n_mp = np.zeros((M, P), dtype=np.float64)
    d_mp = np.zeros((M, P), dtype=np.float64)
    m_idx = {m: i for i, m in enumerate(models)}
    p_idx = {p: j for j, p in enumerate(projs)}
    for c in cases:
        mi = m_idx[c["model"]]
        pj = p_idx[c["project_name"]]
        n_mp[mi, pj] += 1.0
        d_mp[mi, pj] += float(int(c["A3_TrueWitness"]["recovered"]) - int(c["A1_ScalarRatio"]["recovered"]))

    obs_per_m = (np.sum(d_mp, axis=1) / np.maximum(1.0, np.sum(n_mp, axis=1))) * 100.0
    obs_eq_m = float(np.mean(obs_per_m))

    rng = np.random.default_rng(seed)
    idx_mat = rng.integers(0, P, size=(n_boot, P))
    boot_eq_vals = np.zeros(n_boot, dtype=np.float64)
    for mi in range(M):
        bn = np.sum(n_mp[mi][idx_mat], axis=1)
        bd = np.sum(d_mp[mi][idx_mat], axis=1)
        boot_eq_vals += (bd / np.maximum(1.0, bn)) * 100.0
    boot_eq_vals /= float(M)
    lo, hi = np.percentile(boot_eq_vals, [2.5, 97.5])

    # Project-level sign-flip on equal-model-weighted project contributions: sum_m (d_mp / N_m)
    tot_n_m = np.maximum(1.0, np.sum(n_mp, axis=1))
    proj_contrib = np.sum((d_mp / tot_n_m[:, None]), axis=0) / float(M)
    nz = proj_contrib[np.abs(proj_contrib) > 1e-12]
    obs_abs = abs(float(np.sum(proj_contrib)))
    if len(nz) == 0:
        p_eq = 1.0
    elif len(nz) <= 16:
        signs = np.array(np.meshgrid(*[[-1.0, 1.0]] * len(nz))).T.reshape(-1, len(nz))
        p_eq = float(np.mean(np.abs(signs @ nz) >= obs_abs - 1e-12))
    else:
        signs = rng.choice([-1.0, 1.0], size=(20000, len(nz)))
        p_eq = float((1 + np.sum(np.abs(signs @ nz) >= obs_abs - 1e-12)) / 20001)

    return {
        "models": models,
        "n_forks_total": len(cases),
        "n_projects": P,
        "per_model_diff_pp": {m: round(float(obs_per_m[i]), 2) for i, m in enumerate(models)},
        "equal_model_weighted_diff_pp": round(obs_eq_m, 2),
        "equal_model_weighted_cluster_ci95": [round(float(lo), 2), round(float(hi), 2)],
        "p_cluster_signflip_equal_model": round(p_eq, 6),
    }


def summarize_forked_ablation_v2(
    ab_path: Path,
    k5_files: List[Path],
    samples_by_id: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Reconstructs ALL eligible `r_0 < 5` triggers directly from `k5_files` merged with `v2_forked_ablation_results.json`:
      - Guarantees 100% enrolment completeness (`n = 110`) for primary `A3_TrueWitness vs A1_ScalarRatio`
        even if `A2_MisleadingLocation` hit an API cap on `3` `deepseek-v4-pro` cases!
      - Reports `A2` honestly on completed `n = 107` cases with explicit missing-case log.
      - Computes ALL THREE pre-registered analyses: pooled, per-model, AND equal-model-weighted cluster bootstrap.
      - Reports exact character-length differences between `A2` and `A3` and discloses main-trajectory cache reuse.
    """
    ab_existing: Dict[str, Dict[str, Any]] = {}
    zero_r5_existing: List[Dict[str, Any]] = []
    if ab_path.exists():
        ab = json.loads(ab_path.read_text(encoding="utf-8"))
        zero_r5_existing = ab.get("zero_budget_r5_triggers", [])
        for c in ab.get("cases", []):
            ab_existing[f"{c['model']}::{c['sample_id']}"] = c

    all_cases: List[Dict[str, Any]] = []
    recovered_missing_a2: List[str] = []
    zero_budget_r5: List[Dict[str, Any]] = []

    for fp in k5_files:
        if not fp.exists():
            continue
        mj = json.loads(fp.read_text(encoding="utf-8"))
        model = mj["model"]
        for r in mj.get("results", []):
            sid = r["sample_id"]
            smp = samples_by_id.get(sid)
            if not smp or not smp.get("v2_eligible", True):
                continue
            ours = r["strategies"]["Ours_MuGuard_Flake"]
            b4 = r["strategies"]["B4_Scalar_Gate_Loop"]
            r0 = None
            for rd in ours["rounds"]:
                if rd["decision"] == "EROSION_RETRY":
                    r0 = rd["round"]
                    break
                if rd["round"] == 5 and rd["decision"] == "ABSTAIN_EROSION":
                    zero_budget_r5.append({"model": model, "sample_id": sid, "trigger_round": 5})
            if r0 is None or r0 >= 5:
                continue
            key = f"{model}::{sid}"
            if key in ab_existing:
                all_cases.append(ab_existing[key])
            else:
                # Reconstruct exact A1 (B4) and A3 (Ours) post-fork outcomes when A2 hit API cap!
                recovered_missing_a2.append(key)
                b4_post = [rd for rd in b4["rounds"] if rd["round"] > r0]
                ours_post = [rd for rd in ours["rounds"] if rd["round"] > r0]
                all_cases.append({
                    "model": model,
                    "sample_id": sid,
                    "project_name": r["project_name"],
                    "language": r["language"],
                    "trigger_round": r0,
                    "remaining_budget": 5 - r0,
                    "a2_missing_reason": "API_REASONING_TOKEN_CAP_ON_A2_BRANCH",
                    "A1_ScalarRatio": {
                        "recovered": bool(b4["sound_fix"]),
                        "post_fork_rounds_used": len(b4_post),
                        "initial_feedback_meta": {"shown_count": 0, "char_len": None, "approx_tokens": None},
                    },
                    "A2_MisleadingLocation": None,
                    "A3_TrueWitness": {
                        "recovered": bool(ours["sound_fix"]),
                        "post_fork_rounds_used": len(ours_post),
                        "initial_feedback_meta": {"shown_count": None, "char_len": None, "approx_tokens": None},
                    },
                })

    if not all_cases:
        return {}

    cases_with_a2 = [c for c in all_cases if c.get("A2_MisleadingLocation") is not None]
    distinct_tests = len({c["sample_id"] for c in all_cases})
    distinct_projs = len({c["project_name"] for c in all_cases})
    trig_dist = Counter(f"R{c['trigger_round']}" for c in all_cases)

    arms_out: Dict[str, Any] = {}
    for arm in ("A1_ScalarRatio", "A2_MisleadingLocation", "A3_TrueWitness"):
        sub_c = cases_with_a2 if arm == "A2_MisleadingLocation" else all_cases
        rate, ci = cluster_rate_ci([(c["project_name"], int(c[arm]["recovered"])) for c in sub_c])
        succ_rounds = [c[arm]["post_fork_rounds_used"] for c in sub_c if c[arm]["recovered"]]
        toks = [c[arm]["initial_feedback_meta"]["approx_tokens"] for c in sub_c if c[arm]["initial_feedback_meta"].get("approx_tokens") is not None]
        chars = [c[arm]["initial_feedback_meta"]["char_len"] for c in sub_c if c[arm]["initial_feedback_meta"].get("char_len") is not None]
        shown = [c[arm]["initial_feedback_meta"]["shown_count"] for c in sub_c if c[arm]["initial_feedback_meta"].get("shown_count") is not None]
        arms_out[arm] = {
            "recovered_count": sum(1 for c in sub_c if c[arm]["recovered"]),
            "total_forks": len(sub_c),
            "missing_forks": len(all_cases) - len(sub_c),
            "recovery_pct": rate,
            "cluster_ci95": ci,
            "mean_post_fork_rounds_among_recovered": round(statistics.mean(succ_rounds), 2) if succ_rounds else None,
            "mean_char_len": round(statistics.mean(chars), 1) if chars else 0.0,
            "mean_approx_feedback_tokens": round(statistics.mean(toks), 1) if toks else 0.0,
            "mean_shown_mutants": round(statistics.mean(shown), 2) if shown else 0.0,
        }

    # Exact A2 vs A3 character-length difference audit (transparent disclosure!)
    abs_char_diffs = [
        abs(c["A3_TrueWitness"]["initial_feedback_meta"]["char_len"] - c["A2_MisleadingLocation"]["initial_feedback_meta"]["char_len"])
        for c in cases_with_a2
        if c["A3_TrueWitness"]["initial_feedback_meta"].get("char_len") is not None
        and c["A2_MisleadingLocation"]["initial_feedback_meta"].get("char_len") is not None
    ]
    a2_a3_char_audit = {
        "n_paired": len(abs_char_diffs),
        "median_abs_char_diff": round(float(np.median(abs_char_diffs)), 1) if abs_char_diffs else 0.0,
        "p90_abs_char_diff": round(float(np.percentile(abs_char_diffs, 90)), 1) if abs_char_diffs else 0.0,
        "max_abs_char_diff": int(max(abs_char_diffs)) if abs_char_diffs else 0,
        "item_count_exact_match_pct": round(
            100.0 * sum(
                1 for c in cases_with_a2
                if c["A3_TrueWitness"]["initial_feedback_meta"]["shown_count"] == c["A2_MisleadingLocation"]["initial_feedback_meta"]["shown_count"]
            ) / max(1, len(cases_with_a2)),
            2,
        ),
        "disclosure": (
            "A2 and A3 match exact displayed mutant item count (100% matched) and template structure; "
            "character lengths vary by focal source-slice length (not per-instance character-padded), "
            "token counts are character-ratio approximations (`chars/3.8`), and A1/A3 reuse cached "
            "main-campaign trajectories (B4 and Ours) sharing verified identical pre-fork prefixes."
        ),
    }

    recs_110 = [
        {
            "project": c["project_name"],
            "A1": int(c["A1_ScalarRatio"]["recovered"]),
            "A3": int(c["A3_TrueWitness"]["recovered"]),
        }
        for c in all_cases
    ]
    recs_107 = [
        {
            "project": c["project_name"],
            "A1": int(c["A1_ScalarRatio"]["recovered"]),
            "A2": int(c["A2_MisleadingLocation"]["recovered"]),
            "A3": int(c["A3_TrueWitness"]["recovered"]),
        }
        for c in cases_with_a2
    ]
    primary_a3_vs_a1 = paired_project_cluster_inference(recs_110, "A3", "A1")
    equal_model_a3_vs_a1 = equal_model_weighted_cluster_bootstrap(all_cases)
    secondary_a3_vs_a2 = paired_project_cluster_inference(recs_107, "A3", "A2")

    pen_a1 = [c["A1_ScalarRatio"]["post_fork_rounds_used"] if c["A1_ScalarRatio"]["recovered"] else (c["remaining_budget"] + 1) for c in all_cases]
    pen_a3 = [c["A3_TrueWitness"]["post_fork_rounds_used"] if c["A3_TrueWitness"]["recovered"] else (c["remaining_budget"] + 1) for c in all_cases]
    diffs = [x - y for x, y in zip(pen_a1, pen_a3)]
    if any(d != 0 for d in diffs):
        w_res = sp_stats.wilcoxon(pen_a1, pen_a3, zero_method="wilcox", alternative="two-sided")
        wilcox_p = round(float(w_res.pvalue), 6)
    else:
        wilcox_p = 1.0

    # Per-model pre-registered cluster inference
    by_model = {}
    for m in sorted({c["model"] for c in all_cases}):
        sub = [c for c in all_cases if c["model"] == m]
        sub_a2 = [c for c in sub if c.get("A2_MisleadingLocation") is not None]
        m_recs = [{"project": c["project_name"], "A1": int(c["A1_ScalarRatio"]["recovered"]), "A3": int(c["A3_TrueWitness"]["recovered"])} for c in sub]
        m_inf = paired_project_cluster_inference(m_recs, "A3", "A1")
        by_model[m] = {
            "n_forks": len(sub),
            "n_forks_with_A2": len(sub_a2),
            "A1_recovered": sum(1 for c in sub if c["A1_ScalarRatio"]["recovered"]),
            "A2_recovered": sum(1 for c in sub_a2 if c["A2_MisleadingLocation"]["recovered"]),
            "A3_recovered": sum(1 for c in sub if c["A3_TrueWitness"]["recovered"]),
            "A3_vs_A1_cluster_inference": m_inf,
        }

    return {
        "n_forks": len(all_cases),
        "n_forks_with_A2": len(cases_with_a2),
        "a2_missing_cases": recovered_missing_a2,
        "distinct_tests": distinct_tests,
        "distinct_projects": distinct_projs,
        "trigger_round_distribution": dict(trig_dist),
        "zero_budget_r5_triggers_count": len(zero_budget_r5) or len(zero_r5_existing),
        "arms": arms_out,
        "a2_vs_a3_length_and_execution_audit": a2_a3_char_audit,
        "primary_mechanism_A3_vs_A1": primary_a3_vs_a1,
        "equal_model_weighted_A3_vs_A1": equal_model_a3_vs_a1,
        "budget_penalized_wilcoxon_p_A3_vs_A1_ref": wilcox_p,
        "secondary_stress_A3_vs_A2": secondary_a3_vs_a2,
        "by_model": by_model,
    }


def main() -> None:
    bench_p = DATA_DIR / "v2_frozen_benchmark_light.json"
    bench = json.loads(bench_p.read_text(encoding="utf-8"))
    samples = bench["samples"]
    samples_by_id = {s["sample_id"]: s for s in samples}
    elig_samples = [s for s in samples if s.get("v2_eligible", True)]

    ho_p = DATA_DIR / "v2_heldout_validation.json"
    heldout_map = json.loads(ho_p.read_text(encoding="utf-8")).get("evaluations", {}) if ho_p.exists() else {}

    k5_files = [
        DATA_DIR / f"v2_results_{safe_model_tag(m)}.json"
        for m in ("deepseek-flash", "deepseek-v4-pro", "qwen3.7-plus")
    ]
    k1_files = [
        DATA_DIR / f"v2_round1_{safe_model_tag(m)}.json"
        for m in ("kimi-k2.5", "glm-5")
    ]

    # Build unique eroded patch records for Top-k sensitivity (deduplicated by `(sample_id, candidate_fp)`)
    eroded_patch_map: Dict[str, Dict[str, Any]] = {}
    for fp in k5_files:
        if not fp.exists():
            continue
        d = json.loads(fp.read_text(encoding="utf-8"))
        for r in d.get("results", []):
            sid = r["sample_id"]
            smp = samples_by_id.get(sid)
            if not smp or not smp.get("v2_eligible", True):
                continue
            # Map legacy empty-index Java mutant IDs (from v2.0.0-frozen traces under PIT 1.9.11/1.15.2)
            # to their exact v2.2 lossless role-tagged IDs for offline Top-k sensitivity accounting.
            exact_pool_ids = (
                {f"target::{m['id']}" for m in smp["m_det"]}
                | {f"partner::{m['id']}" for m in (smp.get("polluter_m_det") or [])}
            )
            legacy_to_exact: Dict[str, List[str]] = defaultdict(list)
            for role_lbl, mlist in (("target", smp["m_det"]), ("partner", smp.get("polluter_m_det") or [])):
                for m in mlist:
                    if smp["language"] == "java":
                        leg_id = (
                            f"{role_lbl}::{m['mutatedClass']}::{m['mutatedMethod']}"
                            f"{m.get('methodDescription', '')}:L{m['lineNumber']}:"
                            f"{m['mutator'].split('.')[-1]}::{m['description']}"
                        )
                        legacy_to_exact[leg_id].append(f"{role_lbl}::{m['id']}")

            for st_rec in r["strategies"].values():
                for rd in st_rec["rounds"]:
                    ev = rd.get("eval")
                    if ev and ev.get("w_plus_passed") and ev.get("eroded"):
                        cfp = rd.get("candidate_fp") or candidate_fingerprint(rd["candidate"])
                        pk = f"{sid}::{cfp}"
                        if pk not in eroded_patch_map:
                            resolved_surv: List[str] = []
                            for raw_rid in (ev.get("survived_role_ids") or []):
                                if raw_rid in exact_pool_ids:
                                    resolved_surv.append(raw_rid)
                                elif raw_rid in legacy_to_exact:
                                    # Map legacy empty-index trace ID to its corresponding v2.2 pool ID(s)
                                    resolved_surv.append(legacy_to_exact[raw_rid][0])
                                else:
                                    resolved_surv.append(raw_rid)
                            eroded_patch_map[pk] = {
                                "patch_key": pk,
                                "sample_id": sid,
                                "target_m_det": smp["m_det"],
                                "partner_m_det": smp.get("polluter_m_det") or [],
                                "survived_role_ids": resolved_surv,
                            }

    eroded_records = list(eroded_patch_map.values())
    topk_joint = compute_v2_topk_sensitivity(eroded_records, include_partner_in_pool=True)
    topk_target_only = compute_v2_topk_sensitivity(eroded_records, include_partner_in_pool=False)

    k5_out = {}
    for fp in k5_files:
        if fp.exists():
            mj = json.loads(fp.read_text(encoding="utf-8"))
            k5_out[mj["model"]] = summarize_model_v2(mj, samples_by_id, heldout_map, is_k5=True)

    k1_out = {}
    for fp in k1_files:
        if fp.exists():
            mj = json.loads(fp.read_text(encoding="utf-8"))
            k1_out[mj["model"]] = summarize_model_v2(mj, samples_by_id, {}, is_k5=False)

    pr_adj = build_pr_adjudication_and_false_rejection(samples)
    tax = build_taxonomy_codebook(samples_by_id, k5_files)
    lat_abs = analyze_latencies_and_abstentions(samples, k5_files)
    ab_sum = summarize_forked_ablation_v2(DATA_DIR / "v2_forked_ablation_results.json", k5_files, samples_by_id)
    mext_adj_p = DATA_DIR / "mext_loss_adjudication_input.json"
    mext_adj_sum = (
        json.loads(mext_adj_p.read_text(encoding="utf-8")).get("symmetric_summary_by_strategy", {})
        if mext_adj_p.exists() else {}
    )

    # Benchmark summary (including exact per-subtype breakdown for Table 1)
    j_elig = [s for s in elig_samples if s["language"] == "java"]
    p_elig = [s for s in elig_samples if s["language"] == "python"]
    m_counts = [s["m_det_count"] for s in elig_samples]
    p_counts = [s.get("polluter_m_det_count", 0) for s in elig_samples if s["test_type"] == "OD"]
    ext_counts = [s.get("m_ext_total_count", 0) for s in elig_samples]

    def _sub_prof(sub_list: List[Dict[str, Any]]) -> Dict[str, Any]:
        return {
            "repos": len({s["project_name"] for s in sub_list}),
            "tests": len(sub_list),
            "target_m_det": sum(s["m_det_count"] for s in sub_list),
            "partner_m_det": sum(s.get("polluter_m_det_count", 0) for s in sub_list),
            "m_ext_total": sum(s.get("m_ext_total_count", 0) for s in sub_list),
            "m_ext_tests": sum(1 for s in sub_list if s.get("m_ext_total_count", 0) > 0),
            "probe_med": round(statistics.median([s["pit_median_sec"] for s in sub_list]), 2) if sub_list else 0.0,
        }

    subtypes_prof = {
        "java_id": _sub_prof([s for s in j_elig if s["test_type"] == "ID"]),
        "java_od_victim": _sub_prof([s for s in j_elig if s["test_type"] == "OD"]),
        "java_all": _sub_prof(j_elig),
        "python_od_victim": _sub_prof([s for s in p_elig if s.get("od_subtype") == "OD-victim"]),
        "python_od_brittle": _sub_prof([s for s in p_elig if s.get("od_subtype") == "OD-brittle"]),
        "python_all": _sub_prof(p_elig),
    }

    summary: Dict[str, Any] = {
        "harness_version": HARNESS_VERSION,
        "exec_benchmark_sha256": bench["exec_benchmark_sha256"],
        "upstream_repeated_controls_summary": (
            (lambda ctrl_p: (
                (lambda cd: {
                    "total_od_controls": len(cd),
                    "total_valid_controls": sum(1 for v in cd.values() if not v.get("is_unknown")),
                    "total_unknown_controls": sum(1 for v in cd.values() if v.get("is_unknown")),
                    "python_od_controls": sum(1 for v in cd.values() if v.get("language") == "python"),
                    "python_valid_controls": sum(1 for v in cd.values() if v.get("language") == "python" and not v.get("is_unknown")),
                    "python_unknown_controls": sum(1 for v in cd.values() if v.get("language") == "python" and v.get("is_unknown")),
                    "python_upstream_p2_nonidempotent_count": sum(1 for v in cd.values() if v.get("language") == "python" and (not v.get("is_unknown")) and v.get("upstream_nonidempotent_p2_call_fail_only")),
                    "python_upstream_p2_nonidempotent_repos": len({v["project_name"] for v in cd.values() if v.get("language") == "python" and (not v.get("is_unknown")) and v.get("upstream_nonidempotent_p2_call_fail_only")}),
                    "java_od_controls": sum(1 for v in cd.values() if v.get("language") == "java"),
                    "java_order_exact_controls": sum(1 for v in cd.values() if v.get("language") == "java" and v.get("order_exact")),
                    "java_phase_level_known_controls": sum(1 for v in cd.values() if v.get("language") == "java" and v.get("phase_level_known")),
                    "java_valid_controls": sum(1 for v in cd.values() if v.get("language") == "java" and not v.get("is_unknown")),
                    "java_unknown_controls": sum(1 for v in cd.values() if v.get("language") == "java" and v.get("is_unknown")),
                    "java_upstream_p2_nonidempotent_count": sum(1 for v in cd.values() if v.get("language") == "java" and (not v.get("is_unknown")) and v.get("upstream_nonidempotent_p2_call_fail_only")),
                    "java_upstream_p2_nonidempotent_repos": len({v["project_name"] for v in cd.values() if v.get("language") == "java" and (not v.get("is_unknown")) and v.get("upstream_nonidempotent_p2_call_fail_only")}),
                })(json.loads(ctrl_p.read_text(encoding="utf-8")))
                if ctrl_p.exists() else {}
            ))(DATA_DIR / "v23_upstream_repeated_pollution_controls.json")
        ),
        "benchmark": {
            "initial_attempted_count": bench["initial_attempted_count"],
            "eligible_count": len(elig_samples),
            "attrition_samples": bench.get("attrition_samples", []),
            "subtypes_profile": subtypes_prof,
            "java_eligible": len(j_elig),
            "python_eligible": len(p_elig),
            "java_id": sum(1 for s in j_elig if s["test_type"] == "ID"),
            "java_od_victim": sum(1 for s in j_elig if s["test_type"] == "OD"),
            "python_od_victim": sum(1 for s in p_elig if s.get("od_subtype") == "OD-victim"),
            "python_od_brittle": sum(1 for s in p_elig if s.get("od_subtype") == "OD-brittle"),
            "distinct_projects": len({s["project_name"] for s in elig_samples}),
            "java_projects": len({s["project_name"] for s in j_elig}),
            "python_projects": len({s["project_name"] for s in p_elig}),
            "total_target_m_det": sum(m_counts),
            "median_target_m_det": statistics.median(m_counts) if m_counts else 0,
            "od_total": len(p_counts),
            "od_with_partner_obligations": sum(1 for c in p_counts if c > 0),
            "od_zero_partner_obligations": sum(1 for c in p_counts if c == 0),
            "total_partner_m_det": sum(p_counts),
            "samples_with_m_ext": sum(1 for c in ext_counts if c > 0),
            "total_m_ext_obligations": sum(ext_counts),
            "java_pit_versions": dict(Counter(f"{s['pit_pair'][0]}/{s['pit_pair'][1]}" for s in j_elig)),
        },
        "pr_adjudication_and_false_rejection": pr_adj,
        "mext_loss_symmetric_adjudication": mext_adj_sum,
        "b1_authoritative_audit": summarize_b1_v2(samples),
        "k5_models": k5_out,
        "k1_models": k1_out,
        "forked_ablation": ab_sum,
        "taxonomy_dual_axis": tax,
        "topk_offline_sensitivity": {
            "n_unique_eroded_patches": len(eroded_records),
            "joint_pool": topk_joint,
            "target_only_pool": topk_target_only,
        },
        "latency_and_abstentions": lat_abs,
    }

    figs = generate_v2_figures(summary)
    summary["generated_figures"] = figs
    summary["security_audit"] = run_security_and_isolation_audit()

    out_p = DATA_DIR / "v2_study_summary_metrics.json"
    out_p.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[OK] Saved {out_p} | figs={figs} | leaks={summary['security_audit']['secret_leaks_found']}")


if __name__ == "__main__":
    main()
