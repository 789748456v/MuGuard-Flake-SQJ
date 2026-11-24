"""
Remote Phase B-v2.3 (`2.3.0-certified`): Closes ALL 5 Codex Round-5 MUST-FIX findings (`R5-1`–`R5-5`):
  - Stage 1 (`R5-1`): 100% fresh `3/3` clean-`e_0` PIT re-freeze across ALL 110 eligible Java samples
    (target `ALL` with `DEFAULTS,STRONGER,EXPERIMENTAL_SWITCH` fallback + OD partner `DEFAULTS`),
    zero label inheritance, zero `multi_site_disambiguated`, zero `attribution_granularity == "NONE"`,
    explicit `runs_3x_statuses`, `runs_3x_granularities`, `runs_3x_killing_tests` on EVERY admitted mutant,
    plus exact old-vs-new pool diff report (`v23_stage1_pool_diff.json`).
  - Stage 2 (`R5-4`): Matched unmodified upstream repo controls (`partner -> partner -> target`) across
    ALL 93 Python OD + 49 Java OD samples (`v23_upstream_repeated_pollution_controls.json`).
  - Stage 3 (`R5-3` + `R5-4`): Exact nodeid & full `setup/call/teardown` lifecycle held-out refresh
    across ALL Python OD (`480`) and Java OD (`257`) held-out entries, recording BOTH
    `w_plus_heldout_passed_strict` (primary) AND `w_plus_heldout_passed_target_stress` (matched-control sensitivity).
  - Stage 4 (`R5-5`): Strict ALL-OR-NOTHING atomic same-tree PR full dual-role gate across all Java PRs
    (abort on any failed file apply with zero fallback, single `applied_tree_sha256`, `extra_files_edited > 0`
    folded into `affects_partner_or_shared`, `W+` + target `3x` PIT + partner `3x` PIT all on the IDENTICAL tree).
  - Stage 5 (`R5-2`): Unified `2.3.0-certified` B1 replay across ALL Java `FlakyDoctor` (GPT & Magicoder)
    and ALL Python `iPFlakies` entries against Stage 1's fresh `3/3` certified pools.
  - Stage 6: Persist updated `v2_frozen_benchmark_exec.json`, `v2_frozen_benchmark_light.json`,
    `pr_reference_manifest.json`, `b1_provenance_manifest.json`, and stamped `v2_samples/*.json`.

100% resumable with atomic `.tmp -> os.replace` checkpoints per stage, strictly `<= 3` concurrent Docker
containers (`<= 7.5 vCPU`, `<= 12 GB RAM`) via LPT distinct-project 3-worker dispatcher. Zero LLM API calls.
"""
import hashlib
import json
import os
import re
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import remote_core as rc
import remote_eval_v2 as rev2
import remote_python_core as rpc
import remote_v2_freeze_benchmark_and_mext as rfreeze
from v2_guards_and_attribution import (
    HARNESS_VERSION,
    analyze_affected_test_scope,
    compute_role_tagged_retention,
    compute_unified_diff,
    extract_declared_java_method_name,
    is_code_semantically_changed,
    java_pit_mutant_exact_id,
    resolve_pit_mutants_by_exact_id,
)

BASE_DIR = "/root/sqj_work/FT1"
FULL_DIR = os.path.join(BASE_DIR, "full_runs")
SAMPLES_DIR = os.path.join(FULL_DIR, "v2_samples")
EXEC_JSON = os.path.join(FULL_DIR, "v2_frozen_benchmark_exec.json")
LIGHT_JSON = os.path.join(FULL_DIR, "v2_frozen_benchmark_light.json")
PR_MANIFEST_JSON = os.path.join(FULL_DIR, "pr_reference_manifest.json")
B1_MANIFEST_JSON = os.path.join(FULL_DIR, "b1_provenance_manifest.json")
BUNDLE_V23_JSON = os.path.join(FULL_DIR, "v23_heldout_bundle.json")

CKPT_STAGE1 = os.path.join(FULL_DIR, "v23_ckpt_stage1_java_3x.json")
STAGE1_DIFF_OUT = os.path.join(FULL_DIR, "v23_stage1_pool_diff.json")
CKPT_STAGE2 = os.path.join(FULL_DIR, "v23_upstream_repeated_pollution_controls.json")
CKPT_STAGE3 = os.path.join(FULL_DIR, "v23_ckpt_stage3_od_heldout_wplus.json")
CKPT_STAGE4 = os.path.join(FULL_DIR, "v23_ckpt_stage4_pr_atomic.json")
CKPT_STAGE5 = os.path.join(FULL_DIR, "v23_ckpt_stage5_b1_all.json")

PROJ_LOCKS: Dict[str, threading.Lock] = defaultdict(threading.Lock)
SAVE_LOCK = threading.Lock()
ALLOWED_EXACT_GRANS = ("METHOD_EXACT", "CLASS_ISOLATED_EXACT")


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def sha256_hex(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def atomic_write(path: str, payload: Any, indent: Optional[int] = None) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=indent, ensure_ascii=False)
    os.replace(tmp, path)


def run_lpt_distinct_project(
    items: List[Any],
    proj_key_fn: Callable[[Any], str],
    work_fn: Callable[[Any], Tuple[str, Any]],
    on_result: Callable[[str, Any, int], None],
    stage_label: str,
    max_workers: int = 3,
) -> None:
    """
    LPT distinct-project dispatcher: strictly `<= max_workers` (3) concurrent Docker containers
    while guaranteeing all 3 workers always operate on 3 DISTINCT repositories in parallel.
    """
    by_proj: Dict[str, List[Any]] = defaultdict(list)
    for it in items:
        by_proj[proj_key_fn(it)].append(it)

    active_projs: Set[str] = set()
    in_flight: Dict[Any, str] = {}
    done_cnt = 0

    def _pick_next_proj() -> Optional[str]:
        cands = [
            (len(q), 1 if pk.startswith("java:") else 0, pk)
            for pk, q in by_proj.items()
            if q and pk not in active_projs
        ]
        if not cands:
            return None
        cands.sort(reverse=True)
        return cands[0][2]

    def _wrapped(it: Any, pk: str) -> Tuple[str, Any]:
        with PROJ_LOCKS[pk]:
            return work_fn(it)

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        while any(by_proj.values()) or in_flight:
            while len(in_flight) < max_workers:
                nxt_pk = _pick_next_proj()
                if nxt_pk is None:
                    break
                it = by_proj[nxt_pk].pop(0)
                active_projs.add(nxt_pk)
                fut = ex.submit(_wrapped, it, nxt_pk)
                in_flight[fut] = nxt_pk
            if not in_flight:
                break
            done_futs, _ = wait(list(in_flight.keys()), return_when=FIRST_COMPLETED)
            for fut in done_futs:
                pk = in_flight.pop(fut)
                active_projs.discard(pk)
                try:
                    k, val = fut.result()
                    done_cnt += 1
                    on_result(k, val, done_cnt)
                except Exception as e:
                    done_cnt += 1
                    log(f"  [{stage_label} ERR on {pk}]: {e}")


# ============================================================================
# STAGE 1 (R5-1): 100% Fresh 3/3 Clean-e_0 Java PIT Re-Freeze (Zero Inheritance!)
# ============================================================================

def _is_strict_attributed_kill(m: Dict[str, Any]) -> bool:
    return bool(
        m.get("attributed_kill")
        and not m.get("is_unknown")
        and m.get("status") == "KILLED"
        and m.get("attribution_granularity") in ALLOWED_EXACT_GRANS
        and str(m.get("index", "")).strip() != ""
    )


def _run_3x_java_pit_certified(
    proj_dir: str,
    mod_rel: str,
    jdk: str,
    test_file_path: str,
    test_fqcn: str,
    test_method: str,
    focal_classes: List[str],
    pit_pair: Tuple[str, Optional[str]],
    fqcn_to_path: Optional[Dict[str, str]],
    mutators_arg: str,
    timeout: int,
) -> Tuple[bool, List[Dict[str, Dict[str, Any]]], List[float]]:
    runs_by_eid: List[Dict[str, Dict[str, Any]]] = []
    secs: List[float] = []
    for _ in range(3):
        ok, muts, dt, _ = rfreeze.run_attributed_java_pit(
            proj_dir=proj_dir,
            mod_rel=mod_rel,
            jdk=jdk,
            test_file_path=test_file_path,
            test_fqcn=test_fqcn,
            test_method=test_method,
            focal_classes=focal_classes,
            pit_pair=pit_pair,
            fqcn_to_path=fqcn_to_path,
            mutators_arg=mutators_arg,
            timeout=timeout,
        )
        secs.append(dt)
        if not ok:
            return False, runs_by_eid, secs
        by_eid, _ = resolve_pit_mutants_by_exact_id(muts)
        runs_by_eid.append(by_eid)
    return True, runs_by_eid, secs


