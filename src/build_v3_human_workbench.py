"""
FT-1 v3.1 Ultra-Clear Interactive Human Dual-Rater Blinded Audit Workbench & Excel Generator.

Builds two zero-install, completely offline, standalone HTML audit workbenches:
  - `human_audit_pack/Rater_A_Blind_Audit_Workbench.html`
  - `human_audit_pack/Rater_B_Blind_Audit_Workbench.html`
PLUS two formatted Excel `.xlsx` workbooks with dropdown validation:
  - `human_audit_pack/Rater_A_Audit_Sheet.xlsx`
  - `human_audit_pack/Rater_B_Audit_Sheet.xlsx`
PLUS crisp Chinese guides `human_audit_pack/开始盲审前必读.md` and `human_audit_pack/ANNOTATION_GUIDE.md`
PLUS the private 4-module mapping key `data/v31_human_audit_key.json` (leaving `v3_human_audit_key.json` untouched).

Canonical Code Alignment (`H1` & `H2` verified):
  - `PR_53` 5-class codes match Stage-1 & 10-session Codex (`CONTRACT_PRESERVING_FIX`, `LEGITIMATE_RELAXATION`,
    `TEST_RESPONSIBILITY_SHIFT`, `GENUINE_ORACLE_WEAKENING`, `INSUFFICIENT_EVIDENCE`), with complete header-stripped
    PR file-tree unified diffs (`build_full_pr_unified_diff`) so all 10 shared-fixture/@Before PRs are 100% visible.
  - `TAX_60` 7-class mechanism codes match Table 5 & 10-session Codex (`M1_PredicateProjectionOrRelaxation` ..
    `M7_PreconditionOrSingletonSetupAlteration`).
  - `HELDOUT` (111) + `SOTA_53` (53) use canonical `W / S / L` codes.
"""
import difflib
import json
import random
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

FT1_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(FT1_ROOT / "src"))
from run_full_study_matrix import candidate_fingerprint
from v3_llm_judge_baseline import build_full_pr_unified_diff

DATA_DIR = FT1_ROOT / "data"
PACK_DIR = FT1_ROOT / "human_audit_pack"
V31_KEY_PATH = DATA_DIR / "v31_human_audit_key.json"

MUTATOR_CN = {
    "NegateConditionalsMutator": "把条件判断反过来写了（例如 == 改成 !=、< 改成 >=）",
    "ConditionalsBoundaryMutator": "把大小边界偏移了一格（例如 < 改成 <=、> 改成 >=）",
    "VoidMethodCallMutator": "直接删掉了这行无返回值的生产方法调用",
    "NonVoidMethodCallMutator": "删掉了这行带返回值的方法调用（改塞默认空值）",
    "ArgumentPropagationMutator": "跳过函数内部逻辑，直接把入参原样当返回值传出",
    "EmptyObjectReturnValsMutator": "把方法返回值硬改成空字符串/空集合/Optional.empty",
    "NullReturnValsMutator": "把方法返回值硬改成 null",
    "BooleanTrueReturnValsMutator": "把 boolean 返回值硬改成恒为 true",
    "BooleanFalseReturnValsMutator": "把 boolean 返回值硬改成恒为 false",
    "PrimitiveReturnsMutator": "把数值返回值硬改成 0",
    "MathMutator": "篡改了算术运算符（例如 + 改成 -、* 改成 /）",
    "IncrementsMutator": "把自增/自减写反了（++ 改成 --）",
    "RemoveConditionalMutator_EQUAL_IF": "把 if 相等判断硬改成恒真（永远不走 else）",
    "RemoveConditionalMutator_EQUAL_ELSE": "把 if 相等判断硬改成恒假（永远跳过 if 块）",
    "RemoveConditionalMutator_ORDER_IF": "把大小比较判断硬改成恒真",
    "RemoveConditionalMutator_ORDER_ELSE": "把大小比较判断硬改成恒假",
    "ConstructorCallMutator": "把 new 对象构造替换成 null",
    "MemberVariableMutator": "删掉了成员变量的赋值语句",
    "InlineConstantMutator": "篡改了代码里的常量数值",
    "ReturnValsMutator": "篡改了方法的返回值",
    "ComparisonNegation": "把比较运算符反过来了（例如 == 改成 !=、in 改成 not in）",
    "BooleanSwap": "把布尔值或逻辑与或反过来了（True↔False 或 and↔or）",
    "ArithmeticSwap": "篡改了算术加减乘除运算符",
    "ConstantFlip": "篡改了代码里的常量值",
    "NoneOrEmptyReturn": "把函数返回值硬改成 None 或空容器",
    "AugAssignSwap": "篡改了复合赋值（例如 += 改成 -=）",
    "UnaryOpMutator": "删掉或反转了一元取反符号（not / -）",
    "DictEntryDrop": "删掉了字典里的某一项键值对",
    "SliceBoundMutator": "把列表切片的起止下标改偏了",
    "DropCallArgMutator": "删掉了函数调用里的一个参数",
}

ASSERT_RX = re.compile(
    r"(\bassert[A-Za-z0-9_]*\b|\bfail\s*\(|\bverify\s*\(|\bpytest\.raises\b|\bself\.assert|\bexpect\s*\(|\.should)",
)


def explain_mutator_cn(mut_name: str, desc: str = "") -> str:
    short = (mut_name or "").split(".")[-1]
    if short in MUTATOR_CN:
        return MUTATOR_CN[short]
    for k, v in MUTATOR_CN.items():
        if k.lower() in (short + " " + desc).lower():
            return v
    low = (desc or "").lower()
    if "removed call" in low:
        return "直接删掉了这行生产方法调用"
    if "negated" in low:
        return "把这行的条件或比较判断反过来写了"
    if "replaced" in low and "return" in low:
        return "把这行的方法返回值篡改成了假值/空值"
    if "argument" in low:
        return "跳过函数处理，直接拿入参冒充返回值"
    if "boundary" in low:
        return "修改了循环或比较的边界条件"
    return "故意改坏了这行生产逻辑（原测试本来能报错抓住它，打补丁后没报错放过了）"


def sanitize_pr_tree_diff(raw_diff: str) -> str:
    """Keep only pure `diff --git a/... b/...` hunks and scrub any PR URL / issue reference if present."""
    if not raw_diff:
        return ""
    idx = raw_diff.find("diff --git ")
    clean = raw_diff[idx:].strip() if idx >= 0 else raw_diff.strip()
    clean = re.sub(r"https?://github\.com/\S+", "[redacted-url]", clean)
    return clean


def make_unified_diff(orig: str, patched: str, label: str = "test") -> str:
    if not orig and not patched:
        return ""
    diff = list(
        difflib.unified_diff(
            (orig or "").splitlines(),
            (patched or "").splitlines(),
            fromfile=f"修改前/{label}",
            tofile=f"打补丁后/{label}",
            lineterm="",
            n=4,
        )
    )
    return "\n".join(diff) if diff else "(目标测试方法体本身无文本改动；请查看下方完整文件/搭档测试改动 Diff)"


def extract_objective_digest(
    orig_t: str,
    patch_t: str,
    p_changed: bool,
    prod_files: List[str],
    build_files: List[str],
    full_pr_diff: str = "",
    pr_wplus_status: Optional[bool] = None,
) -> Dict[str, Any]:
    diff_lines = list(difflib.unified_diff((orig_t or "").splitlines(), (patch_t or "").splitlines(), lineterm="", n=0))
    del_cnt = sum(1 for l in diff_lines if l.startswith("-") and not l.startswith("---"))
    add_cnt = sum(1 for l in diff_lines if l.startswith("+") and not l.startswith("+++"))

    tree_del = tree_add = 0
    if full_pr_diff:
        for l in full_pr_diff.splitlines():
            if l.startswith("-") and not l.startswith("---"):
                tree_del += 1
            elif l.startswith("+") and not l.startswith("+++"):
                tree_add += 1

    orig_asserts = [l.strip() for l in (orig_t or "").splitlines() if ASSERT_RX.search(l) and not l.strip().startswith(("//", "#"))]
    patch_asserts = [l.strip() for l in (patch_t or "").splitlines() if ASSERT_RX.search(l) and not l.strip().startswith(("//", "#"))]
    assert_unchanged = (orig_asserts == patch_asserts)

    return {
        "del_lines": del_cnt,
        "add_lines": add_cnt,
        "tree_del_lines": tree_del,
        "tree_add_lines": tree_add,
        "method_unchanged": (del_cnt == 0 and add_cnt == 0),
        "partner_edited": p_changed,
        "prod_cnt": len(prod_files),
        "build_cnt": len(build_files),
        "pr_wplus_status": pr_wplus_status,
        "assert_unchanged": assert_unchanged,
        "orig_asserts": orig_asserts[:8],
        "patch_asserts": patch_asserts[:8],
    }


def normalize_mutant_item(m: Any, mdet_slice_map: Dict[Any, str], tag: str = "") -> Dict[str, str]:
    if isinstance(m, dict):
        cls = (m.get("mutatedClass") or "").split(".")[-1]
        meth = m.get("mutatedMethod") or ""
        ln = str(m.get("lineNumber") or m.get("line") or "")
        mut = (m.get("mutator_short") or m.get("mutator") or "").split(".")[-1]
        desc = m.get("description") or m.get("desc") or ""
        mid = m.get("exact_id") or m.get("id") or ""
        if not cls and "::" in mid:
            head = mid.split("::", 1)[0]
            cls = head.split(".")[-1]
            rest = mid.split("::", 1)[1]
            meth = rest.split("(", 1)[0].split(":", 1)[0]
        sl = m.get("source_slice") or mdet_slice_map.get(mid) or mdet_slice_map.get((cls, ln)) or ""
        loc = f"{cls}.{meth} (第 {ln} 行)" if (cls and ln) else (f"{cls}.{meth}" if cls else (mid[:75] or f"第 {ln} 行"))
        return {
            "tag": tag or m.get("role", ""),
            "location": loc,
            "mutator": mut,
            "desc": desc,
            "cn_hint": explain_mutator_cn(mut, desc),
            "source_slice": sl[:750],
        }
    s = str(m)
    m_ln = re.search(r":L(\d+)", s)
    ln = m_ln.group(1) if m_ln else ""
    loc = s[:90]
    sl = ""
    for k, sl_txt in mdet_slice_map.items():
        if isinstance(k, tuple) and len(k) == 2 and k[1] == ln and k[0] in s:
            sl = sl_txt
            break
    return {
        "tag": tag,
        "location": loc,
        "mutator": "",
        "desc": s[90:240] if len(s) > 90 else "",
        "cn_hint": explain_mutator_cn(s, s),
        "source_slice": sl[:750],
    }


def build_slice_map(sample: Dict[str, Any]) -> Dict[Any, str]:
    mp: Dict[Any, str] = {}
    for pool_name in ("m_det", "polluter_m_det", "m_ext_op_value_flow", "m_ext_op_call", "m_ext_operator", "m_ext_instance"):
        for m in (sample.get(pool_name) or []):
            if not isinstance(m, dict):
                continue
            sl = m.get("source_slice") or ""
            if not sl:
                continue
            for k in (m.get("exact_id"), m.get("id")):
                if k:
                    mp[k] = sl
            cls = (m.get("mutatedClass") or "").split(".")[-1]
            ln = str(m.get("lineNumber") or m.get("line") or "")
            if cls and ln:
                mp[(cls, ln)] = sl
    return mp


def build_extended_candidate_lookup(bm_by_id: Dict[str, Dict[str, Any]]) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    from build_v3_human_audit_pack import build_candidate_lookup

    by_cand_key, by_unique_key = build_candidate_lookup(bm_by_id)

    def _register(sid: str, cand: Dict[str, Any], fp_override: str = "") -> None:
        if not cand or not isinstance(cand, dict):
            return
        bm = bm_by_id.get(sid, {})
        cand_t = cand.get("test_code") or cand.get("victim_code") or ""
        cand_p = cand.get("polluter_code") or ""
        if not cand_t and not cand_p:
            return
        fp = fp_override or candidate_fingerprint(cand)
        ck = f"{sid}::{fp}"
        if ck not in by_cand_key or not by_cand_key[ck].get("cand_test_code"):
            by_cand_key[ck] = {
                "sample_id": sid,
                "language": bm.get("language", ""),
                "od_subtype": bm.get("od_subtype") or bm.get("test_type", ""),
                "test_full_name": bm.get("test_full_name", sid),
                "orig_test_code": bm.get("orig_test_code", ""),
                "cand_test_code": cand_t,
                "orig_partner_code": bm.get("orig_polluter_code", ""),
                "cand_partner_code": cand_p,
                "imports": cand.get("imports") or [],
                "survived_summaries": [],
                "scope_kind": "",
            }

    for fn in ("v3_b5_coverage_k5_qwen3.7-plus.json", "v3_b6_llm_judge_k5_qwen3.7-plus.json"):
        p = DATA_DIR / fn
        if p.exists():
            res = json.loads(p.read_text(encoding="utf-8")).get("results", {})
            items = res.values() if isinstance(res, dict) else res
            for r in items:
                sid = r["sample_id"]
                if r.get("final_candidate"):
                    _register(sid, r["final_candidate"], r.get("final_candidate_fp") or "")
                for rd in (r.get("rounds") or []):
                    if rd.get("candidate"):
                        _register(sid, rd["candidate"], rd.get("candidate_fp") or "")

    gap_p = DATA_DIR / "v3_completed_missing_k5.json"
    if gap_p.exists():
        for m_dict in json.loads(gap_p.read_text(encoding="utf-8")).get("by_model", {}).values():
            for sid, r in m_dict.items():
                for st in (r.get("strategies") or {}).values():
                    if st.get("final_candidate"):
                        _register(sid, st["final_candidate"])
                    for rd in (st.get("rounds") or []):
                        if rd.get("candidate"):
                            _register(sid, rd["candidate"], rd.get("candidate_fp") or "")

    sota_p = DATA_DIR / "v31_sota_qwen3.7-plus.json"
    if sota_p.exists():
        for r in json.loads(sota_p.read_text(encoding="utf-8")).get("results", []):
            sid = r["sample_id"]
            for st in (r.get("strategies") or {}).values():
                if st.get("final_candidate"):
                    _register(sid, st["final_candidate"], st.get("final_candidate_fp") or "")
                for rd in (st.get("rounds") or []):
                    if rd.get("candidate"):
                        _register(sid, rd["candidate"], rd.get("candidate_fp") or "")

    return by_cand_key, by_unique_key


