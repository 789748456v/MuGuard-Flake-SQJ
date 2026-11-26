"""
FT-1 v2.0.0-frozen Unified Remote Evaluator (`in_loop_gate` & `post_freeze_heldout`).
Strictly enforces:
  1) Closed-fail partner & shared-fixture scope verification (`ABSTAIN_PARTNER_UNCOVERED`, `PARTNER_MUT_EVAL_ERROR`).
  2) Role-tagged `(role, mutant_id)` target/partner/joint obligation accounting.
  3) Complete isolation of `M_ext` and `W+_heldout` from `in_loop_gate` execution.
"""
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Set, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import remote_core as rc
import remote_python_core as rpc
from remote_v2_freeze_benchmark_and_mext import (
    LAST_PY_MUT_DIAG,
    check_java_w_plus_heldout_detailed,
    check_py_w_plus_heldout,
    check_py_w_plus_heldout_detailed,
    run_attributed_java_pit,
    run_attributed_py_mut,
)


def _check_and_mark_infra(
    res: Dict[str, Any],
    rc_code: Optional[int],
    diag_text: str,
    stage: str,
    is_compile: bool = False,
) -> bool:
    hit = rc.detect_infra_failure(rc_code, diag_text, is_compile=is_compile) or rc.pop_infra_trace()
    if hit:
        res["infra_error"] = True
        res["infra_error_detail"] = f"[{stage}] {hit}"
        return True
    return False
from v2_guards_and_attribution import (
    HARNESS_VERSION,
    analyze_affected_test_scope,
    compute_role_tagged_retention,
    compute_unified_diff,
    is_code_semantically_changed,
    java_pit_mutant_exact_id,
    resolve_pit_mutants_by_exact_id,
)


def _restore_runtime_py_fixtures(pdir: str, exclude_rels: Set[str]) -> None:
    """Reset any non-candidate tracked fixture files modified at runtime by OD polluters (e.g., Breathe fruit.py, py-autodoc .md)."""
    try:
        p = subprocess.run(["git", "-C", pdir, "diff", "--name-only", "HEAD", "--"], capture_output=True, text=True, timeout=30)
        if p.returncode == 0:
            to_reset = [
                x.strip()
                for x in (p.stdout or "").splitlines()
                if x.strip() and x.strip().replace("\\", "/") not in exclude_rels
            ]
            if to_reset:
                subprocess.run(["git", "-C", pdir, "checkout", "HEAD", "--"] + to_reset, capture_output=True, timeout=30)
    except Exception:
        pass