def _admit_3x_certified_mutants(runs_by_eid: List[Dict[str, Dict[str, Any]]]) -> Dict[str, Dict[str, Any]]:
    if len(runs_by_eid) != 3:
        return {}
    common_eids = set(runs_by_eid[0].keys()) & set(runs_by_eid[1].keys()) & set(runs_by_eid[2].keys())
    admitted: Dict[str, Dict[str, Any]] = {}
    for eid in sorted(common_eids):
        r0, r1, r2 = runs_by_eid[0][eid], runs_by_eid[1][eid], runs_by_eid[2][eid]
        if _is_strict_attributed_kill(r0) and _is_strict_attributed_kill(r1) and _is_strict_attributed_kill(r2):
            mc = dict(r0)
            mc["v2_attributed_3x"] = True
            mc["certified_v23"] = True
            mc["runs_3x_statuses"] = [r0["status"], r1["status"], r2["status"]]
            mc["runs_3x_granularities"] = [
                r0["attribution_granularity"],
                r1["attribution_granularity"],
                r2["attribution_granularity"],
            ]
            mc["runs_3x_killing_tests"] = [
                r0.get("killingTest", ""),
                r1.get("killingTest", ""),
                r2.get("killingTest", ""),
            ]
            admitted[eid] = mc
    return admitted