def select_stratified_core_ids(cases: List[Dict[str, Any]], target_n: int, rng: random.Random) -> Set[str]:
    if len(cases) <= target_n:
        return {c["blind_id"] for c in cases}
    buckets: Dict[str, List[str]] = {}
    for c in cases:
        if c["task"] == "PR_53":
            key = "lost_pos" if (isinstance(c.get("lost_mutants_count"), int) and c["lost_mutants_count"] > 0) else "lost_zero_or_na"
        else:
            key = f"{c['language']}::{c.get('flaky_type', '')}"
        buckets.setdefault(key, []).append(c["blind_id"])
    for b in buckets.values():
        rng.shuffle(b)

    picked: List[str] = []
    keys = sorted(buckets.keys())
    total = len(cases)
    quotas = {k: max(1, round(target_n * len(buckets[k]) / total)) for k in keys}
    for k in keys:
        take = min(len(buckets[k]), quotas[k])
        picked.extend(buckets[k][:take])
        buckets[k] = buckets[k][take:]
    if len(picked) > target_n:
        rng.shuffle(picked)
        picked = picked[:target_n]
    elif len(picked) < target_n:
        rem = [bid for k in keys for bid in buckets[k]]
        rng.shuffle(rem)
        picked.extend(rem[: target_n - len(picked)])
    return set(picked)