def evaluate_java_v2_in_loop(sample: Dict[str, Any], candidate: Dict[str, Any]) -> Dict[str, Any]:
    rc.reset_infra_trace()
    t0 = time.time()
    pdir = sample["proj_dir"]
    mod_rel = sample["module"]
    jdk = sample["jdk"]
    uses_j5 = sample["uses_junit5"]
    ttype = sample["test_type"]
    pit_pair = tuple(sample["pit_pair"])
    mod_dir = pdir if mod_rel in ("", ".") else os.path.join(pdir, mod_rel)
    pom_path = os.path.join(mod_dir, "pom.xml")
    vic_file = sample["victim_file_path"]
    pol_file = sample.get("polluter_file_path") or vic_file
    same_file = (os.path.abspath(vic_file) == os.path.abspath(pol_file))

    orig_vic_b = open(vic_file, "rb").read()
    orig_pol_b = orig_vic_b if same_file else open(pol_file, "rb").read()
    orig_pom_b = open(pom_path, "rb").read()
    orig_vic_src = re.sub(r"/\*FT1_ISOLATED\s*(.*?)\s*\*/", r"\1", orig_vic_b.decode("utf-8", errors="replace"))
    orig_pol_src = (
        orig_vic_src if same_file
        else re.sub(r"/\*FT1_ISOLATED\s*(.*?)\s*\*/", r"\1", orig_pol_b.decode("utf-8", errors="replace"))
    )
    orig_pom_src = orig_pom_b.decode("utf-8", errors="replace")
    vbak = rc.backup_class_files(mod_dir, sample["victim_fqcn"])
    pbak = rc.backup_class_files(mod_dir, sample["polluter_fqcn"]) if (sample.get("polluter_fqcn") and sample["polluter_fqcn"] != sample["victim_fqcn"]) else {}

    res: Dict[str, Any] = {
        "harness_version": HARNESS_VERSION,
        "compiled": False,
        "compile_error": "",
        "compile_sec": 0.0,
        "w_plus_passed": False,
        "w_plus_diag": "",
        "w_plus_sec": 0.0,
        "pit_ran": False,
        "target_pit_sec": 0.0,
        "partner_pit_sec": 0.0,
        "pit_sec": 0.0,
        "scope_kind": "TARGET_ONLY",
        "affects_partner_or_shared": False,
        "gate_status_override": None,
        "gate_safe": False,
        "eroded": False,
        "sound_fix": False,
        "target_m_det_total": len(sample["m_det"]),
        "target_m_det_killed": 0,
        "target_retention": None,
        "partner_m_det_total": len(sample.get("polluter_m_det") or []),
        "partner_m_det_killed": None,
        "partner_retention": None,
        "joint_m_det_total": len(sample["m_det"]),
        "joint_m_det_killed": 0,
        "joint_retention": None,
        "retention_m_det": None,
        "survived_mutants": [],
        "m_gold_total": len(sample.get("m_gold_ids") or []),
        "m_gold_killed": 0,
        "retention_m_gold": None,
        "applied_diff_sha256": "",
        "total_eval_sec": 0.0,
    }

    try:
        new_vic_src = orig_vic_src
        new_pol_src = orig_pol_src
        tcode_used = ""
        pcode_used = sample.get("orig_polluter_code", "")

        if ttype == "ID":
            tcode = candidate.get("test_code")
            if not tcode:
                res["compile_error"] = "Missing // <fix start> ... // <fix end> method code."
                return res
            tcode_used = tcode
            new_vic_src = rc.replace_java_method(orig_vic_src, sample["victim_method"], tcode)
            new_vic_src = rc.inject_java_imports(new_vic_src, candidate.get("imports") or [])
            open(vic_file, "w", encoding="utf-8").write(new_vic_src)
        else:
            vcode = candidate.get("victim_code")
            pcode = candidate.get("polluter_code")
            if not vcode and not pcode:
                res["compile_error"] = "Missing both victim and polluter method code."
                return res
            tcode_used = vcode or sample["orig_test_code"]
            pcode_used = pcode or sample.get("orig_polluter_code", "")
            p_changed = is_code_semantically_changed("java", sample.get("orig_polluter_code", ""), pcode)
            if same_file:
                new_s = orig_vic_src
                if vcode:
                    new_s = rc.replace_java_method(new_s, sample["victim_method"], vcode)
                if p_changed and sample["polluter_method"] != sample["victim_method"]:
                    new_s = rc.replace_java_method(new_s, sample["polluter_method"], pcode)
                elif pcode and not vcode:
                    new_s = rc.replace_java_method(new_s, sample["polluter_method"], pcode)
                new_s = rc.inject_java_imports(new_s, candidate.get("imports") or [])
                new_vic_src = new_s
                new_pol_src = new_s
                open(vic_file, "w", encoding="utf-8").write(new_s)
            else:
                if vcode:
                    new_vic_src = rc.replace_java_method(orig_vic_src, sample["victim_method"], vcode)
                    new_vic_src = rc.inject_java_imports(new_vic_src, candidate.get("imports") or [])
                    open(vic_file, "w", encoding="utf-8").write(new_vic_src)
                if p_changed:
                    new_pol_src = rc.replace_java_method(orig_pol_src, sample["polluter_method"], pcode)
                    new_pol_src = rc.inject_java_imports(new_pol_src, candidate.get("imports") or [])
                    open(pol_file, "w", encoding="utf-8").write(new_pol_src)

        pom_mod = False
        if candidate.get("pom"):
            new_pom = rc.inject_pom_dependencies(orig_pom_src, candidate["pom"])
            if new_pom != orig_pom_src:
                pom_mod = True
                open(pom_path, "w", encoding="utf-8").write(new_pom)

        diff_txt = compute_unified_diff(orig_vic_src, new_vic_src, sample["victim_file_rel"])
        if not same_file:
            diff_txt += compute_unified_diff(orig_pol_src, new_pol_src, os.path.basename(pol_file))
        res["applied_diff_sha256"] = hashlib.sha256(diff_txt.encode("utf-8")).hexdigest()[:20]

        act_orig_t = rc.extract_java_method(orig_vic_src, sample["victim_method"]) or sample["orig_test_code"]
        act_new_t = rc.extract_java_method(new_vic_src, sample["victim_method"]) or tcode_used
        act_orig_p = (
            rc.extract_java_method(orig_pol_src, sample["polluter_method"])
            if (ttype == "OD" and sample.get("polluter_method")) else ""
        ) or sample.get("orig_polluter_code", "")
        act_new_p = (
            rc.extract_java_method(new_pol_src, sample["polluter_method"])
            if (ttype == "OD" and sample.get("polluter_method")) else ""
        ) or pcode_used

        scope = analyze_affected_test_scope(
            language="java",
            test_type=ttype,
            orig_vic_file_src=orig_vic_src,
            new_vic_file_src=new_vic_src,
            orig_pol_file_src=orig_pol_src,
            new_pol_file_src=new_pol_src,
            same_file=same_file,
            orig_target_method_src=act_orig_t,
            new_target_method_src=act_new_t,
            orig_partner_method_src=act_orig_p,
            new_partner_method_src=act_new_p,
            pom_modified=pom_mod,
        )
        res["scope_kind"] = scope["scope_kind"]
        res["affects_partner_or_shared"] = scope["affects_partner_or_shared"]

        pl = f"-pl {mod_rel}" if mod_rel not in ("", ".") else ""
        extra_cp = "dependency:build-classpath -Dmdep.outputFile=target/ft1_cp.txt" if pom_mod else ""
        rc_c, out_c, dt_c = rc.docker_mvn(pdir, jdk, f"test-compile {extra_cp} {pl}", timeout=120)
        res["compile_sec"] = round(dt_c, 2)
        if _check_and_mark_infra(res, rc_c, out_c, "java_compile", is_compile=(rc_c != 0 or "BUILD SUCCESS" not in out_c)):
            err_lns = [ln for ln in out_c.splitlines() if "[ERROR]" in ln]
            res["compile_error"] = "\n".join(err_lns[:25])[-1800:] or out_c[-1000:]
            return res
        if rc_c != 0 or "BUILD SUCCESS" not in out_c:
            err_lns = [ln for ln in out_c.splitlines() if "[ERROR]" in ln]
            res["compile_error"] = "\n".join(err_lns[:25])[-1800:] or out_c[-1000:]
            return res
        rc.reset_infra_trace()
        res["compiled"] = True

        w_ok, w_diag, dt_w = rc.check_stability_w_plus(
            pdir, mod_rel, jdk, uses_j5, ttype,
            sample["victim_fqcn"], sample["victim_method"],
            sample.get("polluter_fqcn", ""), sample.get("polluter_method", ""),
        )
        res["w_plus_passed"] = w_ok
        res["w_plus_diag"] = w_diag
        res["w_plus_sec"] = round(dt_w, 2)
        if _check_and_mark_infra(res, None, w_diag, "java_w_plus"):
            res["w_plus_passed"] = False
            return res
        if not w_ok:
            return res
        rc.reset_infra_trace()

        # Attributed Target PIT on `DEFAULTS`
        t_ok, t_muts, dt_tpit, t_tail = run_attributed_java_pit(
            proj_dir=pdir, mod_rel=mod_rel, jdk=jdk,
            test_file_path=vic_file, test_fqcn=sample["victim_fqcn"],
            test_method=sample["victim_method"], focal_classes=sample["focal_classes"],
            pit_pair=pit_pair, fqcn_to_path=sample.get("fqcn_to_path"),
            mutators_arg="DEFAULTS", timeout=125,
        )
        res["target_pit_sec"] = dt_tpit
        res["pit_sec"] = dt_tpit
        if _check_and_mark_infra(res, None, t_tail, "java_target_pit"):
            res["pit_ran"] = False
            res["w_plus_diag"] += f"\n[TARGET_PIT_INFRA_ERROR]: {t_tail[-400:]}"
            return res
        if not t_ok:
            res["pit_ran"] = False
            res["w_plus_diag"] += f"\n[TARGET_PIT_ERROR]: {t_tail[-400:]}"
            return res
        rc.reset_infra_trace()

        t_by_eid, t_st_by_eid = resolve_pit_mutants_by_exact_id(t_muts)
        t_killed: Set[str] = set()
        t_unknown_ids: List[str] = []
        for m in sample["m_det"]:
            eid = m.get("exact_id") or java_pit_mutant_exact_id(m)
            rm = t_by_eid.get(eid)
            if rm is None or rm.get("is_unknown"):
                t_unknown_ids.append(m["id"])
            elif rm.get("attributed_kill"):
                t_killed.add(m["id"])
        res["target_unknown_count"] = len(t_unknown_ids)
        res["target_unknown_ids"] = t_unknown_ids[:10]

        p_ran, p_ok, p_killed = False, False, None
        p_unknown_ids: List[str] = []
        pol_m_det = sample.get("polluter_m_det") or []
        if ttype == "OD" and scope["affects_partner_or_shared"] and len(pol_m_det) > 0:
            p_ran = True
            p_fclasses = sample.get("polluter_focal_classes") or sample["focal_classes"]
            p_ok, p_muts, dt_ppit, p_tail = run_attributed_java_pit(
                proj_dir=pdir, mod_rel=mod_rel, jdk=jdk,
                test_file_path=pol_file, test_fqcn=sample["polluter_fqcn"],
                test_method=sample["polluter_method"], focal_classes=p_fclasses[:4],
                pit_pair=pit_pair, fqcn_to_path=sample.get("fqcn_to_path"),
                mutators_arg="DEFAULTS", timeout=120,
            )
            res["partner_pit_sec"] = dt_ppit
            res["pit_sec"] = round(dt_tpit + dt_ppit, 2)
            if _check_and_mark_infra(res, None, p_tail, "java_partner_pit"):
                res["w_plus_diag"] += f"\n[PARTNER_PIT_INFRA_ERROR]: {p_tail[-400:]}"
                return res
            if p_ok:
                p_by_eid, _ = resolve_pit_mutants_by_exact_id(p_muts)
                p_killed = set()
                for pm in pol_m_det:
                    peid = pm.get("exact_id") or java_pit_mutant_exact_id(pm)
                    rpm = p_by_eid.get(peid)
                    if rpm is None or rpm.get("is_unknown"):
                        p_unknown_ids.append(pm["id"])
                    elif rpm.get("attributed_kill"):
                        p_killed.add(pm["id"])
            else:
                res["w_plus_diag"] += f"\n[PARTNER_PIT_ERROR]: {p_tail[-400:]}"
        res["partner_unknown_count"] = len(p_unknown_ids)
        res["partner_unknown_ids"] = p_unknown_ids[:10]

        ret_info = compute_role_tagged_retention(
            target_m_det=sample["m_det"],
            target_killed_ids=t_killed,
            test_type=ttype,
            partner_m_det=pol_m_det,
            affects_partner=scope["affects_partner_or_shared"],
            partner_eval_ran=p_ran,
            partner_eval_ok=p_ok,
            partner_killed_ids=p_killed,
            target_unknown_ids=t_unknown_ids,
            partner_unknown_ids=p_unknown_ids,
        )
        if t_unknown_ids:
            ret_info["gate_safe"] = False
            ret_info["gate_status_override"] = ret_info["gate_status_override"] or "TARGET_MUT_UNKNOWN"
        elif p_unknown_ids:
            ret_info["gate_safe"] = False
            ret_info["gate_status_override"] = ret_info["gate_status_override"] or "PARTNER_MUT_UNKNOWN"
        res.update(ret_info)
        res["pit_ran"] = (ret_info["gate_status_override"] not in ("PARTNER_MUT_EVAL_ERROR", "TARGET_MUT_UNKNOWN", "PARTNER_MUT_UNKNOWN"))
        res["retention_m_det"] = ret_info["joint_retention"]
        res["sound_fix"] = bool(res["w_plus_passed"] and ret_info["gate_safe"])

        gold_ids = sample.get("m_gold_ids") or []
        if gold_ids:
            gk = sum(1 for gid in gold_ids if gid in t_killed)
            res["m_gold_killed"] = gk
            res["retention_m_gold"] = round(gk / len(gold_ids), 4)
        return res
    except Exception as ex:
        res["compile_error"] = str(ex)
        if isinstance(ex, (OSError, subprocess.SubprocessError, TimeoutError, MemoryError)):
            res["infra_error"] = True
            res["infra_error_detail"] = f"{type(ex).__name__}: {ex}"
        return res
    finally:
        open(vic_file, "wb").write(orig_vic_b)
        if not same_file:
            open(pol_file, "wb").write(orig_pol_b)
        open(pom_path, "wb").write(orig_pom_b)
        rc.restore_class_files(mod_dir, sample["victim_fqcn"], vbak)
        if pbak:
            rc.restore_class_files(mod_dir, sample["polluter_fqcn"], pbak)
        res["total_eval_sec"] = round(time.time() - t0, 2)