def fresh_3x_refreeze_java_sample(sample: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    pdir = sample["proj_dir"]
    mod_rel = sample["module"]
    jdk = sample["jdk"]
    ttype = sample["test_type"]
    pit_pair = tuple(sample["pit_pair"])
    vic_file = sample.get("victim_file_path") or os.path.join(pdir, sample["victim_file_rel"])
    pol_file = sample.get("polluter_file_path") or (
        os.path.join(pdir, sample["polluter_file_rel"]) if sample.get("polluter_file_rel") else vic_file
    )

    # Clean any residual isolation markers first
    for fp in {vic_file, pol_file}:
        if os.path.exists(fp):
            txt = open(fp, encoding="utf-8", errors="replace").read()
            cleaned = re.sub(r"/\*FT1_ISOLATED\s*(.*?)\s*\*/", r"\1", txt)
            cleaned = re.sub(r"\bprivate\s+void\s+/\*FT1_ISOLATED_J3\*/\s+_ft1_iso_", "public void ", cleaned)
            if cleaned != txt:
                open(fp, "w", encoding="utf-8").write(cleaned)

    # 1. Target 3/3 clean-e_0 PIT (`ALL` with `DEFAULTS,STRONGER,EXPERIMENTAL_SWITCH` fallback)
    pref_arg = sample.get("java_mext_mutators_used") or "ALL"
    if pref_arg == "STRONGER,EXPERIMENTAL_SWITCH":
        pref_arg = "DEFAULTS,STRONGER,EXPERIMENTAL_SWITCH"
    ok_t, t_runs, t_secs = _run_3x_java_pit_certified(
        proj_dir=pdir, mod_rel=mod_rel, jdk=jdk,
        test_file_path=vic_file, test_fqcn=sample["victim_fqcn"],
        test_method=sample["victim_method"], focal_classes=sample["focal_classes"],
        pit_pair=pit_pair, fqcn_to_path=sample.get("fqcn_to_path"),
        mutators_arg=pref_arg, timeout=160,
    )
    mut_arg_used = pref_arg
    if not ok_t and pref_arg == "ALL":
        mut_arg_used = "DEFAULTS,STRONGER,EXPERIMENTAL_SWITCH"
        ok_t, t_runs, t_secs = _run_3x_java_pit_certified(
            proj_dir=pdir, mod_rel=mod_rel, jdk=jdk,
            test_file_path=vic_file, test_fqcn=sample["victim_fqcn"],
            test_method=sample["victim_method"], focal_classes=sample["focal_classes"],
            pit_pair=pit_pair, fqcn_to_path=sample.get("fqcn_to_path"),
            mutators_arg=mut_arg_used, timeout=160,
        )
    if not ok_t:
        mut_arg_used = "DEFAULTS"
        ok_t, t_runs, t_secs = _run_3x_java_pit_certified(
            proj_dir=pdir, mod_rel=mod_rel, jdk=jdk,
            test_file_path=vic_file, test_fqcn=sample["victim_fqcn"],
            test_method=sample["victim_method"], focal_classes=sample["focal_classes"],
            pit_pair=pit_pair, fqcn_to_path=sample.get("fqcn_to_path"),
            mutators_arg="DEFAULTS", timeout=160,
        )

    admitted_t = _admit_3x_certified_mutants(t_runs) if ok_t else {}
    new_m_det: List[Dict[str, Any]] = []
    for eid in sorted(admitted_t.keys()):
        m = admitted_t[eid]
        if m["mutator_short"] in rfreeze.PIT_DEFAULT_MUTATOR_CLASSES:
            new_m_det.append(m)

    # 2. Partner 3/3 clean-e_0 PIT (`DEFAULTS`) for OD samples
    new_p_det: List[Dict[str, Any]] = []
    p_secs: List[float] = []
    pol_fclasses = sample.get("polluter_focal_classes") or sample["focal_classes"]
    if ttype == "OD" and sample.get("polluter_fqcn") and sample.get("polluter_method") and pol_fclasses:
        ok_p, p_runs, p_secs = _run_3x_java_pit_certified(
            proj_dir=pdir, mod_rel=mod_rel, jdk=jdk,
            test_file_path=pol_file, test_fqcn=sample["polluter_fqcn"],
            test_method=sample["polluter_method"], focal_classes=pol_fclasses[:4],
            pit_pair=pit_pair, fqcn_to_path=sample.get("fqcn_to_path"),
            mutators_arg="DEFAULTS", timeout=135,
        )
        if ok_p:
            admitted_p = _admit_3x_certified_mutants(p_runs)
            new_p_det = [admitted_p[peid] for peid in sorted(admitted_p.keys())[:15]]

    # 3. Disjoint M_ext_op_value_flow & M_ext_op_call from fresh 3/3 certified kills
    forb_eids = {m["exact_id"] for m in (new_m_det + new_p_det)}
    new_vf: List[Dict[str, Any]] = []
    new_cl: List[Dict[str, Any]] = []
    for eid in sorted(admitted_t.keys()):
        m = admitted_t[eid]
        ms = m["mutator_short"]
        if ms in rfreeze.PIT_DEFAULT_MUTATOR_CLASSES or eid in forb_eids:
            continue
        forb_eids.add(eid)
        mc = dict(m)
        if ms in rfreeze.PIT_CALL_CRASH_PRONE_CLASSES:
            mc["pool"] = "M_ext_op_call"
            new_cl.append(mc)
        else:
            mc["pool"] = "M_ext_op_value_flow"
            new_vf.append(mc)
    new_vf = new_vf[:25]
    new_cl = new_cl[:15]

    # Exact old-vs-new pool diff
    def _eids(lst: List[Dict[str, Any]]) -> Set[str]:
        return {m.get("exact_id") or java_pit_mutant_exact_id(m) for m in (lst or [])}

    old_md, new_md = _eids(sample.get("m_det") or []), _eids(new_m_det)
    old_pd, new_pd = _eids(sample.get("polluter_m_det") or []), _eids(new_p_det)
    old_vf, new_vf_e = _eids(sample.get("m_ext_op_value_flow") or []), _eids(new_vf)
    old_cl, new_cl_e = _eids(sample.get("m_ext_op_call") or []), _eids(new_cl)

    upd = {
        "m_det": new_m_det,
        "m_det_count": len(new_m_det),
        "v2_eligible": len(new_m_det) >= 3,
        "polluter_m_det": new_p_det,
        "polluter_m_det_count": len(new_p_det),
        "m_ext_op_value_flow": new_vf,
        "m_ext_op_call": new_cl,
        "m_ext_total_count": len(new_vf) + len(new_cl),
        "java_mext_mutators_used": mut_arg_used,
        "pit_all_3x_secs": t_secs,
        "pit_partner_3x_secs": p_secs,
        "certified_v23_pools": True,
    }
    diff_rec = {
        "sample_id": sample["sample_id"],
        "project_name": sample["project_name"],
        "test_type": ttype,
        "ok_t": ok_t,
        "mut_arg_used": mut_arg_used,
        "eligible_after": len(new_m_det) >= 3,
        "m_det_old": len(old_md),
        "m_det_new": len(new_md),
        "m_det_changed": (old_md != new_md),
        "m_det_dropped": sorted(old_md - new_md),
        "m_det_added": sorted(new_md - old_md),
        "polluter_m_det_old": len(old_pd),
        "polluter_m_det_new": len(new_pd),
        "polluter_m_det_changed": (old_pd != new_pd),
        "polluter_dropped": sorted(old_pd - new_pd),
        "polluter_added": sorted(new_pd - old_pd),
        "m_ext_vf_old": len(old_vf),
        "m_ext_vf_new": len(new_vf_e),
        "m_ext_vf_changed": (old_vf != new_vf_e),
        "m_ext_call_old": len(old_cl),
        "m_ext_call_new": len(new_cl_e),
        "m_ext_call_changed": (old_cl != new_cl_e),
        "any_in_loop_pool_changed": (old_md != new_md) or (old_pd != new_pd),
        "any_mext_pool_changed": (old_vf != new_vf_e) or (old_cl != new_cl_e),
    }
    return upd, diff_rec


# ============================================================================
# STAGE 2 (R5-4): Matched Unmodified Upstream Controls (93 Py OD + 49 Java OD)
# ============================================================================

def run_upstream_control_sample(sample: Dict[str, Any]) -> Dict[str, Any]:
    lang = sample["language"]
    if lang == "python":
        ev = rfreeze.run_py_repeated_pollution_raw(
            proj_dir=sample["proj_dir"],
            target_node=sample["test_full_name"],
            partner_node=sample["polluter_full_name"],
            allow_upstream_nonidempotent_p2_call_fail=False,
        )
        st = str(ev.get("status", ""))
        non_p_or_infra_unknown = not st.startswith((
            "UNKNOWN_EMPTY", "UNKNOWN_MALFORMED", "UNKNOWN_OCC",
            "UNKNOWN_UNEXPECTED", "UNKNOWN_MISSING", "UNKNOWN_INVALID",
            "UNKNOWN_P1", "UNKNOWN_P2", "UNKNOWN_NO_REP",
        ))
        upstream_nonidemp_p2 = bool(
            non_p_or_infra_unknown
            and ev.get("p1_ok")
            and ev.get("p2_lifecycle_ok")
            and (not ev.get("p2_call_ok"))
        )
        return {
            "sample_id": sample["sample_id"],
            "language": "python",
            "project_name": sample["project_name"],
            "od_subtype": sample.get("od_subtype", ""),
            "strict_passed": bool(ev.get("strict_passed")),
            "p1_ok": bool(ev.get("p1_ok")),
            "p2_call_ok": bool(ev.get("p2_call_ok")),
            "p2_lifecycle_ok": bool(ev.get("p2_lifecycle_ok")),
            "target_ok": bool(ev.get("target_ok")),
            "is_unknown": bool(ev.get("is_unknown")),
            "status": st,
            "upstream_nonidempotent_p2_call_fail_only": upstream_nonidemp_p2,
            "sec": ev.get("sec", 0.0),
        }
    else:
        rep = rfreeze.run_java_repeated_pollution_raw(sample, ensure_compile=True)
        is_unk = bool(rep.get("is_unknown"))
        phase_known = bool(rep.get("phase_level_known"))
        p2_lc = bool(rep.get("p2_lifecycle_ok"))
        p2_exc = rep.get("p2_call_exception_type")
        upstream_nonidemp_p2 = bool(
            (not is_unk)
            and phase_known
            and rep.get("order_exact")
            and rep.get("p1_ok")
            and p2_lc
            and (not rep.get("p2_call_ok"))
            and bool(p2_exc)
        )
        return {
            "sample_id": sample["sample_id"],
            "language": "java",
            "project_name": sample["project_name"],
            "mode": rep.get("mode", "UNKNOWN"),
            "exec_order": rep.get("exec_order", ""),
            "phase_level_known": phase_known,
            "phases": rep.get("phases") or {},
            "strict_passed": bool(rep.get("strict_passed")),
            "p1_ok": bool(rep.get("p1_ok")),
            "p2_call_ok": bool(rep.get("p2_call_ok")),
            "p2_lifecycle_ok": p2_lc,
            "p2_call_exception_type": p2_exc,
            "target_ok": bool(rep.get("target_ok")),
            "order_exact": bool(rep.get("order_exact")),
            "is_unknown": is_unk,
            "status": str(rep.get("status", "UNKNOWN")),
            "upstream_nonidempotent_p2_call_fail_only": upstream_nonidemp_p2,
            "fail_lines": rep.get("fail_lines", []),
            "sec": rep.get("sec", 0.0),
        }


# ============================================================================
# STAGE 3 (R5-3 + R5-4): Exact Held-Out OD Repeated-Pollution Refresh (Py & Java)
# ============================================================================

def refresh_od_heldout_wplus(
    sample: Dict[str, Any],
    candidate: Dict[str, Any],
    upstream_ctrl: Dict[str, Any],
) -> Dict[str, Any]:
    lang = sample["language"]
    upstream_nonidemp = bool(
        (not upstream_ctrl.get("is_unknown"))
        and upstream_ctrl.get("upstream_nonidempotent_p2_call_fail_only")
    )
    if lang == "python":
        pdir = sample["proj_dir"]
        vic_file = sample.get("victim_file_path") or os.path.join(pdir, sample["victim_file_rel"])
        pol_file = sample.get("polluter_file_path") or (
            os.path.join(pdir, sample["polluter_file_rel"]) if sample.get("polluter_file_rel") else vic_file
        )
        same_file = (os.path.abspath(vic_file) == os.path.abspath(pol_file))
        orig_vb = open(vic_file, "rb").read()
        orig_pb = orig_vb if same_file else open(pol_file, "rb").read()
        orig_vs = orig_vb.decode("utf-8", errors="replace")
        orig_ps = orig_pb.decode("utf-8", errors="replace")
        try:
            vcode = candidate.get("victim_code") or candidate.get("test_code")
            pcode = candidate.get("polluter_code")
            p_changed = bool(is_code_semantically_changed("python", sample.get("orig_polluter_code", ""), pcode))
            if same_file:
                ns = orig_vs
                if vcode:
                    ns = rpc.replace_py_func(ns, sample.get("victim_class"), sample["victim_method"], vcode)
                if p_changed and sample.get("polluter_method") and sample["polluter_method"] != sample["victim_method"]:
                    ns = rpc.replace_py_func(ns, sample.get("polluter_class"), sample["polluter_method"], pcode)
                ns = rpc.inject_py_imports(ns, candidate.get("imports") or [])
                open(vic_file, "w", encoding="utf-8").write(ns)
            else:
                if vcode:
                    open(vic_file, "w", encoding="utf-8").write(
                        rpc.inject_py_imports(
                            rpc.replace_py_func(orig_vs, sample.get("victim_class"), sample["victim_method"], vcode),
                            candidate.get("imports") or [],
                        )
                    )
                if p_changed:
                    open(pol_file, "w", encoding="utf-8").write(
                        rpc.inject_py_imports(
                            rpc.replace_py_func(orig_ps, sample.get("polluter_class"), sample["polluter_method"], pcode),
                            candidate.get("imports") or [],
                        )
                    )
            allow_stress = bool(upstream_nonidemp and not p_changed)
            w_strict, w_diag, dt_w, scheds = rfreeze.check_py_w_plus_heldout_detailed(
                pdir, sample["test_full_name"], sample["polluter_full_name"],
                allow_upstream_nonidempotent_p2_call_fail=allow_stress,
            )
            w_stress = bool(scheds.get("w_plus_heldout_passed_target_stress", w_strict))
            return {
                "harness_version": HARNESS_VERSION,
                "w_plus_heldout_passed": w_strict,
                "w_plus_heldout_passed_strict": w_strict,
                "w_plus_heldout_passed_target_stress": w_stress,
                "partner_modified_by_candidate": p_changed,
                "upstream_control_p2_nonidempotent": upstream_nonidemp,
                "allow_stress_p2_applied": allow_stress,
                "w_plus_heldout_diag": w_diag[:450],
                "w_plus_heldout_schedules": scheds,
                "w_plus_heldout_sec": dt_w,
            }
        finally:
            open(vic_file, "wb").write(orig_vb)
            if not same_file:
                open(pol_file, "wb").write(orig_pb)
    else:
        pdir = sample["proj_dir"]
        mod_rel = sample["module"]
        jdk = sample["jdk"]
        mod_dir = pdir if mod_rel in ("", ".") else os.path.join(pdir, mod_rel)
        pom_path = os.path.join(mod_dir, "pom.xml")
        vic_file = sample.get("victim_file_path") or os.path.join(pdir, sample["victim_file_rel"])
        pol_file = sample.get("polluter_file_path") or (
            os.path.join(pdir, sample["polluter_file_rel"]) if sample.get("polluter_file_rel") else vic_file
        )
        same_file = (os.path.abspath(vic_file) == os.path.abspath(pol_file))
        orig_vic = re.sub(r"/\*FT1_ISOLATED\s*(.*?)\s*\*/", r"\1", open(vic_file, encoding="utf-8", errors="replace").read())
        orig_pol = orig_vic if same_file else re.sub(r"/\*FT1_ISOLATED\s*(.*?)\s*\*/", r"\1", open(pol_file, encoding="utf-8", errors="replace").read())
        orig_pom = open(pom_path, encoding="utf-8", errors="replace").read()
        vbak = rc.backup_class_files(mod_dir, sample["victim_fqcn"])
        pbak = rc.backup_class_files(mod_dir, sample["polluter_fqcn"]) if (sample.get("polluter_fqcn") and sample["polluter_fqcn"] != sample["victim_fqcn"]) else {}
        try:
            vcode, pcode = candidate.get("victim_code"), candidate.get("polluter_code")
            p_changed = bool(is_code_semantically_changed("java", sample.get("orig_polluter_code", ""), pcode))
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
            with rfreeze.DOCKER_SEM:
                rc.docker_mvn(pdir, jdk, f"test-compile {extra_cp} {pl}", timeout=115)
            allow_stress = bool(upstream_nonidemp and not p_changed)
            expected_p2_exc = upstream_ctrl.get("p2_call_exception_type") if allow_stress else None
            w_strict, w_diag, dt_w, scheds = rfreeze.check_java_w_plus_heldout_detailed(
                sample,
                allow_upstream_nonidempotent_p2_call_fail=allow_stress,
                expected_p2_call_exc=expected_p2_exc,
            )
            w_stress = bool(scheds.get("w_plus_heldout_passed_target_stress", w_strict)) if allow_stress else bool(w_strict)
            return {
                "harness_version": HARNESS_VERSION,
                "w_plus_heldout_passed": w_strict,
                "w_plus_heldout_passed_strict": w_strict,
                "w_plus_heldout_passed_target_stress": w_stress,
                "partner_modified_by_candidate": p_changed,
                "upstream_control_p2_nonidempotent": upstream_nonidemp,
                "upstream_control_p2_call_exception_type": expected_p2_exc,
                "allow_stress_p2_applied": allow_stress,
                "w_plus_heldout_diag": w_diag[:450],
                "w_plus_heldout_schedules": scheds,
                "w_plus_heldout_sec": dt_w,
            }
        finally:
            open(vic_file, "w", encoding="utf-8").write(orig_vic)
            if not same_file:
                open(pol_file, "w", encoding="utf-8").write(orig_pol)
            open(pom_path, "w", encoding="utf-8").write(orig_pom)
            rc.restore_class_files(mod_dir, sample["victim_fqcn"], vbak)
            if pbak:
                rc.restore_class_files(mod_dir, sample["polluter_fqcn"], pbak)


# ============================================================================
# STAGE 4 (R5-5): Strict ALL-OR-NOTHING Atomic Same-Tree PR Full Dual-Role Gate
# ============================================================================

def eval_pr_full_gate_atomic(sample: Dict[str, Any]) -> Dict[str, Any]:
    """
    Re-apply ALL PR test files strictly ALL-OR-NOTHING (zero fallback on any hunk/file failure),
    compute a single `applied_tree_sha256`, fold `extra_files_edited_count > 0` into
    `affects_partner_or_shared`, and execute `W+`, `W+_heldout`, target `3x` PIT, and
    partner `3x` PIT (when OD and `affects_partner_or_shared`) on that IDENTICAL applied tree
    against Stage 1's freshly certified `3/3` pools!
    """
    sid = sample["sample_id"]
    pdir = sample["proj_dir"]
    mod_rel = sample["module"]
    jdk = sample["jdk"]
    ttype = sample["test_type"]
    pit_pair = tuple(sample["pit_pair"])
    mod_dir = pdir if mod_rel in ("", ".") else os.path.join(pdir, mod_rel)
    vic_file = sample.get("victim_file_path") or os.path.join(pdir, sample["victim_file_rel"])
    pol_file = sample.get("polluter_file_path") or (
        os.path.join(pdir, sample["polluter_file_rel"]) if sample.get("polluter_file_rel") else vic_file
    )
    same_file = (os.path.abspath(vic_file) == os.path.abspath(pol_file))

    diff_txt = rfreeze.get_cached_pr_diff(sample["pr_url"])
    if not diff_txt:
        return {"sample_id": sid, "pr_manifest": sample.get("v2_pr_manifest"), "v2_dev_pr_eval": None, "m_gold_ids": []}

    info = rfreeze.inspect_pr_diff_files(diff_txt)
    orig_vic = open(vic_file, encoding="utf-8", errors="replace").read()
    orig_pol = open(pol_file, encoding="utf-8", errors="replace").read() if not same_file else orig_vic
    norm_vic_rel = sample["victim_file_rel"].replace("\\", "/").lstrip("/")
    norm_pol_rel = os.path.relpath(pol_file, pdir).replace("\\", "/").lstrip("/")

    applied_test_files: Dict[str, str] = {}
    failed_files: List[str] = []
    expected_test_files = list(info["test_files"])

    for f_rel in expected_test_files:
        sec = info["sec_by_file"][f_rel]
        f_abs = os.path.join(pdir, f_rel)
        if f_rel.endswith(norm_vic_rel) or norm_vic_rel.endswith(f_rel):
            patched = rfreeze.apply_single_file_diff_hunk(orig_vic, sec)
            if patched is None:
                failed_files.append(f_rel)
            else:
                applied_test_files[vic_file] = patched
        elif (not same_file) and (f_rel.endswith(norm_pol_rel) or norm_pol_rel.endswith(f_rel)):
            patched_p = rfreeze.apply_single_file_diff_hunk(orig_pol, sec)
            if patched_p is None:
                failed_files.append(f_rel)
            else:
                applied_test_files[pol_file] = patched_p
        elif os.path.exists(f_abs):
            orig_other = open(f_abs, encoding="utf-8", errors="replace").read()
            patched_o = rfreeze.apply_single_file_diff_hunk(orig_other, sec)
            if patched_o is None:
                failed_files.append(f_rel)
            else:
                applied_test_files[f_abs] = patched_o
        else:
            failed_files.append(f_rel)

    # STRICT ALL-OR-NOTHING: abort immediately if ANY expected test file failed or victim file wasn't applied!
    all_test_files_applied = (
        len(expected_test_files) > 0
        and len(failed_files) == 0
        and len(applied_test_files) == len(expected_test_files)
        and vic_file in applied_test_files
    )

    pr_manifest_entry: Dict[str, Any] = {
        "sample_id": sid,
        "harness_version": HARNESS_VERSION,
        "project_name": sample["project_name"],
        "benchmark_sha": sample["sha"],
        "pr_url": sample["pr_url"],
        "idoft_status": sample.get("pr_status") or "UNKNOWN",
        "pr_diff_sha256": info["pr_diff_sha256"],
        "pr_scope_type": info["pr_scope_type"],
        "all_files_in_pr": info["all_files"],
        "test_files_in_pr": expected_test_files,
        "prod_files_in_pr": info["prod_files"],
        "build_files_in_pr": info["build_files"],
        "applied_test_files_count": len(applied_test_files),
        "failed_test_files": failed_files,
        "all_test_files_applied": all_test_files_applied,
        "pr_apply_outcome": (
            "CLEAN_FULL_TEST_APPLY" if all_test_files_applied
            else ("PARTIAL_TEST_APPLY_FAILED" if len(applied_test_files) > 0 else "ZERO_FILES_APPLIED")
        ),
    }

    if not all_test_files_applied:
        return {"sample_id": sid, "pr_manifest": pr_manifest_entry, "v2_dev_pr_eval": None, "m_gold_ids": []}

    # Single authoritative applied_tree_sha256 covering ALL modified files
    tree_hasher = hashlib.sha256()
    for fp_k in sorted(applied_test_files.keys()):
        rel_k = os.path.relpath(fp_k, pdir).replace("\\", "/")
        tree_hasher.update(rel_k.encode("utf-8") + b"\0" + applied_test_files[fp_k].encode("utf-8") + b"\0")
    applied_tree_sha = tree_hasher.hexdigest()[:24]

    new_vic_src = applied_test_files[vic_file]
    new_pol_src = applied_test_files.get(pol_file, new_vic_src if same_file else orig_pol)
    act_orig_t = rc.extract_java_method(orig_vic, sample["victim_method"]) or sample["orig_test_code"]
    act_new_t = rc.extract_java_method(new_vic_src, sample["victim_method"]) or ""
    act_orig_p = (rc.extract_java_method(orig_pol, sample["polluter_method"]) if (ttype == "OD" and sample.get("polluter_method")) else "") or sample.get("orig_polluter_code", "")
    act_new_p = (rc.extract_java_method(new_pol_src, sample["polluter_method"]) if (ttype == "OD" and sample.get("polluter_method")) else "") or ""

    scope = analyze_affected_test_scope(
        language="java",
        test_type=ttype,
        orig_vic_file_src=orig_vic,
        new_vic_file_src=new_vic_src,
        orig_pol_file_src=orig_pol,
        new_pol_file_src=new_pol_src,
        same_file=same_file,
        orig_target_method_src=act_orig_t,
        new_target_method_src=act_new_t,
        orig_partner_method_src=act_orig_p,
        new_partner_method_src=act_new_p,
        pom_modified=bool(info.get("build_files")),
    )
    extra_files_edited = [
        os.path.relpath(f, pdir).replace("\\", "/")
        for f in sorted(applied_test_files.keys())
        if os.path.abspath(f) not in {os.path.abspath(vic_file), os.path.abspath(pol_file)}
    ]
    # R5-5: Editing ANY extra test file/helper in the PR conservatively affects shared/partner scope!
    affects_partner = bool(scope["affects_partner_or_shared"] or (ttype == "OD" and len(extra_files_edited) > 0))
    scope_kind = scope["scope_kind"]
    if ttype == "OD" and len(extra_files_edited) > 0 and scope_kind == "TARGET_ONLY":
        scope_kind = "EXTRA_TEST_FILES_SHARED_SCOPE"

    pr_manifest_entry["applied_tree_sha256"] = applied_tree_sha
    pr_manifest_entry["applied_victim_code"] = act_new_t
    pr_manifest_entry["applied_polluter_code"] = act_new_p
    pr_manifest_entry["extra_files_edited_count"] = len(extra_files_edited)
    pr_manifest_entry["extra_files_edited"] = extra_files_edited

    # Write ALL applied files ONCE and execute compile + W+ + W+_heldout + target 3x PIT + partner 3x PIT on SAME tree!
    backups_extra = {fp: open(fp, "rb").read() for fp in applied_test_files}
    vbak = rc.backup_class_files(mod_dir, sample["victim_fqcn"])
    pbak = rc.backup_class_files(mod_dir, sample["polluter_fqcn"]) if (sample.get("polluter_fqcn") and sample["polluter_fqcn"] != sample["victim_fqcn"]) else {}
    v23_m_det = sample.get("m_det") or []
    v23_pol_m_det = sample.get("polluter_m_det") or []
    m_gold_ids: List[str] = []
    v2_dev_pr_eval: Optional[Dict[str, Any]] = None

    try:
        for fp, new_txt in applied_test_files.items():
            open(fp, "w", encoding="utf-8").write(new_txt)
        pl = f"-pl {mod_rel}" if mod_rel not in ("", ".") else ""
        with rfreeze.DOCKER_SEM:
            _, out_c, _ = rc.docker_mvn(pdir, jdk, f"test-compile {pl}", timeout=125)
        comp_ok = "BUILD SUCCESS" in out_c
        w_ok = False
        w_ho_strict = False
        w_ho_stress = False
        t_runs_3x: List[Dict[str, Dict[str, Any]]] = []
        p_runs_3x: List[Dict[str, Dict[str, Any]]] = []
        p_eval_ran = False
        p_eval_ok = False

        if comp_ok:
            with rfreeze.DOCKER_SEM:
                w_ok, _, _ = rc.check_stability_w_plus(
                    pdir, mod_rel, jdk, sample["uses_junit5"], ttype,
                    sample["victim_fqcn"], sample["victim_method"],
                    sample.get("polluter_fqcn", ""), sample.get("polluter_method", ""),
                )
            w_ho_strict, _, _, w_ho_sc = rfreeze.check_java_w_plus_heldout_detailed(sample)
            w_ho_stress = bool(w_ho_sc.get("w_plus_heldout_passed_target_stress", w_ho_strict))

            # Target 3x PIT on this IDENTICAL applied tree (lossless exact_8tuple!)
            ok_t3, t_runs_3x, _ = _run_3x_java_pit_certified(
                proj_dir=pdir, mod_rel=mod_rel, jdk=jdk,
                test_file_path=vic_file, test_fqcn=sample["victim_fqcn"],
                test_method=sample["victim_method"], focal_classes=sample["focal_classes"],
                pit_pair=pit_pair, fqcn_to_path=None,
                mutators_arg="DEFAULTS", timeout=130,
            )

            # Partner 3x PIT on this IDENTICAL applied tree whenever OD and affects_partner and |P_det| > 0!
            if ttype == "OD" and affects_partner and len(v23_pol_m_det) > 0:
                p_eval_ran = True
                pol_fc = sample.get("polluter_focal_classes") or sample["focal_classes"]
                p_eval_ok, p_runs_3x, _ = _run_3x_java_pit_certified(
                    proj_dir=pdir, mod_rel=mod_rel, jdk=jdk,
                    test_file_path=pol_file, test_fqcn=sample["polluter_fqcn"],
                    test_method=sample["polluter_method"], focal_classes=pol_fc[:4],
                    pit_pair=pit_pair, fqcn_to_path=None,
                    mutators_arg="DEFAULTS", timeout=125,
                )

            if ok_t3 and len(t_runs_3x) == 3 and len(v23_m_det) > 0:
                t_killed_3x: Set[str] = set()
                t_killed_union: Set[str] = set()
                t_unknown_ids: List[str] = []
                for m in v23_m_det:
                    eid = m["exact_id"]
                    rms = [r.get(eid) for r in t_runs_3x]
                    if any(rm is None or rm.get("is_unknown") for rm in rms):
                        t_unknown_ids.append(m["id"])
                    else:
                        if all(rm.get("attributed_kill") for rm in rms):
                            t_killed_3x.add(m["id"])
                        if any(rm.get("attributed_kill") for rm in rms):
                            t_killed_union.add(m["id"])

                p_killed_3x: Optional[Set[str]] = None
                p_unknown_ids: List[str] = []
                if p_eval_ran and p_eval_ok and len(p_runs_3x) == 3:
                    p_killed_3x = set()
                    for pm in v23_pol_m_det:
                        peid = pm["exact_id"]
                        rpms = [r.get(peid) for r in p_runs_3x]
                        if any(rpm is None or rpm.get("is_unknown") for rpm in rpms):
                            p_unknown_ids.append(pm["id"])
                        elif all(rpm.get("attributed_kill") for rpm in rpms):
                            p_killed_3x.add(pm["id"])

                ret_full = compute_role_tagged_retention(
                    target_m_det=v23_m_det,
                    target_killed_ids=t_killed_3x,
                    test_type=ttype,
                    partner_m_det=v23_pol_m_det,
                    affects_partner=affects_partner,
                    partner_eval_ran=p_eval_ran,
                    partner_eval_ok=p_eval_ok,
                    partner_killed_ids=p_killed_3x,
                    target_unknown_ids=t_unknown_ids,
                    partner_unknown_ids=p_unknown_ids,
                )
                kept_det = [m["id"] for m in v23_m_det if m["id"] in t_killed_3x]
                lost_det = [m for m in v23_m_det if m["id"] not in t_killed_3x and m["id"] not in set(t_unknown_ids)]
                ret_3x = ret_full["target_retention"]
                ret_union = round(len(t_killed_union) / len(v23_m_det), 4) if (not t_unknown_ids) else None
                if sample.get("pr_status") == "Accepted" and info["pr_scope_type"] == "TEST_ONLY_PR":
                    m_gold_ids = kept_det

                pr_full_gate = {
                    "harness_version": HARNESS_VERSION,
                    "atomic_same_tree": True,
                    "applied_tree_sha256": applied_tree_sha,
                    "scope_kind": scope_kind,
                    "affects_partner_or_shared": affects_partner,
                    "partner_method_changed": scope["partner_method_changed"],
                    "shared_fixture_or_helper_changed": scope["shared_fixture_or_helper_changed"],
                    "extra_files_edited_count": len(extra_files_edited),
                    "extra_files_edited": extra_files_edited,
                    "target_m_det_total": len(v23_m_det),
                    "target_m_det_killed_3x": len(kept_det),
                    "target_unknown_count": len(t_unknown_ids),
                    "target_retention_3x": ret_full["target_retention"],
                    "partner_m_det_total": len(v23_pol_m_det),
                    "partner_eval_ran": p_eval_ran,
                    "partner_eval_ok": p_eval_ok,
                    "partner_unknown_count": len(p_unknown_ids),
                    "partner_retention_3x": ret_full["partner_retention"],
                    "joint_retention_3x": ret_full["joint_retention"],
                    "pr_full_gate_accepts": bool(w_ok and ret_full["gate_safe"]),
                    "pr_full_gate_override": ret_full["gate_status_override"],
                    "survived_mutants_count": len(ret_full["survived_mutants"]),
                }
                v2_dev_pr_eval = {
                    "harness_version": HARNESS_VERSION,
                    "atomic_same_tree": True,
                    "pr_url": sample["pr_url"],
                    "idoft_status": sample.get("pr_status") or "UNKNOWN",
                    "pr_scope_type": info["pr_scope_type"],
                    "pr_apply_outcome": "CLEAN_FULL_TEST_APPLY",
                    "applied_tree_sha256": applied_tree_sha,
                    "applied_victim_code": act_new_t,
                    "applied_polluter_code": act_new_p,
                    "prod_files_in_pr": info["prod_files"],
                    "compiled": comp_ok,
                    "w_plus_passed": w_ok,
                    "w_plus_heldout_passed": w_ho_strict,
                    "w_plus_heldout_passed_strict": w_ho_strict,
                    "w_plus_heldout_passed_target_stress": w_ho_stress,
                    "m_det_total": len(v23_m_det),
                    "m_det_killed_3x": len(kept_det),
                    "target_unknown_count": len(t_unknown_ids),
                    "retention_m_det_3x": ret_3x,
                    "retention_m_det_union": ret_union,
                    "lost_mutants": [
                        {"id": lm["id"], "exact_id": lm["exact_id"], "line": lm["lineNumber"], "mutator": lm["mutator_short"], "desc": lm["description"]}
                        for lm in lost_det
                    ],
                    "pr_full_gate": pr_full_gate,
                    "applied_victim_diff": compute_unified_diff(orig_vic, new_vic_src, sample["victim_file_rel"]),
                }
                pr_manifest_entry.update({
                    "compiled": comp_ok,
                    "w_plus_passed": w_ok,
                    "w_plus_heldout_passed": w_ho_strict,
                    "w_plus_heldout_passed_strict": w_ho_strict,
                    "retention_m_det_3x": ret_3x,
                    "retention_m_det_union": ret_union,
                    "lost_mutant_count_3x": len(lost_det),
                    "target_unknown_count": len(t_unknown_ids),
                    "partner_unknown_count": len(p_unknown_ids),
                    "affects_partner_or_shared": affects_partner,
                    "scope_kind": scope_kind,
                    "pr_full_gate_accepts": pr_full_gate["pr_full_gate_accepts"],
                    "pr_full_gate_override": pr_full_gate["pr_full_gate_override"],
                })
            else:
                pr_manifest_entry.update({"compiled": comp_ok, "w_plus_passed": w_ok, "pit_3x_completed": False})
    finally:
        for fp, raw_b in backups_extra.items():
            open(fp, "wb").write(raw_b)
        rc.restore_class_files(mod_dir, sample["victim_fqcn"], vbak)
        if pbak:
            rc.restore_class_files(mod_dir, sample["polluter_fqcn"], pbak)
        if len(applied_test_files) > 1:
            pl_rst = f"-pl {mod_rel}" if mod_rel not in ("", ".") else ""
            with rfreeze.DOCKER_SEM:
                rc.docker_mvn(pdir, jdk, f"test-compile {pl_rst}", timeout=120)

    return {
        "sample_id": sid,
        "pr_manifest": pr_manifest_entry,
        "v2_dev_pr_eval": v2_dev_pr_eval,
        "m_gold_ids": m_gold_ids,
    }


# ============================================================================
# STAGE 5 (R5-2): Unified 2.3.0-certified B1 Replay (Java FlakyDoctor + Py iPFlakies)
# ============================================================================

def replay_b1_java_unified(sample: Dict[str, Any], upstream: Dict[str, Any]) -> Dict[str, Any]:
    sid = sample["sample_id"]
    ttype = sample["test_type"]
    b1_entries: Dict[str, Any] = {}
    for tag, p_field in (("gpt", "b1_gpt_patch_path"), ("magicoder", "b1_magicoder_patch_path")):
        csv_rec = upstream["fd_csv"][tag].get((sample["sha"].lower(), sample["test_full_name"]))
        old_ppath = sample.get(p_field) or ""
        auth_ppath = csv_rec["csv_patch_abs"] if (csv_rec and csv_rec["has_official_P"] and csv_rec["csv_patch_abs"]) else ""
        used_ppath = auth_ppath or old_ppath
        if not used_ppath or not os.path.exists(used_ppath):
            continue
        p_sha = sha256_hex(open(used_ppath, "rb").read())[:24]
        is_official_p = bool(csv_rec and csv_rec["has_official_P"] and auth_ppath and os.path.abspath(used_ppath) == os.path.abspath(auth_ppath))
        parsed = rc.parse_flakydoctor_patch_file(used_ppath, ttype, sample["victim_method"], sample.get("polluter_method", ""))
        b1_rec: Dict[str, Any] = {
            "sample_id": sid,
            "tool": f"FlakyDoctor_{tag}",
            "evaluator_version": HARNESS_VERSION,
            "in_official_csv_P": is_official_p,
            "patch_rel_path": os.path.relpath(used_ppath, rc.FD_ROOT),
            "patch_sha256": p_sha,
            "stage_outcome": "UNPARSED",
        }
        if not parsed:
            b1_rec["stage_outcome"] = "APPLICATOR_FORMAT_UNSUPPORTED"
            b1_entries[tag] = b1_rec
            continue
        mismatch_err = ""
        if ttype == "ID" and parsed.get("test_code"):
            decl = extract_declared_java_method_name(parsed["test_code"])
            if decl and decl != sample["victim_method"]:
                mismatch_err = f"Declared `{decl}` != target `{sample['victim_method']}`"
        elif ttype == "OD":
            if parsed.get("victim_code"):
                decl_v = extract_declared_java_method_name(parsed["victim_code"])
                if decl_v and decl_v != sample["victim_method"]:
                    if decl_v == sample.get("polluter_method") and not parsed.get("polluter_code"):
                        parsed["polluter_code"] = parsed["victim_code"]
                        parsed["victim_code"] = None
                    else:
                        mismatch_err = f"Declared victim `{decl_v}` != `{sample['victim_method']}` (third-class cleaner)"
            if parsed.get("polluter_code"):
                decl_p = extract_declared_java_method_name(parsed["polluter_code"])
                if decl_p and sample.get("polluter_method") and decl_p != sample["polluter_method"]:
                    mismatch_err = (mismatch_err + "; " if mismatch_err else "") + f"Declared polluter `{decl_p}` != `{sample['polluter_method']}`"
        if mismatch_err:
            b1_rec["stage_outcome"] = "APPLICATOR_THIRD_CLASS_MISMATCH"
            b1_rec["mismatch_detail"] = mismatch_err
            b1_entries[tag] = b1_rec
            continue

        cand_verb = dict(parsed)
        cand_verb["allow_static_assert_import_salvage"] = False
        ev_v = rev2.evaluate_java_v2_in_loop(sample, cand_verb)
        if not ev_v["compiled"]:
            cand_salv = dict(parsed)
            extra_imps = list(cand_salv.get("imports") or [])
            extra_imps.extend(["import static org.junit.Assert.*;", "import static org.junit.jupiter.api.Assertions.*;"])
            cand_salv["imports"] = extra_imps
            ev_s = rev2.evaluate_java_v2_in_loop(sample, cand_salv)
            if ev_s["compiled"]:
                b1_rec["salvage_compile_would_pass"] = True
                b1_rec["salvage_w_plus"] = ev_s["w_plus_passed"]
                b1_rec["salvage_eroded"] = ev_s["eroded"]
                b1_rec["salvage_sound_fix"] = ev_s["sound_fix"]

        if not ev_v["compiled"]:
            b1_rec["stage_outcome"] = "VERBATIM_COMPILE_FAIL"
        elif not ev_v["w_plus_passed"]:
            b1_rec["stage_outcome"] = "W_PLUS_FAIL"
        elif ev_v.get("gate_status_override") in ("PARTNER_MUT_EVAL_ERROR", "TARGET_MUT_UNKNOWN", "PARTNER_MUT_UNKNOWN"):
            b1_rec["stage_outcome"] = ev_v["gate_status_override"]
        elif ev_v.get("gate_status_override") == "ABSTAIN_PARTNER_UNCOVERED":
            b1_rec["stage_outcome"] = "ABSTAIN_PARTNER_UNCOVERED"
        elif not ev_v["pit_ran"]:
            b1_rec["stage_outcome"] = "MUTATION_EVAL_FAIL"
        elif ev_v["eroded"]:
            b1_rec["stage_outcome"] = "ERODED_W_PLUS_PASS"
        else:
            b1_rec["stage_outcome"] = "OPERATIONAL_SOUND_FIX"

        b1_rec.update({
            "evaluator_version": ev_v.get("harness_version", HARNESS_VERSION),
            "compiled": ev_v["compiled"],
            "w_plus_passed": ev_v["w_plus_passed"],
            "pit_ran": ev_v["pit_ran"],
            "scope_kind": ev_v.get("scope_kind", "TARGET_ONLY"),
            "affects_partner_or_shared": ev_v.get("affects_partner_or_shared", False),
            "gate_status_override": ev_v.get("gate_status_override"),
            "gate_safe": ev_v.get("gate_safe", False),
            "eroded": ev_v["eroded"],
            "sound_fix": ev_v["sound_fix"],
            "target_m_det_total": ev_v.get("target_m_det_total"),
            "partner_m_det_total": ev_v.get("partner_m_det_total"),
            "target_retention": ev_v.get("target_retention"),
            "partner_retention": ev_v.get("partner_retention"),
            "joint_retention": ev_v.get("joint_retention"),
            "retention_m_det": ev_v["retention_m_det"],
            "retention_m_gold": ev_v["retention_m_gold"],
            "survived_count": len(ev_v.get("survived_mutants", [])),
            "target_unknown_count": ev_v.get("target_unknown_count", 0),
            "partner_unknown_count": ev_v.get("partner_unknown_count", 0),
            "eval": ev_v,
        })
        b1_entries[tag] = b1_rec
    return b1_entries


def replay_b1_py_unified(sample: Dict[str, Any], upstream: Dict[str, Any]) -> Dict[str, Any]:
    sid = sample["sample_id"]
    pdir = sample["proj_dir"]
    t_node = sample["test_full_name"]
    p_node = sample["polluter_full_name"]
    ppath = sample.get("b1_ipflakies_patch_path") or ""
    if not ppath or not os.path.exists(ppath):
        return {}
    csv_rec = upstream["ipf_csv"].get((sample["project_name"], sample["sha"].lower(), t_node))
    p_sha = sha256_hex(open(ppath, "rb").read())[:24]
    in_csv = bool(csv_rec and csv_rec["csv_patch_abs"] and os.path.abspath(ppath) == os.path.abspath(csv_rec["csv_patch_abs"]))
    partner_matches = bool(csv_rec and csv_rec["partner"] == p_node)
    vic_abs = os.path.join(pdir, sample["victim_file_rel"])
    orig_vic_s = open(vic_abs, "rb").read().decode("utf-8", errors="replace")
    patched_s = rpc.apply_ipflakies_patch_file(orig_vic_s, ppath)

    rec: Dict[str, Any] = {
        "sample_id": sid,
        "tool": "iPFlakies",
        "evaluator_version": HARNESS_VERSION,
        "in_official_patches_csv": in_csv,
        "csv_partner_matches_benchmark": partner_matches,
        "csv_partner": csv_rec["partner"] if csv_rec else "",
        "benchmark_partner": p_node,
        "patch_rel_path": os.path.relpath(ppath, rpc.IPFLAKIES_ROOT),
        "patch_sha256": p_sha,
        "stage_outcome": "PATCH_APPLY_FAIL",
    }
    if not patched_s:
        return {"ipflakies": rec}
    new_v_fn = rpc.extract_py_func(patched_s, sample.get("victim_class"), sample["victim_method"])
    if not new_v_fn:
        rec["stage_outcome"] = "APPLICATOR_FORMAT_UNSUPPORTED"
        return {"ipflakies": rec}
    orig_imps = {ln.strip() for ln in orig_vic_s.splitlines() if ln.strip().startswith(("import ", "from "))}
    added_imps = [ln.strip() for ln in patched_s.splitlines() if ln.strip().startswith(("import ", "from ")) and ln.strip() not in orig_imps]
    cand = {"victim_code": new_v_fn, "polluter_code": None, "imports": added_imps}
    ev = rev2.evaluate_py_v2_in_loop(sample, cand)
    if not ev["compiled"]:
        st = "VERBATIM_COMPILE_FAIL"
    elif not ev["w_plus_passed"]:
        st = "W_PLUS_FAIL"
    elif ev.get("gate_status_override") in ("PARTNER_MUT_EVAL_ERROR", "TARGET_MUT_UNKNOWN", "PARTNER_MUT_UNKNOWN"):
        st = ev["gate_status_override"]
    elif ev.get("gate_status_override") == "ABSTAIN_PARTNER_UNCOVERED":
        st = "ABSTAIN_PARTNER_UNCOVERED"
    elif not ev["pit_ran"]:
        st = "MUTATION_EVAL_FAIL"
    elif ev["eroded"]:
        st = "ERODED_W_PLUS_PASS"
    else:
        st = "OPERATIONAL_SOUND_FIX"
    rec.update({
        "stage_outcome": st,
        "compiled": ev["compiled"],
        "w_plus_passed": ev["w_plus_passed"],
        "pit_ran": ev["pit_ran"],
        "scope_kind": ev.get("scope_kind", "TARGET_ONLY"),
        "affects_partner_or_shared": ev.get("affects_partner_or_shared", False),
        "gate_status_override": ev.get("gate_status_override"),
        "gate_safe": ev.get("gate_safe", False),
        "eroded": ev["eroded"],
        "sound_fix": ev["sound_fix"],
        "target_retention": ev.get("target_retention"),
        "partner_retention": ev.get("partner_retention"),
        "joint_retention": ev.get("joint_retention"),
        "retention_m_det": ev["retention_m_det"],
        "survived_count": len(ev.get("survived_mutants", [])),
        "target_unknown_count": ev.get("target_unknown_count", 0),
        "partner_unknown_count": ev.get("partner_unknown_count", 0),
        "eval": ev,
    })
    return {"ipflakies": rec}


# ============================================================================
# MAIN
# ============================================================================

def main() -> None:
    t0 = time.time()
    bench_exec = json.load(open(EXEC_JSON, encoding="utf-8"))
    s_by_id: Dict[str, Dict[str, Any]] = {s["sample_id"]: s for s in bench_exec["samples"]}
    upstream = rfreeze.build_upstream_indices()
    bundle_v23 = json.load(open(BUNDLE_V23_JSON, encoding="utf-8")) if os.path.exists(BUNDLE_V23_JSON) else {"od_heldout_items": []}

    # ------------------------------------------------------------------
    # STAGE 1 (R5-1): Fresh 3/3 clean-e_0 Java PIT re-freeze on ALL 110 eligible Java samples
    # ------------------------------------------------------------------
    java_elig_sids = sorted(s["sample_id"] for s in s_by_id.values() if s["language"] == "java" and s.get("v2_eligible"))
    ckpt1: Dict[str, Any] = json.load(open(CKPT_STAGE1, encoding="utf-8")) if os.path.exists(CKPT_STAGE1) else {}
    stage1_diff: Dict[str, Any] = json.load(open(STAGE1_DIFF_OUT, encoding="utf-8")) if os.path.exists(STAGE1_DIFF_OUT) else {}
    for sid, upd in ckpt1.items():
        if sid in s_by_id:
            s_by_id[sid].update(upd)
    rem1 = [sid for sid in java_elig_sids if sid not in ckpt1]
    log(f"=== STAGE 1 (R5-1): Fresh 3/3 clean-e_0 Java PIT re-freeze ({len(ckpt1)} cached, {len(rem1)} rem of {len(java_elig_sids)}) ===")
    if rem1:
        def _on_s1(sid: str, pair: Tuple[Dict[str, Any], Dict[str, Any]], _: int) -> None:
            upd, drec = pair
            with SAVE_LOCK:
                s_by_id[sid].update(upd)
                ckpt1[sid] = upd
                stage1_diff[sid] = drec
                n = len(ckpt1)
                if n % 5 == 0 or n == len(java_elig_sids):
                    log(
                        f"  [STAGE 1 3x-PIT] {n}/{len(java_elig_sids)} done | "
                        f"last={sid} |M_det|={drec['m_det_old']}->{drec['m_det_new']} "
                        f"|P_det|={drec['polluter_m_det_old']}->{drec['polluter_m_det_new']} "
                        f"|M_ext|={drec['m_ext_vf_old']+drec['m_ext_call_old']}->{drec['m_ext_vf_new']+drec['m_ext_call_new']}"
                    )
                    atomic_write(CKPT_STAGE1, ckpt1)
                    atomic_write(STAGE1_DIFF_OUT, stage1_diff, indent=2)

        run_lpt_distinct_project(
            items=rem1,
            proj_key_fn=lambda sid: f"java:{s_by_id[sid]['sha']}:{s_by_id[sid]['project_name']}",
            work_fn=lambda sid: (sid, fresh_3x_refreeze_java_sample(s_by_id[sid])),
            on_result=_on_s1,
            stage_label="STAGE1",
            max_workers=3,
        )
        atomic_write(CKPT_STAGE1, ckpt1)
        atomic_write(STAGE1_DIFF_OUT, stage1_diff, indent=2)

    n_mdet_chg = sum(1 for d in stage1_diff.values() if d.get("m_det_changed"))
    n_pdet_chg = sum(1 for d in stage1_diff.values() if d.get("polluter_m_det_changed"))
    n_mext_chg = sum(1 for d in stage1_diff.values() if d.get("any_mext_pool_changed"))
    n_dropped_elig = sum(1 for d in stage1_diff.values() if not d.get("eligible_after"))
    log(
        f"  STAGE 1 SUMMARY: total={len(stage1_diff)}, m_det_changed={n_mdet_chg}, "
        f"partner_changed={n_pdet_chg}, m_ext_changed={n_mext_chg}, dropped_below_3={n_dropped_elig}"
    )

    # ------------------------------------------------------------------
    # STAGE 2 (R5-4): Matched Unmodified Upstream Controls (All eligible OD samples)
    # ------------------------------------------------------------------
    od_elig_sids = sorted(
        s["sample_id"] for s in s_by_id.values()
        if s.get("v2_eligible") and s["test_type"] == "OD"
    )
    ckpt2: Dict[str, Any] = json.load(open(CKPT_STAGE2, encoding="utf-8")) if os.path.exists(CKPT_STAGE2) else {}
    rem2 = [sid for sid in od_elig_sids if sid not in ckpt2]
    log(f"=== STAGE 2 (R5-4): Matched unmodified upstream OD controls ({len(ckpt2)} cached, {len(rem2)} rem of {len(od_elig_sids)}) ===")
    if rem2:
        def _on_s2(sid: str, ctrl: Dict[str, Any], _: int) -> None:
            with SAVE_LOCK:
                ckpt2[sid] = ctrl
                n = len(ckpt2)
                if n % 15 == 0 or n == len(od_elig_sids):
                    log(f"  [STAGE 2 UPSTREAM CTRL] {n}/{len(od_elig_sids)} done")
                    atomic_write(CKPT_STAGE2, ckpt2, indent=2)

        run_lpt_distinct_project(
            items=rem2,
            proj_key_fn=lambda sid: f"{s_by_id[sid]['language']}:{s_by_id[sid]['sha']}:{s_by_id[sid]['project_name']}",
            work_fn=lambda sid: (sid, run_upstream_control_sample(s_by_id[sid])),
            on_result=_on_s2,
            stage_label="STAGE2",
            max_workers=3,
        )
        atomic_write(CKPT_STAGE2, ckpt2, indent=2)

    for sid, ctrl in ckpt2.items():
        if sid in s_by_id:
            s_by_id[sid]["upstream_repeated_control"] = ctrl

    py_nonidemp = sum(1 for c in ckpt2.values() if c.get("language") == "python" and c.get("upstream_nonidempotent_p2_call_fail_only"))
    java_nonidemp = sum(1 for c in ckpt2.values() if c.get("language") == "java" and c.get("upstream_nonidempotent_p2_call_fail_only"))
    log(f"  STAGE 2 SUMMARY: py_upstream_p2_nonidempotent={py_nonidemp}, java_upstream_p2_nonidempotent={java_nonidemp}")

    # ------------------------------------------------------------------
    # STAGE 3 (R5-3 + R5-4): Exact OD Held-Out Repeated-Pollution Refresh (Py & Java OD)
    # ------------------------------------------------------------------
    od_ho_items = [
        it for it in bundle_v23.get("od_heldout_items", [])
        if it["sample_id"] in s_by_id and s_by_id[it["sample_id"]].get("v2_eligible")
    ]
    ckpt3: Dict[str, Any] = json.load(open(CKPT_STAGE3, encoding="utf-8")) if os.path.exists(CKPT_STAGE3) else {}
    rem3 = [it for it in od_ho_items if it["cand_key"] not in ckpt3]
    log(f"=== STAGE 3 (R5-3/R5-4): Exact OD held-out W+ refresh ({len(ckpt3)} cached, {len(rem3)} rem of {len(od_ho_items)}) ===")
    if rem3:
        def _on_s3(ck: str, res_w: Dict[str, Any], _: int) -> None:
            with SAVE_LOCK:
                ckpt3[ck] = res_w
                n = len(ckpt3)
                if n % 25 == 0 or n == len(od_ho_items):
                    log(f"  [STAGE 3 OD HELDOUT W+] {n}/{len(od_ho_items)} done")
                    atomic_write(CKPT_STAGE3, ckpt3, indent=2)

        run_lpt_distinct_project(
            items=rem3,
            proj_key_fn=lambda it: f"{s_by_id[it['sample_id']]['language']}:{s_by_id[it['sample_id']]['sha']}:{s_by_id[it['sample_id']]['project_name']}",
            work_fn=lambda it: (
                it["cand_key"],
                refresh_od_heldout_wplus(s_by_id[it["sample_id"]], it["candidate"], ckpt2.get(it["sample_id"], {})),
            ),
            on_result=_on_s3,
            stage_label="STAGE3",
            max_workers=3,
        )
        atomic_write(CKPT_STAGE3, ckpt3, indent=2)

    # ------------------------------------------------------------------
    # STAGE 4 (R5-5): Atomic Same-Tree PR Full Dual-Role Gate on ALL Java PRs
    # ------------------------------------------------------------------
    pr_sids = sorted(
        s["sample_id"] for s in s_by_id.values()
        if s["language"] == "java" and s.get("v2_eligible") and s.get("pr_url")
    )
    ckpt4: Dict[str, Any] = json.load(open(CKPT_STAGE4, encoding="utf-8")) if os.path.exists(CKPT_STAGE4) else {}
    rem4 = [sid for sid in pr_sids if sid not in ckpt4]
    log(f"=== STAGE 4 (R5-5): Atomic same-tree PR full dual-role gate ({len(ckpt4)} cached, {len(rem4)} rem of {len(pr_sids)}) ===")
    if rem4:
        def _on_s4(sid: str, pr_res: Dict[str, Any], _: int) -> None:
            with SAVE_LOCK:
                ckpt4[sid] = pr_res
                n = len(ckpt4)
                if n % 10 == 0 or n == len(pr_sids):
                    log(f"  [STAGE 4 ATOMIC PR] {n}/{len(pr_sids)} done")
                    atomic_write(CKPT_STAGE4, ckpt4, indent=2)

        run_lpt_distinct_project(
            items=rem4,
            proj_key_fn=lambda sid: f"java:{s_by_id[sid]['sha']}:{s_by_id[sid]['project_name']}",
            work_fn=lambda sid: (sid, eval_pr_full_gate_atomic(s_by_id[sid])),
            on_result=_on_s4,
            stage_label="STAGE4",
            max_workers=3,
        )
        atomic_write(CKPT_STAGE4, ckpt4, indent=2)

    for sid, pr_res in ckpt4.items():
        if sid in s_by_id:
            if pr_res.get("pr_manifest") is not None:
                s_by_id[sid]["v2_pr_manifest"] = pr_res["pr_manifest"]
            s_by_id[sid]["v2_dev_pr_eval"] = pr_res.get("v2_dev_pr_eval")
            s_by_id[sid]["m_gold_ids"] = pr_res.get("m_gold_ids") or []

    # ------------------------------------------------------------------
    # STAGE 5 (R5-2): Unified 2.3.0-certified B1 Replay (Java FlakyDoctor + Py iPFlakies)
    # ------------------------------------------------------------------
    b1_sids = sorted(
        s["sample_id"] for s in s_by_id.values()
        if s.get("v2_eligible") and (
            (s["language"] == "java" and (s.get("b1_gpt_patch_path") or s.get("b1_magicoder_patch_path")))
            or (s["language"] == "python" and s.get("b1_ipflakies_patch_path"))
        )
    )
    ckpt5: Dict[str, Any] = json.load(open(CKPT_STAGE5, encoding="utf-8")) if os.path.exists(CKPT_STAGE5) else {}
    rem5 = [sid for sid in b1_sids if sid not in ckpt5]
    log(f"=== STAGE 5 (R5-2): Unified 2.3.0-certified B1 replay ({len(ckpt5)} cached, {len(rem5)} rem of {len(b1_sids)}) ===")
    if rem5:
        def _do_b1(sid: str) -> Tuple[str, Dict[str, Any]]:
            smp = s_by_id[sid]
            if smp["language"] == "java":
                return sid, replay_b1_java_unified(smp, upstream)
            return sid, replay_b1_py_unified(smp, upstream)

        def _on_s5(sid: str, b1_d: Dict[str, Any], _: int) -> None:
            with SAVE_LOCK:
                ckpt5[sid] = b1_d
                n = len(ckpt5)
                if n % 15 == 0 or n == len(b1_sids):
                    log(f"  [STAGE 5 B1 UNIFIED] {n}/{len(b1_sids)} done")
                    atomic_write(CKPT_STAGE5, ckpt5, indent=2)

        run_lpt_distinct_project(
            items=rem5,
            proj_key_fn=lambda sid: f"{s_by_id[sid]['language']}:{s_by_id[sid]['sha']}:{s_by_id[sid]['project_name']}",
            work_fn=_do_b1,
            on_result=_on_s5,
            stage_label="STAGE5",
            max_workers=3,
        )
        atomic_write(CKPT_STAGE5, ckpt5, indent=2)

    for sid, b1_d in ckpt5.items():
        if sid in s_by_id:
            s_by_id[sid]["v2_b1_audit"] = b1_d

    # ------------------------------------------------------------------
    # STAGE 6: Persist Updated Frozen Benchmark, Manifests, and Per-Sample Caches
    # ------------------------------------------------------------------
    ordered = [s_by_id[s["sample_id"]] for s in bench_exec["samples"]]
    for s in ordered:
        s["harness_version"] = HARNESS_VERSION
    frozen_ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    exec_payload = {
        "frozen_at": frozen_ts,
        "harness_version": HARNESS_VERSION,
        "initial_attempted_count": len(ordered),
        "eligible_count": sum(1 for s in ordered if s.get("v2_eligible")),
        "samples": ordered,
    }
    atomic_write(EXEC_JSON, exec_payload)
    exec_sha = sha256_hex(open(EXEC_JSON, "rb").read())

    light_samples = [rfreeze.strip_all_exec_blobs(s) for s in ordered]
    light_payload = {
        "frozen_at": frozen_ts,
        "harness_version": HARNESS_VERSION,
        "exec_benchmark_sha256": exec_sha,
        "initial_attempted_count": len(ordered),
        "eligible_count": sum(1 for s in ordered if s.get("v2_eligible")),
        "attrition_samples": [
            {"sample_id": s["sample_id"], "v1_m_det": s["v1_m_det_count"], "v2_m_det": s["m_det_count"]}
            for s in ordered if not s.get("v2_eligible")
        ],
        "samples": light_samples,
    }
    atomic_write(LIGHT_JSON, light_payload, indent=2)

    pr_manifest = [s["v2_pr_manifest"] for s in ordered if s.get("v2_pr_manifest")]
    b1_manifest = []
    for s in ordered:
        for _, brec in (s.get("v2_b1_audit") or {}).items():
            b1_manifest.append({k: v for k, v in brec.items() if k != "eval"})
    atomic_write(PR_MANIFEST_JSON, {"frozen_at": frozen_ts, "harness_version": HARNESS_VERSION, "entries": pr_manifest}, indent=2)
    atomic_write(B1_MANIFEST_JSON, {"frozen_at": frozen_ts, "harness_version": HARNESS_VERSION, "entries": b1_manifest}, indent=2)

    os.makedirs(SAMPLES_DIR, exist_ok=True)
    for s in ordered:
        sid_hash = hashlib.sha1(s["sample_id"].encode("utf-8")).hexdigest()[:20]
        sc = dict(s)
        sc["_frozen_exec_sha256"] = exec_sha
        atomic_write(os.path.join(SAMPLES_DIR, f"{sid_hash}.json"), sc)

    log(
        f"=== V2.3 CERTIFICATION PIPELINE COMPLETE in {(time.time()-t0)/60:.1f} min | "
        f"eligible={light_payload['eligible_count']}/{len(ordered)} | sha={exec_sha[:16]} ==="
    )


if __name__ == "__main__":
    main()