def build_all_blinded_cases() -> Tuple[Dict[str, List[Dict[str, Any]]], Dict[str, Any]]:
    bm_by_id = {s["sample_id"]: s for s in json.loads((DATA_DIR / "v2_frozen_benchmark_light.json").read_text(encoding="utf-8"))["samples"]}
    hkey_v3 = json.loads((DATA_DIR / "v3_human_audit_key.json").read_text(encoding="utf-8"))
    pr_man = {e["sample_id"]: e for e in json.loads((DATA_DIR / "pr_reference_manifest.json").read_text(encoding="utf-8"))["entries"]}
    pr_diff_cache = json.loads((DATA_DIR / "v3_pr_diff_cache.json").read_text(encoding="utf-8"))
    tax_cb = {e["unique_patch_key"]: e for e in json.loads((DATA_DIR / "taxonomy_audit_codebook.json").read_text(encoding="utf-8"))["entries"]}

    ho_evals: Dict[str, Any] = {}
    for fn in ("v2_heldout_validation.json", "v3_b5_b6_heldout_validation.json", "v3_completed_heldout.json", "v31_sota_heldout_validation.json"):
        p = DATA_DIR / fn
        if p.exists():
            ho_evals.update(json.loads(p.read_text(encoding="utf-8")).get("evaluations", {}))

    by_cand_key, by_unique_key = build_extended_candidate_lookup(bm_by_id)

    # 1. PR_53 (with full header-stripped PR file-tree unified diff — fixes H2 completely!)
    pr_cases: List[Dict[str, Any]] = []
    for bid, meta in sorted(hkey_v3["pr_53"].items()):
        sid = meta["sample_id"]
        s = bm_by_id[sid]
        e = pr_man[sid]
        sl_map = build_slice_map(s)
        orig_t = s.get("orig_test_code") or ""
        app_t = e.get("applied_victim_code") or ""
        orig_p = s.get("orig_polluter_code") or ""
        app_p = e.get("applied_polluter_code") or ""
        p_changed = bool(orig_p and app_p and orig_p.strip() != app_p.strip())
        full_tree_diff = sanitize_pr_tree_diff(build_full_pr_unified_diff(s, e, pr_diff_cache, max_chars=24000))
        assert full_tree_diff, f"Missing full PR tree diff for {bid} ({sid})"

        method_unchanged = (orig_t.strip() == app_t.strip())
        primary_diff = (
            f"# [说明] 目标 @Test 方法体本身未改，该 PR 修改了测试类的 @Before/@After 夹具、import 或共享辅助文件，完整文件树 Diff 如下：\n{full_tree_diff}"
            if method_unchanged
            else make_unified_diff(orig_t, app_t, "target_test.java")
        )

        dev_ev = s.get("v2_dev_pr_eval") or {}
        raw_muts = dev_ev.get("lost_mutants") or dev_ev.get("survived_mutants") or dev_ev.get("survived_mutant_summaries") or []
        muts_fmt = [normalize_mutant_item(m, sl_map, "基准Bug") for m in raw_muts[:8]]

        prod_files = e.get("prod_files_in_pr") or []
        build_files = e.get("build_files_in_pr") or []
        test_files = e.get("test_files_in_pr") or []
        lost_cnt = e.get("lost_mutant_count_3x")
        is_test_only_wplus = (meta.get("pr_scope_type") == "TEST_ONLY_PR") and (meta.get("w_plus_passed") is True)
        mut_badge = (
            f"漏抓生产Bug: {lost_cnt} 个" if (lost_cnt is not None and lost_cnt > 0)
            else ("0 漏抓（原基准Bug全能抓到）" if lost_cnt == 0 else "未进入变异体比对（未作为纯测试稳态通过）")
        )
        pr_cases.append({
            "blind_id": bid,
            "task": "PR_53",
            "language": "java",
            "flaky_type": s.get("od_subtype") or s.get("test_type", ""),
            "test_name": s.get("test_full_name", sid),
            "project": s.get("project_name", ""),
            "scope_summary": f"改测试文件 {len(test_files)} 个 | 改生产文件 {len(prod_files)} 个 | 改构建配置 {len(build_files)} 个",
            "prod_files": prod_files,
            "build_files": build_files,
            "mut_badge": mut_badge,
            "lost_mutants_count": "" if lost_cnt is None else lost_cnt,
            "digest": extract_objective_digest(orig_t, app_t, p_changed, prod_files, build_files, full_tree_diff, is_test_only_wplus),
            "orig_target": orig_t,
            "patched_target": app_t,
            "target_diff": primary_diff,
            "full_pr_tree_diff": "" if method_unchanged else full_tree_diff,
            "orig_partner": orig_p if p_changed else "",
            "patched_partner": app_p if p_changed else "",
            "partner_diff": make_unified_diff(orig_p, app_p, "partner_test.java") if p_changed else "",
            "context_header": (s.get("context_header") or "")[:2200],
            "lost_mutants": muts_fmt,
        })

    # 2. TAX_60
    tax_cases: List[Dict[str, Any]] = []
    for bid, meta in sorted(hkey_v3["tax_60"].items()):
        uk = meta["unique_patch_key"]
        sid = meta["sample_id"]
        s = bm_by_id[sid]
        sl_map = build_slice_map(s)
        tail_fp = uk.split("::")[-1]
        rec = by_unique_key.get(uk) or by_cand_key.get(f"{sid}::{tail_fp}") or {}
        cb_e = tax_cb.get(uk) or {}
        lang = rec.get("language") or s.get("language", "java")
        ext = "java" if lang == "java" else "py"
        orig_t = rec.get("orig_test_code") or s.get("orig_test_code") or ""
        cand_t = rec.get("cand_test_code") or ""
        orig_p = rec.get("orig_partner_code") or s.get("orig_polluter_code") or ""
        cand_p = rec.get("cand_partner_code") or ""
        p_changed = bool(orig_p and cand_p and orig_p.strip() != cand_p.strip())
        raw_muts = rec.get("survived_summaries") or cb_e.get("survived_summaries") or []
        muts_fmt = [normalize_mutant_item(m, sl_map, "基准Bug") for m in raw_muts[:8]]

        tk = rec.get("target_m_det_killed")
        tt = rec.get("target_m_det_total")
        pk = rec.get("partner_m_det_killed")
        pt = rec.get("partner_m_det_total")
        mut_badge = f"目标测试抓Bug数: {tk}/{tt}"
        if pt and pt > 0 and pk is not None:
            mut_badge += f" | 搭档测试抓Bug数: {pk}/{pt}"

        tax_cases.append({
            "blind_id": bid,
            "task": "TAX_60",
            "language": lang,
            "flaky_type": rec.get("od_subtype") or s.get("od_subtype") or s.get("test_type", ""),
            "test_name": rec.get("test_full_name") or s.get("test_full_name", sid),
            "project": s.get("project_name", ""),
            "scope_summary": f"改动范围: {'同时改了搭档测试/共享区' if p_changed else '仅改目标测试'}",
            "prod_files": [],
            "build_files": [],
            "mut_badge": mut_badge,
            "digest": extract_objective_digest(orig_t, cand_t, p_changed, [], []),
            "orig_target": orig_t,
            "patched_target": cand_t,
            "target_diff": make_unified_diff(orig_t, cand_t, f"target.{ext}"),
            "full_pr_tree_diff": "",
            "orig_partner": orig_p if p_changed else "",
            "patched_partner": cand_p if p_changed else "",
            "partner_diff": make_unified_diff(orig_p, cand_p, f"partner.{ext}") if p_changed else "",
            "context_header": (s.get("context_header") or "")[:2200],
            "lost_mutants": muts_fmt,
        })

    def _build_ho_case(bid: str, ck: str, sid: str, task_name: str) -> Dict[str, Any]:
        s = bm_by_id[sid]
        sl_map = build_slice_map(s)
        rec = by_cand_key.get(ck) or {}
        ev = ho_evals.get(ck) or {}
        lang = rec.get("language") or s.get("language", "java")
        ext = "java" if lang == "java" else "py"
        orig_t = rec.get("orig_test_code") or s.get("orig_test_code") or ""
        cand_t = rec.get("cand_test_code") or ""
        assert cand_t, f"Missing candidate test code for {bid} ({ck})"
        orig_p = rec.get("orig_partner_code") or s.get("orig_polluter_code") or ""
        cand_p = rec.get("cand_partner_code") or ""
        p_changed = bool(orig_p and cand_p and orig_p.strip() != cand_p.strip())

        raw_muts: List[Any] = []
        for field, tag in (
            ("lost_value_flow_mutants", "隐藏题-返回值/条件"),
            ("lost_call_mutants", "隐藏题-方法调用"),
            ("lost_op_mutants", "隐藏题-未见算子"),
            ("lost_inst_mutants", "隐藏题-未见代码行"),
        ):
            for item in (ev.get(field) or []):
                raw_muts.append((item, tag))
        muts_fmt = [normalize_mutant_item(m, sl_map, tag) for m, tag in raw_muts[:10]]
        hk = ev.get("m_ext_all_killed", "?")
        ht = ev.get("m_ext_all_total", "?")
        lost_n = (ht - hk) if (isinstance(hk, int) and isinstance(ht, int)) else len(muts_fmt)
        return {
            "blind_id": bid,
            "task": task_name,
            "language": lang,
            "flaky_type": s.get("od_subtype") or s.get("test_type", ""),
            "test_name": s.get("test_full_name", sid),
            "project": s.get("project_name", ""),
            "scope_summary": f"搭档测试(Partner)是否被改: {'是' if p_changed else '否'}",
            "prod_files": [],
            "build_files": [],
            "mut_badge": f"隐藏Bug抓获: {hk}/{ht}（漏抓了 {lost_n} 个隐藏生产Bug）",
            "m_ext_killed_of_total": f"{hk}/{ht}",
            "digest": extract_objective_digest(orig_t, cand_t, p_changed, [], []),
            "orig_target": orig_t,
            "patched_target": cand_t,
            "target_diff": make_unified_diff(orig_t, cand_t, f"target.{ext}"),
            "full_pr_tree_diff": "",
            "orig_partner": orig_p if p_changed else "",
            "patched_partner": cand_p if p_changed else "",
            "partner_diff": make_unified_diff(orig_p, cand_p, f"partner.{ext}") if p_changed else "",
            "context_header": (s.get("context_header") or "")[:2200],
            "lost_mutants": muts_fmt,
        }

    # 3. HELDOUT (111 main multi-LLM held-out losses)
    ho_cases = [_build_ho_case(bid, meta["cand_key"], meta["sample_id"], "HELDOUT") for bid, meta in sorted(hkey_v3["ho_all"].items())]

    # 4. SOTA_53 (7 Coverage/Judge extra + 46 v3.1 Same-Model SOTA new held-out losses)
    extra7 = json.loads((DATA_DIR / "v3_extra7_heldout_wsl_10sessions.json").read_text(encoding="utf-8"))
    extra46 = json.loads((DATA_DIR / "v31_sota_extra_wsl_10sessions.json").read_text(encoding="utf-8"))
    sota_key_sec: Dict[str, Any] = {}
    sota_cases: List[Dict[str, Any]] = []

    for i, ck in enumerate(sorted(extra7.keys()), 1):
        bid = f"HS-COV-{i:03d}"
        sid = extra7[ck].get("sample_id") or "::".join(ck.split("::")[:-1])
        sota_cases.append(_build_ho_case(bid, ck, sid, "SOTA_53"))
        sota_key_sec[bid] = {
            "cand_key": ck,
            "sample_id": sid,
            "language": bm_by_id[sid]["language"],
            "source_pool": "v3_extra7_coverage_or_judge",
        }

    for i, ck in enumerate(sorted(extra46.keys()), 1):
        bid = f"HS-SOTA-{i:03d}"
        sid = "::".join(ck.split("::")[:-1])
        sota_cases.append(_build_ho_case(bid, ck, sid, "SOTA_53"))
        sota_key_sec[bid] = {
            "cand_key": ck,
            "sample_id": sid,
            "language": bm_by_id[sid]["language"],
            "source_pool": "v31_same_model_sota_head2head",
        }

    assert len(sota_cases) == 53, f"Expected 53 SOTA/extra held-out cases, got {len(sota_cases)}"

    rng_core = random.Random(20261127)
    core_ids: Set[str] = set()
    for arr in (pr_cases, tax_cases, ho_cases, sota_cases):
        picked = select_stratified_core_ids(arr, 30, rng_core)
        core_ids |= picked
        for c in arr:
            c["is_core"] = c["blind_id"] in picked
    assert len(core_ids) == 120, f"Expected 120 core IDs, got {len(core_ids)}"

    v31_key = {
        "protocol_version": "3.1.1-human-dual-rater-blinded-277-canonical",
        "note": "PRIVATE MAPPING KEY — DO NOT SHARE WITH HUMAN RATERS BEFORE CODING COMPLETES.",
        "core_120_ids": sorted(core_ids),
        "pr_53": hkey_v3["pr_53"],
        "tax_60": hkey_v3["tax_60"],
        "ho_all": hkey_v3["ho_all"],
        "sota_53": sota_key_sec,
    }
    return {"PR_53": pr_cases, "TAX_60": tax_cases, "HELDOUT": ho_cases, "SOTA_53": sota_cases}, v31_key


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>FT-1 补丁双盲人工审议工作台 — __RATER_TITLE__</title>
<style>
:root {
  --bg: #f1f5f9; --card: #ffffff; --border: #cbd5e1; --text: #0f172a; --muted: #64748b;
  --primary: #2563eb; --primary-soft: #eff6ff; --green: #16a34a; --green-bg: #dcfce7;
  --red: #dc2626; --red-bg: #fee2e2; --amber: #d97706; --amber-bg: #fef3c7;
}
* { box-sizing: border-box; }
body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif; background: var(--bg); color: var(--text); }
header { background: #0f172a; color: #f8fafc; padding: 11px 20px; display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 10px; position: sticky; top: 0; z-index: 40; box-shadow: 0 2px 10px rgba(0,0,0,.18); }
header h1 { margin: 0; font-size: 16.5px; font-weight: 700; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.rater-pill { background: #2563eb; color: #fff; padding: 3px 10px; border-radius: 999px; font-size: 12px; }
.mode-switch { display: inline-flex; background: #1e293b; border: 1px solid #475569; border-radius: 8px; padding: 2px; gap: 2px; }
.mode-switch button { border: none; background: transparent; color: #cbd5e1; padding: 5px 10px; border-radius: 6px; font-size: 12px; font-weight: 700; cursor: pointer; }
.mode-switch button.active { background: #f59e0b; color: #0f172a; }
.top-actions { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.btn { border: 1px solid #cbd5e1; background: #fff; color: #1e293b; padding: 6px 12px; border-radius: 7px; cursor: pointer; font-size: 12.5px; font-weight: 700; transition: all .15s; }
.btn:hover { background: #f1f5f9; }
.btn-primary { background: var(--primary); color: #fff; border-color: var(--primary); }
.btn-primary:hover { background: #1d4ed8; }
.btn-success { background: #16a34a; color: #fff; border-color: #16a34a; box-shadow: 0 1px 4px rgba(22,163,74,.35); }
.btn-success:hover { background: #15803d; }
.btn-dark { background: #334155; color: #f8fafc; border-color: #475569; }
.btn-dark:hover { background: #475569; }

.bar { background: #fff; border-bottom: 1px solid var(--border); padding: 9px 20px; display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 10px; }
.tabs { display: flex; gap: 6px; flex-wrap: wrap; }
.tab { padding: 7px 12px; border-radius: 8px; border: 1px solid var(--border); background: #f8fafc; cursor: pointer; font-size: 12.5px; font-weight: 700; color: #334155; }
.tab.active { background: var(--primary); color: #fff; border-color: var(--primary); }
.tab.tab-intro { border-color: #f59e0b; background: #fffbeb; color: #92400e; }
.tab.tab-intro.active { background: #d97706; color: #fff; border-color: #d97706; }
.tab .cnt { opacity: .92; font-weight: 600; margin-left: 4px; font-size: 11.5px; }

.intro-wrap { max-width: 1180px; margin: 18px auto 40px; padding: 0 20px; }
.hero-card { background: #fff; border: 1px solid var(--border); border-radius: 14px; padding: 22px 26px; box-shadow: 0 2px 8px rgba(0,0,0,.04); margin-bottom: 16px; }
.hero-card h2 { margin: 0 0 8px; font-size: 20px; color: #0f172a; }
.hero-card p { margin: 6px 0; font-size: 14px; line-height: 1.7; color: #334155; }
.step3-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 14px; margin-top: 14px; }
.ex-card { background: #fff; border-radius: 12px; padding: 16px; border: 2px solid #e2e8f0; }
.ex-card h3 { margin: 0 0 6px; font-size: 15px; }
.ex-card p { font-size: 13px; line-height: 1.6; color: #334155; margin: 6px 0; }
.mini-diff { font-family: ui-monospace, Consolas, monospace; font-size: 12px; border-radius: 7px; overflow: hidden; border: 1px solid #cbd5e1; margin: 8px 0; }
.mini-diff div { padding: 3px 8px; white-space: pre-wrap; }
.task-table { width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 13.5px; }
.task-table th, .task-table td { border: 1px solid #e2e8f0; padding: 9px 12px; text-align: left; vertical-align: top; }
.task-table th { background: #f8fafc; font-weight: 700; }

.role-banner { max-width: 1740px; margin: 12px auto 0; padding: 0 20px; }
.role-inner { background: linear-gradient(90deg, #eff6ff 0%, #f8fafc 100%); border: 1px solid #bfdbfe; border-left: 5px solid #2563eb; border-radius: 10px; padding: 10px 16px; display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 10px; font-size: 13.5px; line-height: 1.55; }

.workspace { display: grid; grid-template-columns: minmax(0, 1.5fr) minmax(380px, 1fr); gap: 16px; padding: 12px 20px 32px; max-width: 1740px; margin: 0 auto; }
@media (max-width: 1120px) { .workspace { grid-template-columns: 1fr; } }

.panel { background: var(--card); border: 1px solid var(--border); border-radius: 12px; box-shadow: 0 1px 3px rgba(0,0,0,.04); overflow: hidden; }
.panel-head { padding: 11px 16px; border-bottom: 1px solid var(--border); background: #f8fafc; display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 8px; }
.panel-body { padding: 14px 16px; }

.meta-badges { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.badge { padding: 3px 9px; border-radius: 6px; font-size: 12px; font-weight: 700; background: #e2e8f0; color: #334155; }
.badge-id { background: #0f172a; color: #fff; font-family: ui-monospace, Consolas, monospace; font-size: 13px; }
.badge-core { background: #fef3c7; color: #b45309; border: 1px solid #fcd34d; }
.badge-java { background: #ffedd5; color: #9a3412; }
.badge-py { background: #dbeafe; color: #1e40af; }
.badge-mut { background: #fee2e2; color: #991b1b; }
.badge-mut-ok { background: #dcfce7; color: #166534; }
.badge-done { background: var(--green-bg); color: #166534; }
.badge-todo { background: #f1f5f9; color: #64748b; }

.test-title { font-family: ui-monospace, Consolas, monospace; font-size: 12.5px; color: #0f172a; background: #f1f5f9; padding: 6px 10px; border-radius: 6px; word-break: break-all; margin-bottom: 10px; }

.digest-card { background: #f8fafc; border: 1px solid #cbd5e1; border-radius: 10px; padding: 10px 12px; margin-bottom: 12px; font-size: 12.5px; }
.digest-title { font-weight: 700; color: #0f172a; margin-bottom: 6px; display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.assert-cmp { margin-top: 6px; border: 1px solid #e2e8f0; border-radius: 7px; overflow: hidden; font-family: ui-monospace, Consolas, monospace; font-size: 11.8px; }
.assert-old { background: #fef2f2; color: #7f1d1d; padding: 4px 8px; border-bottom: 1px dashed #fecaca; white-space: pre-wrap; word-break: break-all; }
.assert-new { background: #f0fdf4; color: #14532d; padding: 4px 8px; white-space: pre-wrap; word-break: break-all; }
.assert-same { background: #eff6ff; color: #1e40af; padding: 6px 10px; border-radius: 6px; font-weight: 600; margin-top: 4px; }
.wplus-warn { background: #fef2f2; color: #991b1b; border: 1px solid #fecaca; padding: 6px 10px; border-radius: 6px; font-weight: 700; margin-bottom: 6px; }
.wplus-ok { background: #f0fdf4; color: #166534; border: 1px solid #bbf7d0; padding: 5px 10px; border-radius: 6px; font-weight: 700; margin-bottom: 6px; }

.view-toggle { display: flex; gap: 6px; margin-bottom: 8px; align-items: center; }
.view-toggle button { font-size: 12px; padding: 5px 11px; border-radius: 6px; border: 1px solid var(--border); background: #fff; cursor: pointer; font-weight: 700; }
.view-toggle button.active { background: #0f172a; color: #fff; border-color: #0f172a; }

.diff-box { font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: 12.5px; line-height: 1.52; border: 1px solid #cbd5e1; border-radius: 8px; overflow-x: auto; background: #fff; max-height: 460px; overflow-y: auto; }
.diff-line { padding: 1px 10px; white-space: pre; min-height: 19px; }
.diff-add { background: #dcfce7; color: #14532d; }
.diff-del { background: #fee2e2; color: #7f1d1d; }
.diff-hunk { background: #eff6ff; color: #1d4ed8; font-weight: 700; }
.diff-hdr { background: #f1f5f9; color: #475569; font-weight: 700; }

.sbs-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
@media (max-width: 850px) { .sbs-grid { grid-template-columns: 1fr; } }
.code-pane { border: 1px solid #cbd5e1; border-radius: 8px; overflow: hidden; }
.code-pane-hdr { background: #f1f5f9; padding: 5px 10px; font-size: 12px; font-weight: 700; border-bottom: 1px solid #cbd5e1; }
.code-pane pre { margin: 0; padding: 8px 10px; font-family: ui-monospace, Consolas, monospace; font-size: 12px; line-height: 1.48; overflow: auto; max-height: 410px; background: #fff; }

.section-sub { margin: 14px 0 6px; font-size: 13px; font-weight: 700; color: #0f172a; }
.mut-explain-bar { font-size: 12px; color: #7c2d12; background: #ffedd5; border: 1px solid #fed7aa; padding: 6px 10px; border-radius: 7px; margin-bottom: 8px; }
.mut-card { border: 1px solid #fde68a; background: #fffdf5; border-radius: 8px; padding: 8px 10px; margin-bottom: 8px; font-size: 12.5px; }
.mut-loc { font-family: ui-monospace, Consolas, monospace; font-weight: 700; color: #92400e; word-break: break-all; }
.mut-cn { color: #b91c1c; font-weight: 700; margin-top: 3px; }
.mut-desc { color: #475569; font-size: 11.5px; margin-top: 2px; }
.mut-slice { margin: 6px 0 0; padding: 6px 8px; background: #0f172a; color: #e2e8f0; border-radius: 6px; font-family: ui-monospace, Consolas, monospace; font-size: 11.5px; line-height: 1.45; overflow-x: auto; white-space: pre; }
.mut-slice .hl-line { background: #991b1b; color: #fff; display: block; padding: 1px 4px; margin: 0 -4px; font-weight: 700; }

.sticky-form { position: sticky; top: 64px; }
.wizard-box { background: #fffbeb; border: 1px solid #fcd34d; border-radius: 9px; padding: 9px 11px; margin-bottom: 12px; font-size: 12.5px; }
.wizard-box summary { cursor: pointer; font-weight: 700; color: #92400e; }
.wiz-step { margin-top: 8px; padding-top: 8px; border-top: 1px dashed #fde68a; }
.wiz-btns { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; }
.wiz-btn { font-size: 12px; padding: 5px 9px; border-radius: 6px; border: 1px solid #d97706; background: #fff; cursor: pointer; font-weight: 700; color: #92400e; }
.wiz-btn:hover { background: #fef3c7; }

.q-group { margin-bottom: 13px; }
.q-label { font-size: 13px; font-weight: 700; margin-bottom: 6px; display: block; color: #0f172a; }
.opt-list { display: flex; flex-direction: column; gap: 6px; }
.opt-row { display: grid; grid-template-columns: repeat(3, 1fr); gap: 7px; }
.opt-btn { text-align: left; padding: 9px 11px; border: 2px solid #e2e8f0; border-radius: 9px; background: #fff; cursor: pointer; transition: all .12s; }
.opt-btn:hover { border-color: #93c5fd; background: #f8fafc; }
.opt-btn.selected { border-color: var(--primary); background: var(--primary-soft); box-shadow: 0 0 0 2px rgba(37,99,235,.15); }
.opt-btn.sel-L.selected { border-color: #16a34a; background: #dcfce7; }
.opt-btn.sel-S.selected { border-color: #d97706; background: #fef3c7; }
.opt-btn.sel-W.selected { border-color: #dc2626; background: #fee2e2; }
.opt-title { font-size: 13px; font-weight: 700; color: #0f172a; display: flex; justify-content: space-between; align-items: center; gap: 6px; }
.opt-sub { font-size: 11.8px; color: #334155; margin-top: 3px; line-height: 1.4; }
.opt-code { font-family: ui-monospace, Consolas, monospace; font-size: 11px; padding: 1px 6px; border-radius: 4px; background: #f1f5f9; color: #475569; white-space: nowrap; }

.chips { display: flex; flex-wrap: wrap; gap: 5px; margin-bottom: 6px; }
.chip { font-size: 11.5px; padding: 3px 8px; border-radius: 999px; border: 1px solid #cbd5e1; background: #f8fafc; cursor: pointer; color: #334155; }
.chip:hover { background: #e2e8f0; }

textarea { width: 100%; min-height: 54px; padding: 7px 10px; border: 1px solid #cbd5e1; border-radius: 8px; font-family: inherit; font-size: 12.5px; resize: vertical; }
.nav-bar { display: flex; justify-content: space-between; align-items: center; gap: 8px; margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--border); }
.jump-strip { display: flex; flex-wrap: wrap; gap: 4px; max-height: 86px; overflow-y: auto; padding: 8px 16px; background: #f8fafc; border-top: 1px solid var(--border); }
.jdot { width: 27px; height: 22px; font-size: 11px; border-radius: 5px; border: 1px solid #cbd5e1; background: #fff; cursor: pointer; font-weight: 700; color: #475569; }
.jdot.done { background: #86efac; border-color: #22c55e; color: #14532d; }
.jdot.curr { background: #2563eb; border-color: #1d4ed8; color: #fff; }
.kbd { font-family: ui-monospace, Consolas, monospace; font-size: 11px; background: #e2e8f0; padding: 1px 5px; border-radius: 4px; color: #1e293b; font-weight: 700; }
.toast { position: fixed; bottom: 20px; right: 20px; background: #0f172a; color: #fff; padding: 10px 16px; border-radius: 8px; font-size: 13px; font-weight: 700; opacity: 0; pointer-events: none; transition: opacity .25s; z-index: 99; box-shadow: 0 4px 12px rgba(0,0,0,.25); }
.toast.show { opacity: 1; }
</style>
</head>
<body>

<header>
  <h1>
    <span>FT-1 双盲人工审议工作台</span>
    <span class="rater-pill">__RATER_TITLE__</span>
    <div class="mode-switch" title="两位标注员在核心模式下抽到的是完全相同的120道题（仅顺序独立打乱），标完120题即可直接算全模块双人一致性 κ！">
      <button id="modeCoreBtn" class="active" onclick="setPoolMode('core')">⚡ 核心必标集 (120题 · 约35分钟)</button>
      <button id="modeFullBtn" onclick="setPoolMode('full')">📚 全量完整集 (277题 · 全覆盖)</button>
    </div>
  </h1>
  <div class="top-actions">
    <span id="totalProgress" style="font-size:13px; font-weight:700; color:#fde68a;"></span>
    <button class="btn btn-dark" onclick="document.getElementById('importFile').click()">📂 导入已保存进度</button>
    <input type="file" id="importFile" accept=".json" style="display:none" onchange="importJSON(event)">
    <button class="btn btn-dark" onclick="exportAllCSVs()" title="可选：导出 CSV 表格">导出 CSV</button>
    <button class="btn btn-success" onclick="exportResultJSON()">💾 标完点此：一键下载审议结果 (.json)</button>
  </div>
</header>

<div class="bar">
  <div class="tabs">
    <button class="tab tab-intro" id="tab-INTRO" onclick="switchTask('INTRO')">📖 0. 先看我：1分钟看懂审什么</button>
    <button class="tab" id="tab-PR_53" onclick="switchTask('PR_53')">模块一：开源真实 PR <span class="cnt" id="cnt-PR_53"></span></button>
    <button class="tab" id="tab-TAX_60" onclick="switchTask('TAX_60')">模块二：侵蚀手法分类 <span class="cnt" id="cnt-TAX_60"></span></button>
    <button class="tab" id="tab-HELDOUT" onclick="switchTask('HELDOUT')">模块三：主实验隐藏题核查 <span class="cnt" id="cnt-HELDOUT"></span></button>
    <button class="tab" id="tab-SOTA_53" onclick="switchTask('SOTA_53')">模块四：SOTA对决隐藏题核查 <span class="cnt" id="cnt-SOTA_53"></span></button>
  </div>
  <div id="toolbarRight" style="display:flex; gap:12px; align-items:center; font-size:12.5px; flex-wrap:wrap;">
    <label style="cursor:pointer; font-weight:700; color:#1d4ed8;"><input type="checkbox" id="autoNextChk" checked onchange="savePrefs()"> ⚡ 点完选项自动跳下一题</label>
    <label style="cursor:pointer;"><input type="checkbox" id="onlyUnlabeled" onchange="render()"> 只看未标</label>
    <button class="btn" onclick="jumpNextUnlabeled()">下一未标 <span class="kbd">N</span></button>
  </div>
</div>

<!-- TAB 0: 1-MINUTE TUTORIAL -->
<div id="introView" class="intro-wrap" style="display:none;">
  <div class="hero-card">
    <h2>👋 欢迎！不用懂复杂项目背景，1 分钟看明白你要做什么</h2>
    <p><b>背景一句话：</b>Flaky Test（不稳定测试）是指有时跑绿、有时报错的测试（比如因为 HashMap 顺序不固定、或者上个测试留下了脏数据）。程序员或大模型会写一个<b>补丁（Patch）</b>让它变稳定。</p>
    <p><b>我们要抓什么问题？</b> 有些补丁是<b>真修好了根因</b>（比如把无序 JSON 排好序）；但有些补丁是<b>投机取巧“糊弄学”</b>——为了不报错，直接把 <code>assert</code> 断言删了、改宽松了，或者用假对象绕开了原本要测的代码！你的任务就是<b>看左边红删绿增的代码对比，点右边按钮给补丁定性</b>。</p>
    <p style="background:#eff6ff; border:1px solid #bfdbfe; padding:9px 12px; border-radius:8px; color:#1e3a8a;">
      💡 <b>怎么选模式？</b> 顶部默认开启 <b>「⚡ 核心必标集 (120题)」</b>（每个模块精选 30 题，A 和 B 抽到的是完全相同的 120 题，约 35 分钟标完即可算出整篇论文全部 4 个模块的双人一致性 Cohen's κ！）。如果精力充沛想把全篇 277 题全部标满，随时点顶部切换到 <b>「📚 全量完整集 (277题)」</b>，之前标的进度 100% 保留！<br>
      ⚠️ <b>唯一纪律：</b>标注员 A（打开 <code>Rater_A_...html</code>）和标注员 B（打开 <code>Rater_B_...html</code>）请<b>各自独立完成、中途不要商量</b>；全部标完后点右上角绿色按钮下载 1 个 <code>.json</code> 文件发回即可！
    </p>
  </div>

  <div class="hero-card">
    <h2>🎯 全场通用的核心三分类（L / S / W）—— 看这 3 个小例子秒懂！</h2>
    <p>几乎所有题目最后都归结为区分 <b>L（正常好补丁）</b>、<b>S（偷梁换柱/绕道）</b> 和 <b>W（断言放水）</b>。只看这 3 个例子就彻底懂了：</p>
    <div class="step3-grid">
      <div class="ex-card" style="border-color:#22c55e; background:#f0fdf4;">
        <h3 style="color:#15803d;">🟢 L · 正常修法（好补丁 / 没毛病）</h3>
        <p><b>特征：</b>只消除了乱序或残留脏状态，<b>原本要检查的业务字段和数值一点没少查！</b></p>
        <div class="mini-diff">
          <div class="diff-del">- String s = JSON.toJSONString(map);</div>
          <div class="diff-add">+ String s = JSON.toJSONString(map, SortField);</div>
          <div>  assertEquals("{\"a\":1,\"b\":2}", s);</div>
        </div>
        <p><b>为什么有时它也会“漏抓1个变异体”？</b> 比如排好序后不再经过旧的无序分支。只要原测试核心检查没缩水，坚定选 <b>L</b>！</p>
      </div>

      <div class="ex-card" style="border-color:#f59e0b; background:#fffbeb;">
        <h3 style="color:#b45309;">🟠 S · 偷梁换柱（换对象 / Mock绕过 / 砍空输入）</h3>
        <p><b>特征：</b>为了不报错，<b>不去测原来的真代码或深层分支了</b>，绕道逃跑了！</p>
        <div class="mini-diff">
          <div class="diff-del">- Client c = RealFactory.DEFAULT.create();</div>
          <div class="diff-add">+ Client c = Mockito.mock(Client.class);</div>
          <div>  assertNotNull(c);</div>
        </div>
        <p>常见套路：① 用 Mock/Stub 假对象替掉真方法；② 不测序列化字符串改测内存对象；③ 把原本 3 个元素的测试输入砍成空集合/单元素，导致深层代码根本没执行；④ 清空全局单例或把搭档测试改坏了。选 <b>S</b>！</p>
      </div>

      <div class="ex-card" style="border-color:#ef4444; background:#fef2f2;">
        <h3 style="color:#b91c1c;">🔴 W · 断言放水（删断言 / 精确改模糊）</h3>
        <p><b>特征：</b>还在调原来的函数，但<b>把检查标准降级或删掉了</b>，错的结果也能混过去！</p>
        <div class="mini-diff">
          <div class="diff-del">- assertEquals("a,b,c", result);</div>
          <div class="diff-add">+ assertTrue(result.contains("a"));</div>
        </div>
        <p>常见套路：① 直接删掉 <code>assertEquals</code>；② 精确相等改成 <code>contains</code> / <code>isNotNull</code> / 只查长度不查内容；③ 用 <code>split(",")</code> 吞掉末尾空字符串。选 <b>W</b>！</p>
      </div>
    </div>
  </div>

  <div class="hero-card">
    <h2>📋 四个模块分别在审什么？（一张表看清）</h2>
    <table class="task-table">
      <thead>
        <tr><th>模块名称</th><th>核心集 / 全量题数</th><th>左边给你看的是什么补丁？</th><th>右边你只需要点什么？</th></tr>
      </thead>
      <tbody>
        <tr>
          <td><b>模块一：开源真实 PR</b></td>
          <td>核心 30 题 / 全量 53 题</td>
          <td>GitHub 开源程序员提交的真实 PR（含方法体及完整测试类 <code>@Before</code> 夹具 Diff）</td>
          <td>5 选 1：若顶部红条提示「未作为纯测试独立稳态通过」直接选第 5 项；否则判断是<b>契约完整保持 (1) / 合法规范化放宽 (2)</b>，还是<b>偷梁换柱 (3) / 断言放水 (4)</b></td>
        </tr>
        <tr>
          <td><b>模块二：侵蚀手法分类</b></td>
          <td>核心 30 题 / 全量 60 题</td>
          <td>已经被机器实锤“漏掉了原本必抓 Bug”的补丁</td>
          <td>先选<b>它用了哪种手法漏掉 Bug（M1–M7 七选一，与论文表 5 完全对齐）</b>，再点一下 <b>L / S / W</b></td>
        </tr>
        <tr>
          <td><b>模块三：主实验隐藏题核查</b></td>
          <td>核心 30 题 / 全量 111 题</td>
          <td>主实验中通过了门禁、但在<b>隐藏测试题</b>上少抓了 Bug 的补丁</td>
          <td>只点 1 下：<b>L（正常修法被冤枉）</b>、<b>S（偷梁换柱）</b> 或 <b>W（断言放水）</b></td>
        </tr>
        <tr>
          <td><b>模块四：SOTA对决隐藏题核查</b></td>
          <td>核心 30 题 / 全量 53 题</td>
          <td>同底座 SOTA 正面对决中在<b>隐藏测试题</b>上少抓了 Bug 的补丁</td>
          <td>与模块三完全相同！只点 1 下：<b>L / S / W</b></td>
        </tr>
      </tbody>
    </table>
    <div style="margin-top:18px; text-align:center;">
      <button class="btn btn-primary" style="font-size:15px; padding:11px 28px; border-radius:10px;" onclick="startFirstTask()">🚀 我彻底看懂了！开始第 1 题 →</button>
    </div>
  </div>
</div>

<!-- MAIN AUDIT VIEW -->
<div id="auditView">
  <div class="role-banner">
    <div class="role-inner" id="roleBannerText"></div>
  </div>

  <div class="workspace">
    <!-- LEFT: CODE DIFF, DIGEST & MUTANTS -->
    <div class="panel">
      <div class="panel-head">
        <div class="meta-badges" id="metaBadges"></div>
        <div style="font-size:12.5px; font-weight:700; color:var(--muted);" id="caseIndexText"></div>
      </div>
      <div class="panel-body">
        <div class="test-title" id="testName"></div>

        <!-- Objective Zero-Leak Digest -->
        <div class="digest-card" id="digestCard"></div>

        <div class="view-toggle">
          <button id="btnViewDiff" class="active" onclick="setDiffMode('diff')">🔍 红删绿增对比（推荐，一眼看改了哪行）</button>
          <button id="btnViewSbs" onclick="setDiffMode('sbs')">📖 修改前 vs. 修改后目标方法全文</button>
        </div>
        <div id="codeArea"></div>

        <div id="partnerSection" style="display:none;">
          <div class="section-sub">🔗 搭档测试（Partner Test，和目标测试互相影响状态的另一个测试）改动对比</div>
          <div id="partnerDiffArea"></div>
        </div>

        <details id="fullPrDiffWrap" style="display:none; margin-top:12px;" open>
          <summary style="cursor:pointer; font-size:13px; font-weight:700; color:#1d4ed8;">📂 完整 PR 测试文件树 Unified Diff（含 @Before/@After 共享夹具、import 与辅助类全部改动）</summary>
          <div id="fullPrDiffArea" style="margin-top:6px;"></div>
        </details>

        <div class="section-sub">🧬 机器实测报告：打补丁后漏抓的生产代码 Bug（辅助你判断为什么漏抓）</div>
        <div class="mut-explain-bar">
          💡 <b>怎么看下面这块？</b> 机器故意在生产代码标红那一行埋了个错，<b>修改前的原测试能报错抓住它，但打完上方补丁后测试居然没报错放过了它！</b>你可以看一眼：这是因为补丁正常排好序不再走那条旧分支（选 <b>L</b>），还是因为补丁绕开了调用/清空了夹具（选 <b>S</b>），或是把断言改水了（选 <b>W</b>）？
        </div>
        <div id="mutantsArea"></div>

        <details style="margin-top:12px;">
          <summary style="cursor:pointer; font-size:12.5px; font-weight:700; color:#475569;">📄 需要看原测试类成员变量或原始 @Before？点此展开测试类头部上下文</summary>
          <pre id="ctxHeader" style="margin:6px 0 0; padding:10px; background:#f8fafc; border:1px solid #e2e8f0; border-radius:8px; font-size:11.5px; overflow:auto; max-height:260px;"></pre>
        </details>
      </div>
      <div class="jump-strip" id="jumpStrip"></div>
    </div>

    <!-- RIGHT: ANNOTATION PANEL -->
    <div class="panel sticky-form">
      <div class="panel-head">
        <strong style="font-size:14px;">📝 请在下方点选你的判断（实时自动保存）</strong>
        <span id="saveStateBadge" class="badge badge-todo">待标注</span>
      </div>
      <div class="panel-body" id="formArea"></div>
    </div>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
const RATER_ID = "__RATER_ID__";
const STORAGE_KEY = "ft1_v31_canonical_audit_" + RATER_ID;
const PREF_KEY = "ft1_v31_prefs_" + RATER_ID;
const DATA = __JSON_PAYLOAD__;
const TASK_ORDER = ["PR_53", "TAX_60", "HELDOUT", "SOTA_53"];

let currentTask = "INTRO";
let poolMode = "core";
let currentIdx = 0;
let diffMode = "diff";
let answers = {};

try {
  const saved = localStorage.getItem(STORAGE_KEY);
  if (saved) answers = JSON.parse(saved);
  const prefs = JSON.parse(localStorage.getItem(PREF_KEY) || "{}");
  if (prefs.poolMode) poolMode = prefs.poolMode;
  if (prefs.seenIntro) currentTask = "PR_53";
  if (prefs.autoNext === false) {
    window.addEventListener("DOMContentLoaded", () => { document.getElementById("autoNextChk").checked = false; });
  }
} catch (e) {}

const ROLE_BANNERS = {
  PR_53: "<b>【模块一 · 你在审开源程序员的真实 PR】</b> 若左侧提示「未作为纯测试独立稳态通过」直接选第 5 项；若「已稳态通过」，请判断他是<b>正经修好（选 1 契约完整保持 / 2 合法规范化放宽，均为 YES）</b>，还是<b>偷梁换柱 (3) / 断言放水 (4)</b>？",
  TAX_60: "<b>【模块二 · 你在给“漏抓 Bug 的补丁”分手法】</b> 左边补丁已被机器抓到漏掉了生产 Bug。请做两步：<b>① 先在 M1–M7 里选它是怎么改的</b>；<b>② 再在 L / S / W 里选严重程度</b>（开启自动跳题时，两项都选完才会跳下一题）。",
  HELDOUT: "<b>【模块三 · 主实验隐藏测试题人类终审】</b> 左边补丁在<b>隐藏测试题</b>上少抓了下方列出的生产 Bug。请一锤定音：它是<b>正常排序/清脏数据被冤枉了（选 L）</b>，还是<b>偷梁换柱绕道了（选 S）</b>，或是<b>断言放水降标了（选 W）</b>？",
  SOTA_53: "<b>【模块四 · 同底座 SOTA 对决隐藏测试题人类终审】</b> 规则与模块三<b>完全一样</b>！只需看左边红绿改动和下方漏抓的 Bug，点一下 <b>L（正常修法）</b>、<b>S（偷梁换柱）</b> 或 <b>W（断言放水）</b> 即可！"
};

// Canonical 5-class PR codes matching Stage-1 & 10-session Codex (`H1`)
const PR_OPTS = [
  { key: "1", code: "CONTRACT_PRESERVING_FIX", legit: "YES", cls: "sel-L", title: "1. 契约完整保持的正经修复 (CONTRACT_PRESERVING_FIX)", sub: "纯测试独立稳态通过，0 基准 Bug 漏抓（或等价结构校验如 JSONAssert），原有键值与结构检查 100% 保留" },
  { key: "2", code: "LEGITIMATE_RELAXATION", legit: "YES", cls: "sel-L", title: "2. 合法规范化 / 等价放宽 (LEGITIMATE_RELAXATION)", sub: "纯测试独立稳态通过，仅因合理排序/清理测试前污染导致不再走偶然旧分支而少杀附带变异体，核心业务检查未缩水" },
  { key: "3", code: "TEST_RESPONSIBILITY_SHIFT", legit: "NO", cls: "sel-S", title: "3. 偷梁换柱 / 测试责任转移 (TEST_RESPONSIBILITY_SHIFT)", sub: "换了被测对象、用 Mock 假对象绕过真代码、砍空前置输入夹具、清空全局单例注册表或改坏搭档测试" },
  { key: "4", code: "GENUINE_ORACLE_WEAKENING", legit: "NO", cls: "sel-W", title: "4. 断言放水 / 真实预言机弱化 (GENUINE_ORACLE_WEAKENING)", sub: "删掉了 assert，或把精确相等改成模糊包含(contains)/非空(!=null)/切分丢弃边界，导致错结果也能混过" },
  { key: "5", code: "INSUFFICIENT_EVIDENCE", legit: "NO", cls: "sel-W", title: "5. 非纯测试独立稳态 / 证据不足 (INSUFFICIENT_EVIDENCE)", sub: "当左侧提示「未作为纯测试独立稳态通过」（单独应用测试改动后未通过 W+ 稳态重跑）时选此项" },
];

// Canonical M1-M7 codes matching Table 5 & 10-session Codex (`H1`)
const TAX_MECH_OPTS = [
  { key: "1", code: "M1_PredicateProjectionOrRelaxation", title: "M1 · 断言放水 / 谓词降维 (Predicate Projection/Relaxation)", sub: "直接删掉 assert，或把精确相等改成 contains / isNotNull / 只查集合长度或键集而丢掉键值对应" },
  { key: "2", code: "M2_StateResetOrClobber", title: "M2 · 清空/覆盖了共享全局状态 (State Reset/Clobber)", sub: "在测试体或后置清理中清空了全局容器/单例/缓存（如 .clear()），使被测或搭档校验失效" },
  { key: "3", code: "M3_ControlFlowOrExceptionChange", title: "M3 · 控制流或异常捕获改变 (Control-Flow/Exception Shift)", sub: "改动了测试内的循环、条件判断或 try/catch 异常处理，跳过或吞掉了原本的验证分支" },
  { key: "4", code: "M4_SUTCallAlterationOrBypass", title: "M4 · 绕过或替换了被测生产方法 (Focal Call Bypass)", sub: "用 Mock/Stub/匿名假实现替掉了真实生产调用，或删改了对焦点生产方法（如 .toJSONString）的调用" },
  { key: "5", code: "M5_PartnerOrSharedScopeEdit", title: "M5 · 改动搭档测试(Partner)或共享夹具 (Partner/Shared Edit)", sub: "修改了搭档测试方法或类级共享 fixture/helper，导致搭档测试或共享作用域的检错义务丢失" },
  { key: "6", code: "M6_DelimiterOrFormatNormalization", title: "M6 · 字符串切分/格式转换吞掉边界 (Delimiter/Format Norm.)", sub: "如 split(',') 吞掉末尾空项、正则/JSON/SQL/空白清洗时顺手把边界格式错误也掩盖了" },
  { key: "7", code: "M7_PreconditionOrSingletonSetupAlteration", title: "M7 · 缩减/改动前置输入夹具或单例配置 (Precondition Shift)", sub: "把多元素输入砍成单元素/空壳，或改动前置构造参数/单例初始化，导致深层生产分支根本没跑到" },
];

const WSL_OPTS = [
  { key: "L", code: "L", cls: "sel-L", title: "🟢 L · 正常修法（没毛病）", sub: "只消除了乱序或残留脏数据，原测试该查的核心业务字段、数值和边界完全保留" },
  { key: "S", code: "S", cls: "sel-S", title: "🟠 S · 偷梁换柱（绕道/换目标/砍输入）", sub: "换了被测对象、Mock 绕过真代码、把输入砍成空壳跑不到原分支、清空全局单例或改坏搭档测试" },
  { key: "W", code: "W", cls: "sel-W", title: "🔴 W · 断言放水（删断言/降精度）", sub: "仍在调原方法，但删了 assert、精确相等改模糊包含/非空、或切分字符串丢了边界" },
];

const QUICK_CHIPS = [
  "仅引入确定性排序/清理污染状态，原核心检查完整保留",
  "未作为纯测试独立通过 W+ 稳态检验，归为证据不足",
  "将精确相等弱化为 contains/非空/只查长度，丢掉键值校验",
  "直接删掉了原有的 assert 断言或异常检查",
  "split/格式清洗吞掉了尾部空项或边界格式错误",
  "缩减了前置输入夹具（如单元素/空集合）或清空全局单例，深层分支未执行",
  "用 Mock/Stub 或替代调用绕过了原生产默认方法",
  "修改搭档测试(Partner)或共享夹具导致伴随义务失守",
];

function savePrefs() {
  try {
    const autoNextEl = document.getElementById("autoNextChk");
    localStorage.setItem(PREF_KEY, JSON.stringify({
      poolMode,
      seenIntro: true,
      autoNext: autoNextEl ? autoNextEl.checked : true
    }));
  } catch (e) {}
}

function showToast(msg) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.classList.add("show");
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove("show"), 2000);
}

function saveStore() {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(answers)); } catch (e) {}
}

function isComplete(task, bid) {
  const a = answers[bid];
  if (!a) return false;
  if (task === "PR_53") return !!(a.rater_label_5class && a.is_legitimate_repair_YES_NO);
  if (task === "TAX_60") return !!(a.primary_mechanism_M1toM7 && a.semantic_severity_W_S_L);
  if (task === "HELDOUT" || task === "SOTA_53") return !!(a.label_W_S_L);
  return false;
}

function getPoolForTask(t) {
  const raw = DATA[t] || [];
  return poolMode === "core" ? raw.filter(x => x.is_core) : raw;
}

function getVisibleList() {
  if (currentTask === "INTRO") return [];
  const list = getPoolForTask(currentTask);
  const onlyUn = document.getElementById("onlyUnlabeled").checked;
  return onlyUn ? list.filter(x => !isComplete(currentTask, x.blind_id)) : list;
}

function setPoolMode(m) {
  poolMode = m;
  currentIdx = 0;
  document.getElementById("modeCoreBtn").classList.toggle("active", m === "core");
  document.getElementById("modeFullBtn").classList.toggle("active", m === "full");
  savePrefs();
  render();
  showToast(m === "core" ? "已切到：核心必标集（120题，A/B抽样完全一致）" : "已切到：全量完整集（全部277题）");
}

function startFirstTask() {
  savePrefs();
  switchTask("PR_53");
}

function switchTask(t) {
  currentTask = t;
  currentIdx = 0;
  if (t !== "INTRO") savePrefs();
  document.querySelectorAll(".tab").forEach(el => el.classList.remove("active"));
  const tabEl = document.getElementById("tab-" + t);
  if (tabEl) tabEl.classList.add("active");
  render();
}

function setDiffMode(m) {
  diffMode = m;
  document.getElementById("btnViewDiff").classList.toggle("active", m === "diff");
  document.getElementById("btnViewSbs").classList.toggle("active", m === "sbs");
  render();
}

function escHtml(s) {
  return (s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function renderDiffHtml(diffText) {
  if (!diffText) return '<div style="padding:10px;color:#64748b;">(无差异)</div>';
  return '<div class="diff-box">' + diffText.split("\n").map(ln => {
    let cls = "diff-line";
    if (ln.startsWith("+++") || ln.startsWith("---") || ln.startsWith("diff --git") || ln.startsWith("# [说明]")) cls += " diff-hdr";
    else if (ln.startsWith("@@")) cls += " diff-hunk";
    else if (ln.startsWith("+")) cls += " diff-add";
    else if (ln.startsWith("-")) cls += " diff-del";
    return `<div class="${cls}">${escHtml(ln) || " "}</div>`;
  }).join("") + '</div>';
}

function renderSliceHtml(sl) {
  if (!sl) return "";
  return '<div class="mut-slice">' + sl.split("\n").map(ln => {
    if (ln.trimStart().startsWith(">>")) return `<span class="hl-line">${escHtml(ln)}   ◀ 故意埋错的这行代码</span>`;
    return escHtml(ln);
  }).join("\n") + '</div>';
}

function renderDigestHtml(item) {
  const d = item.digest || {};
  let wplusBanner = "";
  if (item.task === "PR_53") {
    if (d.pr_wplus_status === true) {
      wplusBanner = `<div class="wplus-ok">✅ 纯测试独立稳态检验 (W+)：<b>已通过</b>（请在右侧 1–4 项中评估补丁的语义质量）</div>`;
    } else {
      wplusBanner = `<div class="wplus-warn">⚠️ 纯测试独立稳态检验 (W+)：<b>未通过</b>（测试单独应用后未稳态通过；按标注规范通常直接选右侧<b>第 5 项 INSUFFICIENT_EVIDENCE</b>）</div>`;
    }
  }
  let topBadges = `<span>🔍 <b>本题改动速览：</b>目标方法删 <b>${d.del_lines || 0}</b> 行 / 增 <b>${d.add_lines || 0}</b> 行</span>`;
  if (d.tree_del_lines || d.tree_add_lines) {
    topBadges += `<span>（整个 PR 文件树共删 <b>${d.tree_del_lines}</b> 行 / 增 <b>${d.tree_add_lines}</b> 行）</span>`;
  }
  if (d.method_unchanged && item.task === "PR_53") {
    topBadges += `<span style="color:#1d4ed8;font-weight:700;">| 📂 目标 @Test 方法体未动，下方已直接展示完整测试类/@Before 夹具改动！</span>`;
  }
  if (d.partner_edited) topBadges += `<span style="color:#b45309;font-weight:700;">| ⚠️ 同时修改了下方搭档测试(Partner)！</span>`;

  let assertHtml = "";
  if (d.assert_unchanged) {
    assertHtml = `<div class="assert-same">💡 机器比对提示：目标方法的 <code>assert</code> 语句本身<b>一字未改</b>！请重点看下方代码里的「@Before 夹具 / 输入数据准备 / 方法调用与 Mock / 排序或清理状态 / 搭档测试」是怎么改的。</div>`;
  } else {
    const oldA = (d.orig_asserts && d.orig_asserts.length) ? d.orig_asserts.map(x => "- 原断言: " + x).join("\n") : "- 原断言: (未检出显式 assert 语句或委托给辅助函数)";
    const newA = (d.patch_asserts && d.patch_asserts.length) ? d.patch_asserts.map(x => "+ 新断言: " + x).join("\n") : "+ 新断言: (断言语句被删空或移入其他结构！)";
    assertHtml = `<div style="margin-top:5px;font-weight:700;color:#334155;">🎯 断言语句（Assert）前后直接对比：</div>
      <div class="assert-cmp">
        <div class="assert-old">${escHtml(oldA)}</div>
        <div class="assert-new">${escHtml(newA)}</div>
      </div>`;
  }
  return `${wplusBanner}<div class="digest-title">${topBadges}</div>${assertHtml}`;
}

function setField(bid, field, val, extraFields) {
  const wasDone = isComplete(currentTask, bid);
  if (!answers[bid]) answers[bid] = { confidence_1to3: "3", rationale_1_sentence: "" };
  answers[bid][field] = val;
  if (extraFields) Object.assign(answers[bid], extraFields);
  saveStore();
  const nowDone = isComplete(currentTask, bid);
  renderFormOnly(getCurrentItem());
  updateCounters();

  const autoNext = document.getElementById("autoNextChk") && document.getElementById("autoNextChk").checked;
  const isPrimaryField = (field === "rater_label_5class" || field === "semantic_severity_W_S_L" || field === "primary_mechanism_M1toM7" || field === "label_W_S_L");
  if (autoNext && isPrimaryField && nowDone && (!wasDone || currentTask !== "TAX_60")) {
    setTimeout(() => {
      const list = getVisibleList();
      if (document.getElementById("onlyUnlabeled").checked) {
        render();
      } else if (currentIdx < list.length - 1) {
        currentIdx++;
        render();
      } else {
        showToast("🎉 当前模块最后一题已标完！可切到顶部下一个模块。");
      }
    }, 180);
  }
}

function applyWizardChoice(choice) {
  const item = getCurrentItem();
  if (!item) return;
  const bid = item.blind_id;
  if (currentTask === "PR_53") {
    const map = {
      L: ["CONTRACT_PRESERVING_FIX", "YES"],
      REL: ["LEGITIMATE_RELAXATION", "YES"],
      S: ["TEST_RESPONSIBILITY_SHIFT", "NO"],
      W: ["GENUINE_ORACLE_WEAKENING", "NO"],
      INS: ["INSUFFICIENT_EVIDENCE", "NO"],
    };
    const [c, lg] = map[choice];
    setField(bid, "rater_label_5class", c, { is_legitimate_repair_YES_NO: lg });
  } else if (currentTask === "TAX_60") {
    setField(bid, "semantic_severity_W_S_L", choice);
  } else {
    setField(bid, "label_W_S_L", choice);
  }
}

function getCurrentItem() {
  const list = getVisibleList();
  if (!list.length) return null;
  if (currentIdx >= list.length) currentIdx = list.length - 1;
  if (currentIdx < 0) currentIdx = 0;
  return list[currentIdx];
}

function updateCounters() {
  document.getElementById("modeCoreBtn").classList.toggle("active", poolMode === "core");
  document.getElementById("modeFullBtn").classList.toggle("active", poolMode === "full");
  let totDone = 0, totAll = 0;
  for (const t of TASK_ORDER) {
    const arr = getPoolForTask(t);
    const done = arr.filter(x => isComplete(t, x.blind_id)).length;
    document.getElementById("cnt-" + t).textContent = `(${done}/${arr.length})`;
    totDone += done; totAll += arr.length;
  }
  const modeLbl = poolMode === "core" ? "核心集总进度" : "全量集总进度";
  document.getElementById("totalProgress").textContent = `${modeLbl}：${totDone} / ${totAll} (${totAll ? Math.round(totDone/totAll*100) : 0}%)`;

  if (currentTask === "INTRO") return;
  const list = getVisibleList();
  const strip = document.getElementById("jumpStrip");
  strip.innerHTML = list.map((it, i) => {
    const done = isComplete(currentTask, it.blind_id);
    const cls = "jdot" + (done ? " done" : "") + (i === currentIdx ? " curr" : "");
    return `<button class="${cls}" title="${it.blind_id}" onclick="jumpTo(${i})">${i+1}</button>`;
  }).join("");
}

function jumpTo(i) { currentIdx = i; render(); }
function stepCase(delta) {
  const list = getVisibleList();
  if (!list.length) return;
  currentIdx = Math.max(0, Math.min(list.length - 1, currentIdx + delta));
  render();
}

function jumpNextUnlabeled() {
  if (currentTask === "INTRO") { startFirstTask(); return; }
  const list = getVisibleList();
  for (let i = currentIdx + 1; i < list.length; i++) {
    if (!isComplete(currentTask, list[i].blind_id)) { currentIdx = i; render(); return; }
  }
  for (let i = 0; i <= currentIdx && i < list.length; i++) {
    if (!isComplete(currentTask, list[i].blind_id)) { currentIdx = i; render(); return; }
  }
  for (const t of TASK_ORDER) {
    const arr = getPoolForTask(t);
    if (arr.some(x => !isComplete(t, x.blind_id))) {
      showToast(`当前模块已全部标完！正在为你切到未完成的 ${t}...`);
      switchTask(t);
      return;
    }
  }
  showToast("🎉 太棒了！全部 4 个模块均已 100% 标注完成！请点右上角绿色按钮下载结果文件。");
}

function renderWizardHtml(item) {
  const isPr = (currentTask === "PR_53");
  const notWplus = isPr && item && item.digest && item.digest.pr_wplus_status === false;
  return `<details class="wizard-box">
    <summary>🧭 拿不准选哪个？点此用「两步傻瓜问答」帮你推导</summary>
    ${notWplus ? `<div class="wiz-step"><div><b>前置检查：</b>本题左侧红条已显示「未作为纯测试独立稳态通过」！</div><div class="wiz-btns"><button class="wiz-btn" style="border-color:#dc2626;color:#b91c1c;" onclick="applyWizardChoice('INS')">⚠️ 直接选第 5 项 INSUFFICIENT_EVIDENCE</button></div></div>` : ``}
    <div class="wiz-step">
      <div><b>第 1 问：</b>看左侧绿增红删，补丁是不是<b>仅仅做了合理排序 / 清理测试前脏状态 / 等价结构断言</b>，而原本要查的业务键值和边界<b>一点没少查</b>？</div>
      <div class="wiz-btns">
        <button class="wiz-btn" style="border-color:#16a34a;color:#15803d;" onclick="applyWizardChoice('L')">✅ 0漏抓或完全等价 → ${isPr ? '选 1 (契约完整保持)' : '选 L（正常修法）'}</button>
        ${isPr ? `<button class="wiz-btn" style="border-color:#16a34a;color:#15803d;" onclick="applyWizardChoice('REL')">🟢 排好序跳过旧分支少杀附带变异 → 选 2 (合法规范化放宽)</button>` : ``}
      </div>
    </div>
    <div class="wiz-step">
      <div><b>第 2 问（如果检查确实缩水或偏离了）：</b>它是怎么缩水的？</div>
      <div class="wiz-btns">
        <button class="wiz-btn" onclick="applyWizardChoice('S')">🟠 换了被测对象 / Mock绕过真方法 / 砍空输入或清空全局单例 → 选 S</button>
        <button class="wiz-btn" style="border-color:#dc2626;color:#b91c1c;" onclick="applyWizardChoice('W')">🔴 还在测原方法，但删了 assert 或把相等改成了 contains/非空 → 选 W</button>
      </div>
    </div>
  </details>`;
}

function renderFormOnly(item) {
  const form = document.getElementById("formArea");
  const badge = document.getElementById("saveStateBadge");
  if (!item) {
    badge.className = "badge badge-done"; badge.textContent = "本列表已完成";
    form.innerHTML = `<div style="padding:24px;text-align:center;color:#15803d;font-weight:700;line-height:1.7;">
      🎉 当前模块在当前模式下已全部标完！<br>
      <button class="btn btn-primary" style="margin-top:10px;" onclick="jumpNextUnlabeled()">跳到下一个未完成模块 →</button>
    </div>`;
    return;
  }
  const bid = item.blind_id;
  const a = answers[bid] || { confidence_1to3: "3", rationale_1_sentence: "" };
  const done = isComplete(currentTask, bid);
  badge.className = "badge " + (done ? "badge-done" : "badge-todo");
  badge.textContent = done ? "✓ 已自动保存" : "待点选";

  let html = renderWizardHtml(item);

  if (currentTask === "PR_53") {
    html += `<div class="q-group"><span class="q-label">请点选 1 项（支持键盘数字键 <span class="kbd">1</span>–<span class="kbd">5</span>）：</span><div class="opt-list">`;
    for (const o of PR_OPTS) {
      const sel = a.rater_label_5class === o.code ? " selected" : "";
      html += `<button class="opt-btn ${o.cls}${sel}" onclick="setField('${bid}','rater_label_5class','${o.code}',{is_legitimate_repair_YES_NO:'${o.legit}'})">
        <div class="opt-title"><span>${o.title}</span><span class="opt-code">${o.legit === 'YES' ? '合法 YES' : '非合法 NO'}</span></div>
        <div class="opt-sub">${o.sub}</div>
      </button>`;
    }
    html += `</div></div>`;
  } else if (currentTask === "TAX_60") {
    html += `<div class="q-group"><span class="q-label">第一步：它用了哪种改动手法？（键盘 <span class="kbd">1</span>–<span class="kbd">7</span>）</span><div class="opt-list">`;
    for (const o of TAX_MECH_OPTS) {
      const sel = a.primary_mechanism_M1toM7 === o.code ? " selected" : "";
      html += `<button class="opt-btn${sel}" onclick="setField('${bid}','primary_mechanism_M1toM7','${o.code}')">
        <div class="opt-title"><span>${o.title}</span></div>
        <div class="opt-sub">${o.sub}</div>
      </button>`;
    }
    html += `</div></div>`;
    html += `<div class="q-group"><span class="q-label">第二步：定性为 L / S / W 哪一类？（键盘 <span class="kbd">L</span> / <span class="kbd">S</span> / <span class="kbd">W</span>）</span><div class="opt-list">`;
    for (const o of WSL_OPTS) {
      const sel = a.semantic_severity_W_S_L === o.code ? " selected" : "";
      html += `<button class="opt-btn ${o.cls}${sel}" onclick="setField('${bid}','semantic_severity_W_S_L','${o.code}')">
        <div class="opt-title"><span>${o.title}</span></div>
        <div class="opt-sub">${o.sub}</div>
      </button>`;
    }
    html += `</div></div>`;
  } else {
    html += `<div class="q-group"><span class="q-label">只需点选 1 项（支持键盘快捷键 <span class="kbd">L</span> / <span class="kbd">S</span> / <span class="kbd">W</span>）：</span><div class="opt-list">`;
    for (const o of WSL_OPTS) {
      const sel = a.label_W_S_L === o.code ? " selected" : "";
      html += `<button class="opt-btn ${o.cls}${sel}" onclick="setField('${bid}','label_W_S_L','${o.code}')">
        <div class="opt-title"><span>${o.title}</span><span class="opt-code">按键 ${o.key}</span></div>
        <div class="opt-sub">${o.sub}</div>
      </button>`;
    }
    html += `</div></div>`;
  }

  const conf = a.confidence_1to3 || "3";
  html += `<div class="q-group"><span class="q-label">把握程度（默认「高」，吃不准时可点「低/中」方便后续核对）：</span><div class="opt-row">
    ${[["1","1 · 拿不准"],["2","2 · 较有把握"],["3","3 · 很确定"]].map(([v,lbl]) =>
      `<button class="opt-btn ${conf===v?'selected':''}" style="text-align:center;padding:6px;" onclick="setField('${bid}','confidence_1to3','${v}')"><b>${lbl}</b></button>`
    ).join("")}
  </div></div>`;

  html += `<div class="q-group"><span class="q-label">💬 备注理由（<span style="color:#16a34a;">完全选填！不填也算完成</span>；也可点下方胶囊一键填入）：</span>
    <div class="chips">${QUICK_CHIPS.map(c => `<span class="chip" onclick="appendChip('${bid}','${c.replace(/'/g,"\\'")}')">+ ${c}</span>`).join("")}</div>
    <textarea id="rationaleInput" placeholder="可留空！只有你觉得这道题有特殊之处想记一笔时再写..." oninput="updateRationale('${bid}', this.value)">${escHtml(a.rationale_1_sentence || "")}</textarea>
  </div>`;

  html += `<div class="nav-bar">
    <button class="btn" onclick="stepCase(-1)">← 上一题</button>
    <span style="font-size:12px;color:var(--muted);">键盘 <span class="kbd">←</span> <span class="kbd">→</span> 翻页</span>
    <button class="btn btn-primary" onclick="stepCase(1)">下一题 →</button>
  </div>`;
  form.innerHTML = html;
}

function appendChip(bid, txt) {
  if (!answers[bid]) answers[bid] = { confidence_1to3: "3", rationale_1_sentence: "" };
  answers[bid].rationale_1_sentence = txt;
  saveStore();
  const el = document.getElementById("rationaleInput");
  if (el) el.value = txt;
}

function updateRationale(bid, val) {
  if (!answers[bid]) answers[bid] = { confidence_1to3: "3", rationale_1_sentence: "" };
  answers[bid].rationale_1_sentence = val;
  saveStore();
}

function render() {
  updateCounters();
  const isIntro = (currentTask === "INTRO");
  document.getElementById("introView").style.display = isIntro ? "block" : "none";
  document.getElementById("auditView").style.display = isIntro ? "none" : "block";
  document.getElementById("toolbarRight").style.display = isIntro ? "none" : "flex";
  if (isIntro) return;

  document.getElementById("roleBannerText").innerHTML = ROLE_BANNERS[currentTask] || "";

  const list = getVisibleList();
  const item = getCurrentItem();
  if (!item) {
    document.getElementById("metaBadges").innerHTML = "";
    document.getElementById("caseIndexText").textContent = "0 / 0";
    document.getElementById("testName").textContent = "当前筛选下已无未标注题目";
    document.getElementById("digestCard").innerHTML = "";
    document.getElementById("codeArea").innerHTML = "";
    document.getElementById("partnerSection").style.display = "none";
    document.getElementById("fullPrDiffWrap").style.display = "none";
    document.getElementById("mutantsArea").innerHTML = "";
    document.getElementById("ctxHeader").textContent = "";
    renderFormOnly(null);
    return;
  }

  document.getElementById("caseIndexText").textContent = `当前第 ${currentIdx + 1} / ${list.length} 题`;
  const langCls = item.language === "java" ? "badge-java" : "badge-py";
  const mutCls = (item.mut_badge || "").includes("0 漏抓") ? "badge-mut-ok" : "badge-mut";
  document.getElementById("metaBadges").innerHTML =
    `<span class="badge badge-id">${item.blind_id}</span>` +
    (item.is_core ? `<span class="badge badge-core">⚡ 核心必标</span>` : ``) +
    `<span class="badge ${langCls}">${item.language.toUpperCase()}</span>` +
    `<span class="badge">${escHtml(item.flaky_type)}</span>` +
    `<span class="badge ${mutCls}">${escHtml(item.mut_badge)}</span>`;

  let titleTxt = `${item.project}  |  ${item.test_name}  |  ${item.scope_summary || ""}`;
  if (item.prod_files && item.prod_files.length) titleTxt += "  |  修改的生产文件: " + item.prod_files.join(", ");
  document.getElementById("testName").textContent = titleTxt;

  document.getElementById("digestCard").innerHTML = renderDigestHtml(item);

  if (diffMode === "diff") {
    document.getElementById("codeArea").innerHTML = renderDiffHtml(item.target_diff);
  } else {
    document.getElementById("codeArea").innerHTML = `<div class="sbs-grid">
      <div class="code-pane"><div class="code-pane-hdr">修改前：原测试方法 (Original)</div><pre>${escHtml(item.orig_target)}</pre></div>
      <div class="code-pane"><div class="code-pane-hdr">修改后：补丁测试方法 (Patched)</div><pre>${escHtml(item.patched_target)}</pre></div>
    </div>`;
  }

  if (item.partner_diff) {
    document.getElementById("partnerSection").style.display = "block";
    document.getElementById("partnerDiffArea").innerHTML = renderDiffHtml(item.partner_diff);
  } else {
    document.getElementById("partnerSection").style.display = "none";
  }

  if (item.full_pr_tree_diff) {
    document.getElementById("fullPrDiffWrap").style.display = "block";
    document.getElementById("fullPrDiffArea").innerHTML = renderDiffHtml(item.full_pr_tree_diff);
  } else {
    document.getElementById("fullPrDiffWrap").style.display = "none";
  }

  if (item.lost_mutants && item.lost_mutants.length) {
    document.getElementById("mutantsArea").innerHTML = item.lost_mutants.map((m, idx) => `
      <div class="mut-card">
        <div class="mut-loc">漏抓 Bug #${idx+1} ${m.tag ? `[${escHtml(m.tag)}] ` : ""}生产代码位置：${escHtml(m.location)}</div>
        <div class="mut-cn">👉 人话解释：机器在这里「${escHtml(m.cn_hint)}」，原测试能报错抓住，打完补丁后没报错放过了！</div>
        ${m.desc ? `<div class="mut-desc">底层算子描述：${escHtml(m.desc)}</div>` : ""}
        ${renderSliceHtml(m.source_slice)}
      </div>
    `).join("");
  } else {
    document.getElementById("mutantsArea").innerHTML = `<div style="font-size:12.5px;color:#15803d;background:#f0fdf4;border:1px solid #bbf7d0;padding:9px 12px;border-radius:8px;font-weight:600;">✓ 本题没有记录到漏抓基准生产 Bug（请结合上方独立稳态提示与完整代码 Diff 判断）。</div>`;
  }

  document.getElementById("ctxHeader").textContent = item.context_header || "(无额外类头上下文)";
  renderFormOnly(item);
}

function csvCell(v) {
  const s = (v === undefined || v === null) ? "" : String(v);
  return '"' + s.replace(/"/g, '""') + '"';
}

function downloadBlob(filename, content, mime) {
  const blob = new Blob([content], { type: mime });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 300);
}

function exportResultJSON() {
  let doneCore = 0, doneAll = 0;
  for (const t of TASK_ORDER) {
    for (const it of DATA[t]) {
      if (isComplete(t, it.blind_id)) {
        doneAll++;
        if (it.is_core) doneCore++;
      }
    }
  }
  const payload = {
    protocol_version: "3.1.1-human-audit-workbench-canonical",
    rater: RATER_ID,
    pool_mode: poolMode,
    completed_core_of_120: doneCore,
    completed_total_of_277: doneAll,
    saved_at: new Date().toISOString(),
    answers
  };
  downloadBlob(`${RATER_ID}_audit_backup.json`, JSON.stringify(payload, null, 2), "application/json");
  showToast(`已下载 ${RATER_ID}_audit_backup.json（已完成 核心 ${doneCore}/120 · 全量 ${doneAll}/277）！放到 human_audit_pack 目录即可自动计分。`);
}

function exportAllCSVs() {
  const bom = "\uFEFF";
  let cols1 = ["blind_id","is_core","language","flaky_type","test_name","pr_file_scope","lost_mutants_count","rater_label_5class","is_legitimate_repair_YES_NO","confidence_1to3","rationale_1_sentence"];
  let rows1 = [cols1.join(",")];
  for (const it of DATA.PR_53) {
    const a = answers[it.blind_id] || {};
    rows1.push([it.blind_id, it.is_core?"YES":"NO", it.language, it.flaky_type, it.test_name, it.scope_summary, it.lost_mutants_count, a.rater_label_5class||"", a.is_legitimate_repair_YES_NO||"", a.confidence_1to3||"", a.rationale_1_sentence||""].map(csvCell).join(","));
  }
  downloadBlob(`${RATER_ID}_PR_53.csv`, bom + rows1.join("\r\n"), "text/csv;charset=utf-8");

  let cols2 = ["blind_id","is_core","language","flaky_type","test_name","scope_kind","primary_mechanism_M1toM7","semantic_severity_W_S_L","confidence_1to3","rationale_1_sentence"];
  let rows2 = [cols2.join(",")];
  for (const it of DATA.TAX_60) {
    const a = answers[it.blind_id] || {};
    rows2.push([it.blind_id, it.is_core?"YES":"NO", it.language, it.flaky_type, it.test_name, it.scope_summary, a.primary_mechanism_M1toM7||"", a.semantic_severity_W_S_L||"", a.confidence_1to3||"", a.rationale_1_sentence||""].map(csvCell).join(","));
  }
  setTimeout(() => downloadBlob(`${RATER_ID}_TAX_60.csv`, bom + rows2.join("\r\n"), "text/csv;charset=utf-8"), 300);

  let cols3 = ["blind_id","is_core","language","flaky_type","test_name","m_ext_killed_of_total","label_W_S_L","confidence_1to3","rationale_1_sentence"];
  let rows3 = [cols3.join(",")];
  for (const it of DATA.HELDOUT) {
    const a = answers[it.blind_id] || {};
    rows3.push([it.blind_id, it.is_core?"YES":"NO", it.language, it.flaky_type, it.test_name, it.m_ext_killed_of_total, a.label_W_S_L||"", a.confidence_1to3||"", a.rationale_1_sentence||""].map(csvCell).join(","));
  }
  setTimeout(() => downloadBlob(`${RATER_ID}_HELDOUT.csv`, bom + rows3.join("\r\n"), "text/csv;charset=utf-8"), 600);

  let rows4 = [cols3.join(",")];
  for (const it of DATA.SOTA_53) {
    const a = answers[it.blind_id] || {};
    rows4.push([it.blind_id, it.is_core?"YES":"NO", it.language, it.flaky_type, it.test_name, it.m_ext_killed_of_total, a.label_W_S_L||"", a.confidence_1to3||"", a.rationale_1_sentence||""].map(csvCell).join(","));
  }
  setTimeout(() => downloadBlob(`${RATER_ID}_SOTA_53.csv`, bom + rows4.join("\r\n"), "text/csv;charset=utf-8"), 900);
  showToast("已导出 4 份 CSV 明细表！");
}

function importJSON(evt) {
  const f = evt.target.files[0];
  if (!f) return;
  const rd = new FileReader();
  rd.onload = () => {
    try {
      const obj = JSON.parse(rd.result);
      answers = Object.assign({}, answers, obj.answers || obj);
      saveStore();
      render();
      showToast("进度导入成功！");
    } catch (e) { alert("JSON 解析失败：" + e); }
  };
  rd.readAsText(f);
}

document.addEventListener("keydown", e => {
  if (document.activeElement && (document.activeElement.tagName === "TEXTAREA" || document.activeElement.tagName === "INPUT")) return;
  if (currentTask === "INTRO") return;
  const item = getCurrentItem();
  if (e.key === "ArrowRight") { stepCase(1); return; }
  if (e.key === "ArrowLeft") { stepCase(-1); return; }
  if (e.key === "n" || e.key === "N") { jumpNextUnlabeled(); return; }
  if (!item) return;
  const bid = item.blind_id;
  const k = e.key.toUpperCase();
  if (currentTask === "PR_53" && ["1","2","3","4","5"].includes(k)) {
    const o = PR_OPTS[parseInt(k, 10) - 1];
    setField(bid, "rater_label_5class", o.code, { is_legitimate_repair_YES_NO: o.legit });
  } else if (currentTask === "TAX_60") {
    if (["1","2","3","4","5","6","7"].includes(k)) {
      const o = TAX_MECH_OPTS[parseInt(k, 10) - 1];
      setField(bid, "primary_mechanism_M1toM7", o.code);
    } else if (["L","S","W"].includes(k)) {
      setField(bid, "semantic_severity_W_S_L", k);
    }
  } else if ((currentTask === "HELDOUT" || currentTask === "SOTA_53") && ["L","S","W"].includes(k)) {
    setField(bid, "label_W_S_L", k);
  }
});

render();
</script>
</body>
</html>
"""


def build_excel_workbook(rater_id: str, rater_title: str, data_shuffled: Dict[str, List[Dict[str, Any]]], out_path: Path) -> None:
    wb = openpyxl.Workbook()
    ws_g = wb.active
    ws_g.title = "0_一分钟看懂怎么标"
    guide_rows = [
        ("FT-1 双盲人工审议表（Excel 备选版）", rater_title),
        ("强烈推荐用网页版！", f"请直接用浏览器双击打开同目录下的 {rater_id.replace('rater_', 'Rater_')}_Blind_Audit_Workbench.html！网页版自带 1 分钟图文教学、红删绿增高亮、完整 PR 夹具 Diff、断言自动提取对比、两步问答助手和一键保存。"),
        ("", ""),
        ("🟢 L（正常修法）", "只消除了乱序或残留脏状态（如固定排序、测试前清缓存），原测试要检查的业务结果和边界一点没少查。"),
        ("🟠 S（偷梁换柱）", "换了被测对象、用 Mock 假对象绕过真方法、把测试输入砍成空壳、清空全局单例或改坏搭档测试。"),
        ("🔴 W（断言放水）", "还在调原方法，但删掉了 assert、把精确相等改成了模糊包含(contains)/非空(!=null)、或切分字符串吞掉了边界。"),
    ]
    for r_i, (k, v) in enumerate(guide_rows, 1):
        ws_g.cell(r_i, 1, k).font = Font(bold=True, size=11)
        ws_g.cell(r_i, 2, v).alignment = Alignment(wrap_text=True, vertical="top")
    ws_g.column_dimensions["A"].width = 24
    ws_g.column_dimensions["B"].width = 112

    hdr_fill = PatternFill("solid", fgColor="0F172A")
    hdr_font = Font(bold=True, color="FFFFFF", size=10)
    input_fill = PatternFill("solid", fgColor="FEF9C3")
    mono_font = Font(name="Consolas", size=9)

    def add_sheet(title: str, headers: List[str], rows: List[List[Any]], validations: List[tuple], widths: List[int]) -> None:
        ws = wb.create_sheet(title)
        ws.freeze_panes = "C2"
        for c_i, h in enumerate(headers, 1):
            cell = ws.cell(1, c_i, h)
            cell.fill = hdr_fill
            cell.font = hdr_font
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws.row_dimensions[1].height = 26

        for r_i, row in enumerate(rows, 2):
            for c_i, val in enumerate(row, 1):
                cell = ws.cell(r_i, c_i, val)
                if headers[c_i - 1] in ("代码差异 (Unified Diff)", "漏抓生产Bug说明"):
                    cell.font = mono_font
                if headers[c_i - 1].startswith("【请填】"):
                    cell.fill = input_fill
                cell.alignment = Alignment(vertical="top", wrap_text=True)
            ws.row_dimensions[r_i].height = 110

        for col_idx, formula in validations:
            col_l = get_column_letter(col_idx)
            dv = DataValidation(type="list", formula1=f'"{formula}"', allow_blank=True)
            ws.add_data_validation(dv)
            dv.add(f"{col_l}2:{col_l}{len(rows)+1}")

        for c_i, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(c_i)].width = w

    def fmt_muts_cell(muts: List[Dict[str, str]]) -> str:
        if not muts:
            return "(无漏抓基准生产Bug)"
        return "\n".join(f"{idx}. {m['location']} | {m['cn_hint']}" for idx, m in enumerate(muts[:5], 1))

    def fmt_pr_diff_cell(it: Dict[str, Any]) -> str:
        parts = [it["target_diff"]]
        if it["partner_diff"]:
            parts.append(f"--- Partner Diff ---\n{it['partner_diff']}")
        if it.get("full_pr_tree_diff"):
            parts.append(f"--- Full PR File-Tree Diff ---\n{it['full_pr_tree_diff'][:12000]}")
        return "\n\n".join(parts)[:30000]

    add_sheet(
        "1_PR_53",
        ["blind_id", "核心120", "语言/类型", "测试名称", "稳态与改动范围", "代码差异 (Unified Diff)", "漏抓生产Bug说明", "【请填】5分类标签", "【请填】是否合法(YES/NO)", "【请填】置信度(1-3)", "【选填】备注"],
        [
            [
                it["blind_id"], "核心必标" if it["is_core"] else "扩展", f"{it['language'].upper()} / {it['flaky_type']}", it["test_name"],
                ("纯测试W+稳态: 通过\n" if it["digest"]["pr_wplus_status"] else "纯测试W+稳态: 未通过 (选INSUFFICIENT_EVIDENCE)\n")
                + it["scope_summary"] + (f"\n生产文件: {', '.join(it['prod_files'])}" if it["prod_files"] else ""),
                fmt_pr_diff_cell(it),
                it["mut_badge"] + "\n" + fmt_muts_cell(it["lost_mutants"]),
                "", "", "3", "",
            ]
            for it in data_shuffled["PR_53"]
        ],
        [
            (8, "CONTRACT_PRESERVING_FIX,LEGITIMATE_RELAXATION,TEST_RESPONSIBILITY_SHIFT,GENUINE_ORACLE_WEAKENING,INSUFFICIENT_EVIDENCE"),
            (9, "YES,NO"),
            (10, "1,2,3"),
        ],
        [11, 10, 14, 32, 26, 62, 44, 30, 14, 12, 28],
    )

    add_sheet(
        "2_TAX_60",
        ["blind_id", "核心120", "语言/类型", "测试名称", "改动范围", "代码差异 (Unified Diff)", "漏抓生产Bug说明", "【请填】主要机制(M1-M7)", "【请填】语义性质(W/S/L)", "【请填】置信度(1-3)", "【选填】备注"],
        [
            [
                it["blind_id"], "核心必标" if it["is_core"] else "扩展", f"{it['language'].upper()} / {it['flaky_type']}", it["test_name"],
                it["scope_summary"],
                it["target_diff"] + (f"\n\n--- Partner Diff ---\n{it['partner_diff']}" if it["partner_diff"] else ""),
                it["mut_badge"] + "\n" + fmt_muts_cell(it["lost_mutants"]),
                "", "", "3", "",
            ]
            for it in data_shuffled["TAX_60"]
        ],
        [
            (8, "M1_PredicateProjectionOrRelaxation,M2_StateResetOrClobber,M3_ControlFlowOrExceptionChange,M4_SUTCallAlterationOrBypass,M5_PartnerOrSharedScopeEdit,M6_DelimiterOrFormatNormalization,M7_PreconditionOrSingletonSetupAlteration"),
            (9, "W,S,L"),
            (10, "1,2,3"),
        ],
        [11, 10, 14, 32, 20, 60, 44, 36, 14, 12, 28],
    )

    for sheet_name, sec_key in (("3_HELDOUT_111", "HELDOUT"), ("4_SOTA_53", "SOTA_53")):
        add_sheet(
            sheet_name,
            ["blind_id", "核心120", "语言/类型", "测试名称", "代码差异 (Unified Diff)", "漏抓生产Bug说明", "【请填】标签(W/S/L)", "【请填】置信度(1-3)", "【选填】备注"],
            [
                [
                    it["blind_id"], "核心必标" if it["is_core"] else "扩展", f"{it['language'].upper()} / {it['flaky_type']}", it["test_name"],
                    it["target_diff"] + (f"\n\n--- Partner Diff ---\n{it['partner_diff']}" if it["partner_diff"] else ""),
                    it["mut_badge"] + "\n" + fmt_muts_cell(it["lost_mutants"]),
                    "", "3", "",
                ]
                for it in data_shuffled[sec_key]
            ],
            [(7, "W,S,L"), (8, "1,2,3")],
            [13, 10, 14, 32, 62, 46, 14, 12, 28],
        )

    wb.save(str(out_path))


def main() -> None:
    PACK_DIR.mkdir(parents=True, exist_ok=True)
    all_cases, v31_key = build_all_blinded_cases()
    V31_KEY_PATH.write_text(json.dumps(v31_key, indent=2, ensure_ascii=False), encoding="utf-8")

    # Verify all 10 shared-fixture PRs now show complete non-empty PR file-tree diffs (`H2`)
    unchanged_method_prs = [c["blind_id"] for c in all_cases["PR_53"] if c["digest"]["method_unchanged"]]
    for c in all_cases["PR_53"]:
        assert "diff --git " in (c["target_diff"] + c["full_pr_tree_diff"]), f"Missing PR git diff on {c['blind_id']}"

    ho_muts = sum(1 for x in all_cases["HELDOUT"] if x["lost_mutants"])
    sota_muts = sum(1 for x in all_cases["SOTA_53"] if x["lost_mutants"])
    tax_muts = sum(1 for x in all_cases["TAX_60"] if x["lost_mutants"])
    print(
        f"[CHECK] Cases built: PR={len(all_cases['PR_53'])} (shared-fixture full-tree diffs={len(unchanged_method_prs)}), "
        f"TAX={len(all_cases['TAX_60'])} (muts={tax_muts}/60), "
        f"HELDOUT={len(all_cases['HELDOUT'])} (muts={ho_muts}/111), SOTA_53={len(all_cases['SOTA_53'])} (muts={sota_muts}/53) | "
        f"Core-120={len(v31_key['core_120_ids'])}"
    )

    for rater_id, fname_prefix, rater_title, seed in (
        ("rater_A", "Rater_A", "标注员 A（第一位作者用）", 202601),
        ("rater_B", "Rater_B", "标注员 B（第二位作者用）", 202602),
    ):
        rng = random.Random(seed)
        shuffled: Dict[str, List[Dict[str, Any]]] = {}
        for k, arr in all_cases.items():
            cp = list(arr)
            rng.shuffle(cp)
            shuffled[k] = cp

        payload_js = json.dumps(shuffled, ensure_ascii=False).replace("</script>", "<\\/script>")
        html = (
            HTML_TEMPLATE
            .replace("__RATER_TITLE__", rater_title)
            .replace("__RATER_ID__", rater_id)
            .replace("__JSON_PAYLOAD__", payload_js)
        )
        html_path = PACK_DIR / f"{fname_prefix}_Blind_Audit_Workbench.html"
        html_path.write_text(html, encoding="utf-8")

        xlsx_path = PACK_DIR / f"{fname_prefix}_Audit_Sheet.xlsx"
        build_excel_workbook(rater_id, rater_title, shuffled, xlsx_path)
        print(f"[OK] Generated {html_path.name} ({html_path.stat().st_size/1024:.1f} KB) + {xlsx_path.name} ({xlsx_path.stat().st_size/1024:.1f} KB)")

    guide_md = """# FT-1 v3.1 双人独立盲审标准编码指南（Canonical Zero-Ambiguity Codebook）

本指南与论文正文（Table 4、Table 5、Section 5.2）及 10 会话盲审 Codex 编码表 **100% 严格对齐**。
强烈推荐直接双击打开 `Rater_A_Blind_Audit_Workbench.html` / `Rater_B_Blind_Audit_Workbench.html` 进行可视化点选。

---

## 一、核心零歧义三步决策树（`L` → `S` → `W`）

1. **Step 1 — 合法规范化（`L`）**：仅消除真实不确定性（确定性排序、测试前隔离脏状态、等价结构比较），原测试核心键值对应、元素重数与边界检查 **100% 保留**，丢失变异体仅因不再经过无序偶然旧路径。
2. **Step 2 — 测试责任转移（`S`）**：非 `L`，且改变了被测输出对象、用 Mock/Stub 绕过了原生产默认实现、缩减前置输入夹具/清空全局单例导致深层分支不可达、或损害了伴随测试职责。
3. **Step 3 — 真实预言机弱化（`W`）**：仍调用原生产入口检查同一输出，但删减了断言、将精确相等放宽为 `contains`/`isNotNull`/仅查长度、或用有损切分（如单参 `split(",")`）丢弃了边界信息。

---

## 二、四项任务标准编码表

### 模块一：`PR_53`（53 个真实 Java PR，含完整测试类与 `@Before` 夹具 Diff）
- `CONTRACT_PRESERVING_FIX`（合法 `YES`）：纯测试独立通过 $W^+$，完全保持契约（0 基准变异丢失或等价结构校验）。
- `LEGITIMATE_RELAXATION`（合法 `YES`）：纯测试独立通过 $W^+$，符合 Step 1 合法规范化（仅少杀附带无序路径变异体）。
- `TEST_RESPONSIBILITY_SHIFT`（非合法 `NO`）：纯测试独立通过 $W^+$，符合 Step 2 责任转移。
- `GENUINE_ORACLE_WEAKENING`（非合法 `NO`）：纯测试独立通过 $W^+$，符合 Step 3 预言机弱化。
- `INSUFFICIENT_EVIDENCE`（非合法 `NO`）：未作为纯测试独立通过 $W^+$ 稳态检验。

### 模块二：`TAX_60`（60 个分层侵蚀补丁：机制 `M1–M7` + 严重度 `W/S/L`）
- `M1_PredicateProjectionOrRelaxation`：断言放水、删除或谓词降维（如精确相等改子串/非空/长度）
- `M2_StateResetOrClobber`：在测试体或后置中清空/覆盖全局或共享状态
- `M3_ControlFlowOrExceptionChange`：改动测试内循环、条件分支或异常捕获导致跳过验证
- `M4_SUTCallAlterationOrBypass`：删改、替换或用 Mock/Stub 绕过焦点生产方法调用
- `M5_PartnerOrSharedScopeEdit`：改动伴随测试或共享夹具导致伴随/共享义务失守
- `M6_DelimiterOrFormatNormalization`：分隔符切分或格式归一化丢弃边界信息
- `M7_PreconditionOrSingletonSetupAlteration`：改动或缩减前置输入夹具/单例配置导致深层分支不可达

### 模块三 & 模块四：`HELDOUT`（111 题）与 `SOTA_53`（53 题）
- 严格按上方三步决策树点选 `L`、`S` 或 `W`。
"""
    (PACK_DIR / "ANNOTATION_GUIDE.md").write_text(guide_md, encoding="utf-8")

    # Strict automated ZERO-LEAK blinding audit across HTML workbenches
    forbidden = (
        "B0_Standard", "B2_Prompt", "B3_Static", "B3_Plus", "B4_Scalar", "Ours_MuGuard",
        "S1_FlakyFix", "S1b_FlakyFix", "S2_FlakyDoctor", "S3_FlakyGuard", "S4_MuGuard", "S5_FlakyGuard",
        "FlakyFix", "FlakyDoctor", "FlakyGuard", "MuGuard-Flake",
        "deepseek-flash", "deepseek-v4-pro", "qwen3.7-plus", "kimi-k2.5", "glm-5", "gpt-6-astra",
        "stage1_label", "codex_10session", "pr_full_gate_accepts", "idoft_status", "github.com/",
    )
    leaks = []
    for fn in ("Rater_A_Blind_Audit_Workbench.html", "Rater_B_Blind_Audit_Workbench.html"):
        txt = (PACK_DIR / fn).read_text(encoding="utf-8")
        for kw in forbidden:
            if kw in txt:
                leaks.append((fn, kw))
    print(f"[SECURITY BLINDING AUDIT] Leaks found in HTML workbenches: {leaks}")
    assert not leaks, f"Blinding leaks detected: {leaks}"


if __name__ == "__main__":
    main()