def evaluate_py_v2_in_loop(sample: Dict[str, Any], candidate: Dict[str, Any]) -> Dict[str, Any]:
    rc.reset_infra_trace()
    t0 = time.time()
    pdir = sample["proj_dir"]
    vic_file = os.path.join(pdir, sample["victim_file_rel"])
    pol_file = os.path.join(pdir, sample["polluter_file_rel"]) if sample.get("polluter_file_rel") else vic_file
    same_file = (os.path.abspath(vic_file) == os.path.abspath(pol_file))

    orig_vic_b = open(vic_file, "rb").read()
    orig_pol_b = orig_vic_b if same_file else open(pol_file, "rb").read()
    orig_vic_s = orig_vic_b.decode("utf-8", errors="replace")
    orig_pol_s = orig_pol_b.decode("utf-8", errors="replace")

    res: Dict[str, Any] = {
        "harness_version": HARNESS_VERSION,
        "compiled": False,
        "compile_error": "",
        "compile_sec": 0.0,
        "w_plus_passed": False,
        "w_plus_diag": "",
        "w_plus_sec": 0.0,
        "pit_ran": False,
        "target_pit_sec": 0.0,
        "partner_pit_sec": 0.0,
        "pit_sec": 0.0,
        "scope_kind": "TARGET_ONLY",
        "affects_partner_or_shared": False,
        "gate_status_override": None,
        "gate_safe": False,
        "eroded": False,
        "sound_fix": False,
        "target_m_det_total": len(sample["m_det_exec"]),
        "target_m_det_killed": 0,
        "target_retention": None,
        "partner_m_det_total": len(sample.get("polluter_m_det_exec") or []),
        "partner_m_det_killed": None,
        "partner_retention": None,
        "joint_m_det_total": len(sample["m_det_exec"]),
        "joint_m_det_killed": 0,
        "joint_retention": None,
        "retention_m_det": None,
        "survived_mutants": [],
        "m_gold_total": 0,
        "m_gold_killed": 0,
        "retention_m_gold": None,
        "applied_diff_sha256": "",
        "total_eval_sec": 0.0,
    }

    try:
        t0_c = time.time()
        vcode = candidate.get("victim_code") or candidate.get("test_code")
        pcode = candidate.get("polluter_code")
        if not vcode and not pcode:
            res["compile_error"] = "Missing candidate Python test function code."
            return res

        new_vic_s = orig_vic_s
        new_pol_s = orig_pol_s
        p_changed = is_code_semantically_changed("python", sample.get("orig_polluter_code", ""), pcode)
        if same_file:
            ns = orig_vic_s
            if vcode:
                ns = rpc.replace_py_func(ns, sample.get("victim_class"), sample["victim_method"], vcode)
            if p_changed and sample.get("polluter_method") and sample["polluter_method"] != sample["victim_method"]:
                ns = rpc.replace_py_func(ns, sample.get("polluter_class"), sample["polluter_method"], pcode)
            ns = rpc.inject_py_imports(ns, candidate.get("imports") or [])
            compile(ns, sample["victim_file_rel"], "exec")
            new_vic_s = ns
            new_pol_s = ns
            open(vic_file, "w", encoding="utf-8").write(ns)
        else:
            if vcode:
                nv = rpc.replace_py_func(orig_vic_s, sample.get("victim_class"), sample["victim_method"], vcode)
                nv = rpc.inject_py_imports(nv, candidate.get("imports") or [])
                compile(nv, sample["victim_file_rel"], "exec")
                new_vic_s = nv
                open(vic_file, "w", encoding="utf-8").write(nv)
            if p_changed:
                np = rpc.replace_py_func(orig_pol_s, sample.get("polluter_class"), sample["polluter_method"], pcode)
                np = rpc.inject_py_imports(np, candidate.get("imports") or [])
                compile(np, sample["polluter_file_rel"], "exec")
                new_pol_s = np
                open(pol_file, "w", encoding="utf-8").write(np)

        res["compiled"] = True
        res["compile_sec"] = round(time.time() - t0_c, 2)
        for tf in {vic_file, pol_file}:
            pc = os.path.join(os.path.dirname(tf), "__pycache__")
            if os.path.isdir(pc):
                shutil.rmtree(pc, ignore_errors=True)

        diff_txt = compute_unified_diff(orig_vic_s, new_vic_s, sample["victim_file_rel"])
        if not same_file:
            diff_txt += compute_unified_diff(orig_pol_s, new_pol_s, sample["polluter_file_rel"])
        res["applied_diff_sha256"] = hashlib.sha256(diff_txt.encode("utf-8")).hexdigest()[:20]

        act_orig_t = rpc.extract_py_func(orig_vic_s, sample.get("victim_class"), sample["victim_method"]) or sample["orig_test_code"]
        act_new_t = rpc.extract_py_func(new_vic_s, sample.get("victim_class"), sample["victim_method"]) or (vcode or sample["orig_test_code"])
        act_orig_p = (
            rpc.extract_py_func(orig_pol_s, sample.get("polluter_class"), sample["polluter_method"])
            if sample.get("polluter_method") else ""
        ) or sample.get("orig_polluter_code", "")
        act_new_p = (
            rpc.extract_py_func(new_pol_s, sample.get("polluter_class"), sample["polluter_method"])
            if sample.get("polluter_method") else ""
        ) or (pcode or sample.get("orig_polluter_code", ""))

        scope = analyze_affected_test_scope(
            language="python",
            test_type="OD",
            orig_vic_file_src=orig_vic_s,
            new_vic_file_src=new_vic_s,
            orig_pol_file_src=orig_pol_s,
            new_pol_file_src=new_pol_s,
            same_file=same_file,
            orig_target_method_src=act_orig_t,
            new_target_method_src=act_new_t,
            orig_partner_method_src=act_orig_p,
            new_partner_method_src=act_new_p,
        )
        res["scope_kind"] = scope["scope_kind"]
        res["affects_partner_or_shared"] = scope["affects_partner_or_shared"]

        w_ok, w_diag, dt_w = rpc.check_py_stability_w_plus(pdir, sample["test_full_name"], sample["polluter_full_name"])
        res["w_plus_passed"] = w_ok
        res["w_plus_diag"] = w_diag
        res["w_plus_sec"] = round(dt_w, 2)
        if _check_and_mark_infra(res, None, w_diag, "py_w_plus"):
            res["w_plus_passed"] = False
            return res
        if not w_ok:
            return res
        rc.reset_infra_trace()
        _cand_py_rels = {
            sample["victim_file_rel"].replace("\\", "/"),
            (sample.get("polluter_file_rel") or sample["victim_file_rel"]).replace("\\", "/"),
        }
        _restore_runtime_py_fixtures(pdir, _cand_py_rels)

        LAST_PY_MUT_DIAG.pop(threading.get_ident(), None)
        t_ok, t_map, dt_t = run_attributed_py_mut(pdir, [sample["test_full_name"]], sample["test_full_name"], sample["m_det_exec"], timeout=115)
        rc_tm, diag_tm = LAST_PY_MUT_DIAG.pop(threading.get_ident(), (None, ""))
        res["target_pit_sec"] = dt_t
        res["pit_sec"] = dt_t
        if _check_and_mark_infra(res, rc_tm, diag_tm, "py_target_mut"):
            res["pit_ran"] = False
            res["w_plus_diag"] += f"\n[TARGET_PY_MUT_INFRA_ERROR]: {diag_tm[-400:]}"
            return res
        if not t_ok:
            res["pit_ran"] = False
            res["w_plus_diag"] += f"\n[TARGET_PY_MUT_ERROR]: {diag_tm[-400:]}"
            return res
        rc.reset_infra_trace()

        t_killed = {mid for mid, inf in t_map.items() if inf.get("killed")}
        t_unknown_ids = [
            m["id"] for m in sample["m_det"]
            if (m["id"] not in t_map) or t_map[m["id"]].get("is_unknown")
        ]
        res["target_unknown_count"] = len(t_unknown_ids)
        res["target_unknown_ids"] = t_unknown_ids[:10]

        p_ran, p_ok, p_killed = False, False, None
        p_unknown_ids: List[str] = []
        pol_exec = sample.get("polluter_m_det_exec") or []
        if scope["affects_partner_or_shared"] and len(pol_exec) > 0:
            p_ran = True
            LAST_PY_MUT_DIAG.pop(threading.get_ident(), None)
            p_ok, p_map, dt_p = run_attributed_py_mut(pdir, [sample["polluter_full_name"]], sample["polluter_full_name"], pol_exec, timeout=95)
            rc_pm, diag_pm = LAST_PY_MUT_DIAG.pop(threading.get_ident(), (None, ""))
            res["partner_pit_sec"] = dt_p
            res["pit_sec"] = round(dt_t + dt_p, 2)
            if _check_and_mark_infra(res, rc_pm, diag_pm, "py_partner_mut"):
                res["w_plus_diag"] += f"\n[PARTNER_PY_MUT_INFRA_ERROR]: {diag_pm[-400:]}"
                return res
            if p_ok:
                p_killed = {pid for pid, pinf in p_map.items() if pinf.get("killed")}
                p_unknown_ids = [
                    pm["id"] for pm in (sample.get("polluter_m_det") or [])
                    if (pm["id"] not in p_map) or p_map[pm["id"]].get("is_unknown")
                ]
            else:
                res["w_plus_diag"] += f"\n[PARTNER_PY_MUT_ERROR]: {diag_pm[-400:]}"
        res["partner_unknown_count"] = len(p_unknown_ids)
        res["partner_unknown_ids"] = p_unknown_ids[:10]

        ret_info = compute_role_tagged_retention(
            target_m_det=sample["m_det"],
            target_killed_ids=t_killed,
            test_type="OD",
            partner_m_det=sample.get("polluter_m_det") or [],
            affects_partner=scope["affects_partner_or_shared"],
            partner_eval_ran=p_ran,
            partner_eval_ok=p_ok,
            partner_killed_ids=p_killed,
            target_unknown_ids=t_unknown_ids,
            partner_unknown_ids=p_unknown_ids,
        )
        if t_unknown_ids:
            ret_info["gate_safe"] = False
            ret_info["gate_status_override"] = ret_info["gate_status_override"] or "TARGET_MUT_UNKNOWN"
        elif p_unknown_ids:
            ret_info["gate_safe"] = False
            ret_info["gate_status_override"] = ret_info["gate_status_override"] or "PARTNER_MUT_UNKNOWN"
        res.update(ret_info)
        res["pit_ran"] = (ret_info["gate_status_override"] not in ("PARTNER_MUT_EVAL_ERROR", "TARGET_MUT_UNKNOWN", "PARTNER_MUT_UNKNOWN"))
        res["retention_m_det"] = ret_info["joint_retention"]
        res["sound_fix"] = bool(res["w_plus_passed"] and ret_info["gate_safe"])
        return res
    except SyntaxError as se:
        res["compile_error"] = f"SyntaxError: {se}"
        return res
    except Exception as ex:
        res["compile_error"] = str(ex)
        if isinstance(ex, (OSError, subprocess.SubprocessError, TimeoutError, MemoryError)):
            res["infra_error"] = True
            res["infra_error_detail"] = f"{type(ex).__name__}: {ex}"
        return res
    finally:
        open(vic_file, "wb").write(orig_vic_b)
        if not same_file:
            open(pol_file, "wb").write(orig_pol_b)
        _restore_runtime_py_fixtures(pdir, set())
        for tf in {vic_file, pol_file}:
            pc = os.path.join(os.path.dirname(tf), "__pycache__")
            if os.path.isdir(pc):
                shutil.rmtree(pc, ignore_errors=True)
        res["total_eval_sec"] = round(time.time() - t0, 2)


def evaluate_post_freeze_heldout(sample: Dict[str, Any], candidate: Dict[str, Any]) -> Dict[str, Any]:
    """
    Post-freeze external validation (`W+_heldout` + frozen `M_ext` pools).
    NEVER called inside any repair loop.
    """
    rc.reset_infra_trace()
    lang = sample.get("language", "java")
    t0 = time.time()
    if lang == "java":
        pdir = sample["proj_dir"]
        mod_rel = sample["module"]
        jdk = sample["jdk"]
        ttype = sample["test_type"]
        pit_pair = tuple(sample["pit_pair"])
        mod_dir = pdir if mod_rel in ("", ".") else os.path.join(pdir, mod_rel)
        pom_path = os.path.join(mod_dir, "pom.xml")
        vic_file = sample["victim_file_path"]
        pol_file = sample.get("polluter_file_path") or vic_file
        same_file = (os.path.abspath(vic_file) == os.path.abspath(pol_file))

        orig_vic_b = open(vic_file, "rb").read()
        orig_pol_b = orig_vic_b if same_file else open(pol_file, "rb").read()
        orig_pom_b = open(pom_path, "rb").read()
        orig_vic = re.sub(r"/\*FT1_ISOLATED\s*(.*?)\s*\*/", r"\1", orig_vic_b.decode("utf-8", errors="replace"))
        orig_pol = (
            orig_vic if same_file
            else re.sub(r"/\*FT1_ISOLATED\s*(.*?)\s*\*/", r"\1", orig_pol_b.decode("utf-8", errors="replace"))
        )
        orig_pom = orig_pom_b.decode("utf-8", errors="replace")
        vbak = rc.backup_class_files(mod_dir, sample["victim_fqcn"])
        pbak = rc.backup_class_files(mod_dir, sample["polluter_fqcn"]) if (sample.get("polluter_fqcn") and sample["polluter_fqcn"] != sample["victim_fqcn"]) else {}
        try:
            p_changed_java = False
            if ttype == "ID":
                nv = rc.replace_java_method(orig_vic, sample["victim_method"], candidate["test_code"])
                nv = rc.inject_java_imports(nv, candidate.get("imports") or [])
                open(vic_file, "w", encoding="utf-8").write(nv)
            else:
                vcode, pcode = candidate.get("victim_code"), candidate.get("polluter_code")
                p_changed = is_code_semantically_changed("java", sample.get("orig_polluter_code", ""), pcode)
                p_changed_java = bool(p_changed)
                if same_file:
                    ns = orig_vic
                    if vcode:
                        ns = rc.replace_java_method(ns, sample["victim_method"], vcode)
                    if p_changed and sample["polluter_method"] != sample["victim_method"]:
                        ns = rc.replace_java_method(ns, sample["polluter_method"], pcode)
                    elif pcode and not vcode:
                        ns = rc.replace_java_method(ns, sample["polluter_method"], pcode)
                    ns = rc.inject_java_imports(ns, candidate.get("imports") or [])
                    open(vic_file, "w", encoding="utf-8").write(ns)
                else:
                    if vcode:
                        nv = rc.inject_java_imports(rc.replace_java_method(orig_vic, sample["victim_method"], vcode), candidate.get("imports") or [])
                        open(vic_file, "w", encoding="utf-8").write(nv)
                    if p_changed:
                        np = rc.inject_java_imports(rc.replace_java_method(orig_pol, sample["polluter_method"], pcode), candidate.get("imports") or [])
                        open(pol_file, "w", encoding="utf-8").write(np)
            pom_mod = False
            if candidate.get("pom"):
                new_pom = rc.inject_pom_dependencies(orig_pom, candidate["pom"])
                if new_pom != orig_pom:
                    pom_mod = True
                    open(pom_path, "w", encoding="utf-8").write(new_pom)

            pl = f"-pl {mod_rel}" if mod_rel not in ("", ".") else ""
            extra_cp = "dependency:build-classpath -Dmdep.outputFile=target/ft1_cp.txt" if pom_mod else ""
            rc_hc, out_hc, _ = rc.docker_mvn(pdir, jdk, f"test-compile {extra_cp} {pl}", timeout=115)
            ho_c_probe: Dict[str, Any] = {}
            if _check_and_mark_infra(ho_c_probe, rc_hc, out_hc, "heldout_java_compile", is_compile=(rc_hc != 0 or "BUILD SUCCESS" not in out_hc)):
                ho_c_probe["harness_version"] = HARNESS_VERSION
                ho_c_probe["w_plus_heldout_passed"] = False
                ho_c_probe["w_plus_heldout_passed_strict"] = False
                ho_c_probe["w_plus_heldout_diag"] = out_hc[-500:]
                return ho_c_probe
            if rc_hc != 0 or "BUILD SUCCESS" not in out_hc:
                return {
                    "harness_version": HARNESS_VERSION,
                    "w_plus_heldout_passed": False,
                    "w_plus_heldout_passed_strict": False,
                    "w_plus_heldout_passed_target_stress": False,
                    "w_plus_heldout_diag": out_hc[-500:],
                    "m_ext_applicable": bool((sample.get("m_ext_op_value_flow") or []) or (sample.get("m_ext_op_call") or [])),
                    "m_ext_eval_ok": False,
                    "m_ext_runner_ok": False,
                    "total_sec": round(time.time() - t0, 2),
                }
            rc.reset_infra_trace()
            j_ctrl = sample.get("upstream_repeated_control") or {}
            upstream_nonidemp_p2_java = bool(
                (not j_ctrl.get("is_unknown"))
                and j_ctrl.get("upstream_nonidempotent_p2_call_fail_only")
            )
            allow_stress_p2_java = bool(upstream_nonidemp_p2_java and not p_changed_java)
            expected_p2_exc_java = j_ctrl.get("p2_call_exception_type") if allow_stress_p2_java else None
            w_ho_strict, w_ho_diag, dt_who, w_ho_scheds = check_java_w_plus_heldout_detailed(
                sample,
                allow_upstream_nonidempotent_p2_call_fail=allow_stress_p2_java,
                expected_p2_call_exc=expected_p2_exc_java,
            )
            ho_w_probe: Dict[str, Any] = {}
            if _check_and_mark_infra(ho_w_probe, None, w_ho_diag, "heldout_java_w_plus"):
                ho_w_probe["harness_version"] = HARNESS_VERSION
                return ho_w_probe
            rc.reset_infra_trace()
            w_ho_stress = bool(w_ho_scheds.get("w_plus_heldout_passed_target_stress", w_ho_strict))
            if not allow_stress_p2_java:
                w_ho_stress = bool(w_ho_strict)

            vf_pool = sample.get("m_ext_op_value_flow") or []
            cl_pool = sample.get("m_ext_op_call") or []
            if not vf_pool and not cl_pool:
                return {
                    "harness_version": HARNESS_VERSION,
                    "w_plus_heldout_passed": w_ho_strict,
                    "w_plus_heldout_passed_strict": w_ho_strict,
                    "w_plus_heldout_passed_target_stress": w_ho_stress,
                    "partner_modified_by_candidate": p_changed_java,
                    "w_plus_heldout_diag": w_ho_diag[:300],
                    "w_plus_heldout_schedules": w_ho_scheds,
                    "w_plus_heldout_sec": dt_who,
                    "m_ext_applicable": False,
                    "m_ext_eval_ok": True,
                    "m_ext_runner_ok": True,
                    "op_value_flow_total": 0,
                    "op_value_flow_killed": None,
                    "op_value_flow_unknown": 0,
                    "op_value_flow_retention": None,
                    "op_call_total": 0,
                    "op_call_killed": None,
                    "op_call_unknown": 0,
                    "op_call_retention": None,
                    "m_ext_all_total": 0,
                    "m_ext_all_killed": None,
                    "m_ext_all_unknown": 0,
                    "m_ext_all_retention": None,
                    "total_sec": round(time.time() - t0, 2),
                }

            mut_flag_ext = sample.get("java_mext_mutators_used") or "ALL"
            ok_all, muts_all, dt_all, pit_tail = run_attributed_java_pit(
                proj_dir=pdir, mod_rel=mod_rel, jdk=jdk,
                test_file_path=vic_file, test_fqcn=sample["victim_fqcn"],
                test_method=sample["victim_method"], focal_classes=sample["focal_classes"],
                pit_pair=pit_pair, fqcn_to_path=sample.get("fqcn_to_path"),
                mutators_arg=mut_flag_ext, timeout=155,
            )
            ho_j_probe1: Dict[str, Any] = {}
            if _check_and_mark_infra(ho_j_probe1, None, pit_tail, "heldout_java_mext"):
                ho_j_probe1["harness_version"] = HARNESS_VERSION
                return ho_j_probe1
            if not ok_all and mut_flag_ext == "ALL":
                ok_all, muts_all, dt_all2, pit_tail = run_attributed_java_pit(
                    proj_dir=pdir, mod_rel=mod_rel, jdk=jdk,
                    test_file_path=vic_file, test_fqcn=sample["victim_fqcn"],
                    test_method=sample["victim_method"], focal_classes=sample["focal_classes"],
                    pit_pair=pit_pair, fqcn_to_path=sample.get("fqcn_to_path"),
                    mutators_arg="STRONGER,EXPERIMENTAL_SWITCH", timeout=155,
                )
                dt_all = round(dt_all + dt_all2, 2)
                ho_j_probe2: Dict[str, Any] = {}
                if _check_and_mark_infra(ho_j_probe2, None, pit_tail, "heldout_java_mext_fallback"):
                    ho_j_probe2["harness_version"] = HARNESS_VERSION
                    return ho_j_probe2
            if not ok_all:
                ho_j_fail: Dict[str, Any] = {
                    "harness_version": HARNESS_VERSION,
                    "w_plus_heldout_passed": w_ho_strict,
                    "w_plus_heldout_passed_strict": w_ho_strict,
                    "w_plus_heldout_passed_target_stress": w_ho_stress,
                    "partner_modified_by_candidate": p_changed_java,
                    "w_plus_heldout_diag": w_ho_diag[:300],
                    "w_plus_heldout_schedules": w_ho_scheds,
                    "m_ext_applicable": True,
                    "m_ext_eval_ok": False,
                    "m_ext_runner_ok": False,
                    "m_ext_all_total": len(vf_pool) + len(cl_pool),
                    "m_ext_all_unknown": len(vf_pool) + len(cl_pool),
                    "m_ext_err_tail": pit_tail[-300:],
                    "total_sec": round(time.time() - t0, 2),
                }
                _check_and_mark_infra(ho_j_fail, None, pit_tail, "heldout_java_mext")
                return ho_j_fail
            # Lossless 8-field exact_id matching (zero 4-tuple collision; conflicting duplicates -> UNKNOWN!)
            by_eid, status_by_eid = resolve_pit_mutants_by_exact_id(muts_all)

            def _eval_pool(pool: List[Dict[str, Any]]) -> Tuple[int, List[Dict[str, Any]], List[Dict[str, Any]]]:
                k_cnt = 0
                unk_list: List[Dict[str, Any]] = []
                lost_list: List[Dict[str, Any]] = []
                for m in pool:
                    eid = m.get("exact_id") or java_pit_mutant_exact_id(m)
                    rm = by_eid.get(eid)
                    if rm is None:
                        unk_list.append(m)
                        lost_list.append({"id": m["id"], "exact_id": eid, "mutator": m["mutator_short"], "line": m["lineNumber"], "desc": m["description"], "status": "MISSING_EXACT_ID"})
                    elif rm.get("is_unknown"):
                        unk_list.append(m)
                        lost_list.append({"id": m["id"], "exact_id": eid, "mutator": m["mutator_short"], "line": m["lineNumber"], "desc": m["description"], "status": status_by_eid.get(eid, "UNKNOWN")})
                    elif rm.get("attributed_kill"):
                        k_cnt += 1
                    else:
                        lost_list.append({"id": m["id"], "exact_id": eid, "mutator": m["mutator_short"], "line": m["lineNumber"], "desc": m["description"], "status": status_by_eid.get(eid, "SURVIVED")})
                return k_cnt, unk_list, lost_list

            vf_k, vf_unk, lost_vf = _eval_pool(vf_pool)
            cl_k, cl_unk, lost_cl = _eval_pool(cl_pool)
            tot_ext = len(vf_pool) + len(cl_pool)
            tot_k = vf_k + cl_k
            tot_unk = len(vf_unk) + len(cl_unk)
            return {
                "harness_version": HARNESS_VERSION,
                "match_key_schema": "exact_8tuple",
                "w_plus_heldout_passed": w_ho_strict,
                "w_plus_heldout_passed_strict": w_ho_strict,
                "w_plus_heldout_passed_target_stress": w_ho_stress,
                "partner_modified_by_candidate": p_changed_java,
                "w_plus_heldout_diag": w_ho_diag[:500],
                "w_plus_heldout_schedules": w_ho_scheds,
                "w_plus_heldout_sec": dt_who,
                "m_ext_applicable": True,
                "m_ext_runner_ok": True,
                "m_ext_eval_ok": (tot_unk == 0),
                "m_ext_sec": dt_all,
                "op_value_flow_total": len(vf_pool),
                "op_value_flow_killed": vf_k,
                "op_value_flow_unknown": len(vf_unk),
                "op_value_flow_retention": round(vf_k / len(vf_pool), 4) if vf_pool else None,
                "op_call_total": len(cl_pool),
                "op_call_killed": cl_k,
                "op_call_unknown": len(cl_unk),
                "op_call_retention": round(cl_k / len(cl_pool), 4) if cl_pool else None,
                "m_ext_all_total": tot_ext,
                "m_ext_all_killed": tot_k,
                "m_ext_all_unknown": tot_unk,
                "m_ext_unknown_ids": [m["id"] for m in (vf_unk + cl_unk)][:10],
                "m_ext_unknown_statuses": {
                    m["id"]: status_by_eid.get(m.get("exact_id") or java_pit_mutant_exact_id(m), "MISSING_EXACT_ID")
                    for m in (vf_unk + cl_unk)[:10]
                },
                "m_ext_all_retention": round(tot_k / tot_ext, 4) if tot_ext else None,
                "lost_value_flow_mutants": lost_vf,
                "lost_call_mutants": lost_cl,
                "total_sec": round(time.time() - t0, 2),
            }
        finally:
            open(vic_file, "wb").write(orig_vic_b)
            if not same_file:
                open(pol_file, "wb").write(orig_pol_b)
            open(pom_path, "wb").write(orig_pom_b)
            rc.restore_class_files(mod_dir, sample["victim_fqcn"], vbak)
            if pbak:
                rc.restore_class_files(mod_dir, sample["polluter_fqcn"], pbak)
    else:
        pdir = sample["proj_dir"]
        vic_file = os.path.join(pdir, sample["victim_file_rel"])
        pol_file = os.path.join(pdir, sample["polluter_file_rel"]) if sample.get("polluter_file_rel") else vic_file
        same_file = (os.path.abspath(vic_file) == os.path.abspath(pol_file))
        orig_vb = open(vic_file, "rb").read()
        orig_pb = orig_vb if same_file else open(pol_file, "rb").read()
        orig_vs = orig_vb.decode("utf-8", errors="replace")
        orig_ps = orig_pb.decode("utf-8", errors="replace")
        try:
            vcode = candidate.get("victim_code") or candidate.get("test_code")
            pcode = candidate.get("polluter_code")
            p_changed_py = bool(is_code_semantically_changed("python", sample.get("orig_polluter_code", ""), pcode))
            if same_file:
                ns = orig_vs
                if vcode:
                    ns = rpc.replace_py_func(ns, sample.get("victim_class"), sample["victim_method"], vcode)
                if p_changed_py and sample.get("polluter_method") and sample["polluter_method"] != sample["victim_method"]:
                    ns = rpc.replace_py_func(ns, sample.get("polluter_class"), sample["polluter_method"], pcode)
                ns = rpc.inject_py_imports(ns, candidate.get("imports") or [])
                open(vic_file, "w", encoding="utf-8").write(ns)
            else:
                if vcode:
                    open(vic_file, "w", encoding="utf-8").write(
                        rpc.inject_py_imports(rpc.replace_py_func(orig_vs, sample.get("victim_class"), sample["victim_method"], vcode), candidate.get("imports") or [])
                    )
                if p_changed_py:
                    open(pol_file, "w", encoding="utf-8").write(
                        rpc.inject_py_imports(rpc.replace_py_func(orig_ps, sample.get("polluter_class"), sample["polluter_method"], pcode), candidate.get("imports") or [])
                    )

            py_ctrl = sample.get("upstream_repeated_control") or {}
            upstream_nonidemp_p2 = bool(
                (not py_ctrl.get("is_unknown"))
                and py_ctrl.get("upstream_nonidempotent_p2_call_fail_only")
            )
            allow_stress_p2 = (upstream_nonidemp_p2 and not p_changed_py)
            w_ho_strict, w_ho_diag, dt_who, w_ho_scheds = check_py_w_plus_heldout_detailed(
                pdir, sample["test_full_name"], sample["polluter_full_name"],
                allow_upstream_nonidempotent_p2_call_fail=allow_stress_p2,
            )
            ho_py_w_probe: Dict[str, Any] = {}
            if _check_and_mark_infra(ho_py_w_probe, None, w_ho_diag, "heldout_py_w_plus"):
                ho_py_w_probe["harness_version"] = HARNESS_VERSION
                return ho_py_w_probe
            rc.reset_infra_trace()
            _ho_py_rels = {
                sample["victim_file_rel"].replace("\\", "/"),
                (sample.get("polluter_file_rel") or sample["victim_file_rel"]).replace("\\", "/"),
            }
            _restore_runtime_py_fixtures(pdir, _ho_py_rels)
            w_ho_stress = bool(w_ho_scheds.get("w_plus_heldout_passed_target_stress", w_ho_strict))
            op_pool = sample.get("m_ext_operator_exec") or []
            in_pool = sample.get("m_ext_instance_exec") or []
            comb = op_pool + in_pool
            if not comb:
                return {
                    "harness_version": HARNESS_VERSION,
                    "w_plus_heldout_passed": w_ho_strict,
                    "w_plus_heldout_passed_strict": w_ho_strict,
                    "w_plus_heldout_passed_target_stress": w_ho_stress,
                    "partner_modified_by_candidate": p_changed_py,
                    "w_plus_heldout_diag": w_ho_diag[:350],
                    "w_plus_heldout_schedules": w_ho_scheds,
                    "w_plus_heldout_sec": dt_who,
                    "m_ext_applicable": False,
                    "m_ext_runner_ok": True,
                    "m_ext_eval_ok": True,
                    "m_ext_op_total": 0,
                    "m_ext_op_killed": None,
                    "m_ext_op_retention": None,
                    "m_ext_inst_total": 0,
                    "m_ext_inst_killed": None,
                    "m_ext_inst_retention": None,
                    "m_ext_all_total": 0,
                    "m_ext_all_killed": None,
                    "m_ext_all_unknown": 0,
                    "m_ext_all_retention": None,
                    "total_sec": round(time.time() - t0, 2),
                }
            LAST_PY_MUT_DIAG.pop(threading.get_ident(), None)
            ok_x, dmap, dt_x = run_attributed_py_mut(pdir, [sample["test_full_name"]], sample["test_full_name"], comb, timeout=125)
            rc_xm, diag_xm = LAST_PY_MUT_DIAG.pop(threading.get_ident(), (None, ""))
            ho_py_mext_probe: Dict[str, Any] = {}
            if _check_and_mark_infra(ho_py_mext_probe, rc_xm, diag_xm, "heldout_py_mext"):
                ho_py_mext_probe["harness_version"] = HARNESS_VERSION
                return ho_py_mext_probe
            if not ok_x:
                ho_py_fail: Dict[str, Any] = {
                    "harness_version": HARNESS_VERSION,
                    "w_plus_heldout_passed": w_ho_strict,
                    "w_plus_heldout_passed_strict": w_ho_strict,
                    "w_plus_heldout_passed_target_stress": w_ho_stress,
                    "partner_modified_by_candidate": p_changed_py,
                    "w_plus_heldout_diag": w_ho_diag[:350],
                    "w_plus_heldout_schedules": w_ho_scheds,
                    "m_ext_applicable": True,
                    "m_ext_runner_ok": False,
                    "m_ext_eval_ok": False,
                    "m_ext_all_total": len(comb),
                    "m_ext_all_unknown": len(comb),
                    "m_ext_err_tail": diag_xm[-350:],
                    "total_sec": round(time.time() - t0, 2),
                }
                _check_and_mark_infra(ho_py_fail, rc_xm, diag_xm, "heldout_py_mext")
                return ho_py_fail
            op_k = sum(1 for m in op_pool if dmap.get(m["id"], {}).get("killed"))
            op_unk = [m for m in op_pool if (m["id"] not in dmap) or dmap[m["id"]].get("is_unknown")]
            in_k = sum(1 for m in in_pool if dmap.get(m["id"], {}).get("killed"))
            in_unk = [m for m in in_pool if (m["id"] not in dmap) or dmap[m["id"]].get("is_unknown")]
            assert_pool = [m for m in comb if m.get("kill_kind") == "KILLED_CALL_ASSERT"]
            assert_k = sum(1 for m in assert_pool if dmap.get(m["id"], {}).get("killed"))
            assert_unk = [m for m in assert_pool if (m["id"] not in dmap) or dmap[m["id"]].get("is_unknown")]
            tot_k = op_k + in_k
            tot_unk = len(op_unk) + len(in_unk)
            lost_op = [
                {
                    "id": m["id"],
                    "mutator": m["mutator"],
                    "line": m["lineNumber"],
                    "desc": m["description"],
                    "status": dmap.get(m["id"], {}).get("status", "MISSING"),
                }
                for m in op_pool if not dmap.get(m["id"], {}).get("killed")
            ]
            lost_inst = [
                {
                    "id": m["id"],
                    "mutator": m["mutator"],
                    "line": m["lineNumber"],
                    "desc": m["description"],
                    "status": dmap.get(m["id"], {}).get("status", "MISSING"),
                }
                for m in in_pool if not dmap.get(m["id"], {}).get("killed")
            ]
            return {
                "harness_version": HARNESS_VERSION,
                "w_plus_heldout_passed": w_ho_strict,
                "w_plus_heldout_passed_strict": w_ho_strict,
                "w_plus_heldout_passed_target_stress": w_ho_stress,
                "partner_modified_by_candidate": p_changed_py,
                "w_plus_heldout_diag": w_ho_diag[:350],
                "w_plus_heldout_schedules": w_ho_scheds,
                "w_plus_heldout_sec": dt_who,
                "m_ext_applicable": True,
                "m_ext_runner_ok": True,
                "m_ext_eval_ok": (tot_unk == 0),
                "m_ext_sec": dt_x,
                "m_ext_op_total": len(op_pool),
                "m_ext_op_killed": op_k,
                "m_ext_op_unknown": len(op_unk),
                "m_ext_op_retention": round(op_k / len(op_pool), 4) if op_pool else None,
                "m_ext_inst_total": len(in_pool),
                "m_ext_inst_killed": in_k,
                "m_ext_inst_unknown": len(in_unk),
                "m_ext_inst_retention": round(in_k / len(in_pool), 4) if in_pool else None,
                "m_ext_assert_only_total": len(assert_pool),
                "m_ext_assert_only_killed": assert_k,
                "m_ext_assert_only_unknown": len(assert_unk),
                "m_ext_assert_only_retention": round(assert_k / len(assert_pool), 4) if assert_pool else None,
                "m_ext_all_total": len(comb),
                "m_ext_all_killed": tot_k,
                "m_ext_all_unknown": tot_unk,
                "m_ext_unknown_ids": [m["id"] for m in (op_unk + in_unk)][:10],
                "m_ext_unknown_statuses": {
                    m["id"]: dmap.get(m["id"], {}).get("status", "MISSING")
                    for m in (op_unk + in_unk)[:10]
                },
                "m_ext_all_retention": round(tot_k / len(comb), 4) if comb else None,
                "lost_op_mutants": lost_op,
                "lost_inst_mutants": lost_inst,
                "total_sec": round(time.time() - t0, 2),
            }
        finally:
            open(vic_file, "wb").write(orig_vb)
            if not same_file:
                open(pol_file, "wb").write(orig_pb)
            _restore_runtime_py_fixtures(pdir, set())
            for tf in {vic_file, pol_file}:
                pc = os.path.join(os.path.dirname(tf), "__pycache__")
                if os.path.isdir(pc):
                    shutil.rmtree(pc, ignore_errors=True)


def main() -> None:
    """
    CLI: python3 remote_eval_v2.py <mode: in_loop_gate | post_freeze_heldout> <sample_id> <b64_candidate>
    Loads `/root/sqj_work/FT1/full_runs/v2_frozen_benchmark_exec.json`.
    """
    mode = sys.argv[1].strip()
    sid = sys.argv[2].strip()
    b64_cand = sys.argv[3].strip()

    sid_hash = hashlib.sha1(sid.encode("utf-8")).hexdigest()[:20]
    per_sample_p = f"/root/sqj_work/FT1/full_runs/v2_samples/{sid_hash}.json"
    exec_p = "/root/sqj_work/FT1/full_runs/v2_frozen_benchmark_exec.json"
    light_p = "/root/sqj_work/FT1/full_runs/v2_frozen_benchmark_light.json"
    expected_sha = ""
    if os.path.exists(light_p):
        try:
            expected_sha = json.load(open(light_p, encoding="utf-8")).get("exec_benchmark_sha256", "")
        except Exception:
            expected_sha = ""
    sample = None
    if os.path.exists(per_sample_p):
        cand_s = json.load(open(per_sample_p, encoding="utf-8"))
        cached_sha = cand_s.get("_frozen_exec_sha256", "")
        if not expected_sha or cached_sha == expected_sha:
            sample = cand_s
    if sample is None:
        bench = json.load(open(exec_p, encoding="utf-8"))
        sample = next((s for s in bench["samples"] if s["sample_id"] == sid), None)
    if sample is None:
        raise RuntimeError(f"Sample {sid} not found in v2_frozen_benchmark_exec.json")

    cand = json.loads(base64.b64decode(b64_cand.encode("ascii")).decode("utf-8"))
    if mode == "post_freeze_heldout":
        ev = evaluate_post_freeze_heldout(sample, cand)
    else:
        if sample["language"] == "python":
            ev = evaluate_py_v2_in_loop(sample, cand)
        else:
            ev = evaluate_java_v2_in_loop(sample, cand)

    print("===FT1_V2_EVAL_START===")
    print(json.dumps(ev, ensure_ascii=False))
    print("===FT1_V2_EVAL_END===")


if __name__ == "__main__":
    main()
