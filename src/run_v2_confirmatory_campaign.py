"""
FT-1 v2.0.0-frozen Confirmatory Campaign Runner:
  1) `--mode eval`: Runs `B0`, `B2`, `B3`, `B3_Plus_Structural_Guard`, `B4_Scalar_Gate_Loop`,
     and `Ours_MuGuard_Flake` under the closed-fail `v2` harness (`remote_eval_v2.py in_loop_gate`),
     genuinely generating and evaluating all divergent multi-round trajectories (zero spliced suffixes).
  2) `--mode forked_ablation`: Runs strictly prefix-forked `A1_ScalarRatio`, `A2_MisleadingLocation`,
     and `A3_TrueWitness` from identical frozen `EROSION_RETRY` states with interleaved execution order.
  3) `--mode heldout`: Runs post-freeze external validation (`W+_heldout` + frozen `M_ext` pools)
     on all accepted candidates AFTER all repair loops are frozen.
"""
import argparse
import base64
import hashlib
import json
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ast_guard_and_prompts import (
    build_repair_prompt,
    check_static_ast_guard,
    parse_llm_patch_response,
)
from python_ast_guard_and_prompts import (
    build_py_repair_prompt,
    check_py_basic_sanity,
    check_py_static_ast_guard,
    parse_py_llm_patch_response,
)
from remote_and_llm import DATA_DIR, run_ssh, scp_to_remote
from run_full_study_matrix import (
    call_llm_cached,
    candidate_fingerprint,
    check_java_basic_sanity,
    load_disk_llm_cache,
    log,
    safe_model_tag,
    save_disk_llm_cache,
)
from v2_guards_and_attribution import (
    HARNESS_VERSION,
    build_matched_ablation_feedback,
    check_java_b3_plus,
    check_py_b3_plus,
)

REMOTE_EVAL_SEM = threading.Semaphore(3)
PROJ_LOCKS: Dict[str, threading.Lock] = defaultdict(threading.Lock)
SAVE_LOCK = threading.Lock()
V2_CACHE_LOCK = threading.Lock()

V2_STRATEGIES_K5 = [
    "B0_Standard_Validator",
    "B2_Prompt_Guard",
    "B3_Static_AST_Guard",
    "B3_Plus_Structural_Guard",
    "B4_Scalar_Gate_Loop",
    "Ours_MuGuard_Flake",
]

V2_STRATEGIES_K1 = [
    "B0_Standard_Validator",
    "B2_Prompt_Guard",
    "B3_Static_AST_Guard",
    "B3_Plus_Structural_Guard",
    "Ours_MuGuard_Flake",
]


def load_v2_eval_cache() -> Tuple[Path, Dict[str, Dict[str, Any]]]:
    p = DATA_DIR / "v2_eval_cache.json"
    if p.exists():
        try:
            return p, json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return p, {}


def save_v2_eval_cache(p: Path, cache: Dict[str, Dict[str, Any]]) -> None:
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


def build_v2_prompt(
    strategy: str,
    sample: Dict[str, Any],
    round_idx: int,
    prev_candidate: Optional[Dict[str, Any]] = None,
    feedback_msg: str = "",
) -> str:
    # Map B3+, B4, and Fork_* to guarded prompt template (`B2_Prompt_Guard` prompt header) so Round 1 is 100% shared!
    mapped = (
        "B0_Standard_Validator"
        if strategy == "B0_Standard_Validator"
        else "Ours_MuGuard_Flake"
    )
    if sample.get("language") == "python":
        return build_py_repair_prompt(mapped, sample, round_idx, prev_candidate, feedback_msg)
    return build_repair_prompt(mapped, sample, round_idx, prev_candidate, feedback_msg)


def parse_v2_response(raw_text: str, sample: Dict[str, Any]) -> Dict[str, Any]:
    if sample.get("language") == "python":
        return parse_py_llm_patch_response(raw_text)
    return parse_llm_patch_response(raw_text, sample["test_type"])


def remote_evaluate_v2(
    sample: Dict[str, Any],
    cand: Dict[str, Any],
    bench_sha: str,
    eval_cache: Dict[str, Dict[str, Any]],
    eval_cache_path: Path,
    mode: str = "in_loop_gate",
) -> Tuple[Dict[str, Any], bool]:
    sid = sample["sample_id"]
    c_fp = candidate_fingerprint(cand)
    ckey = f"{HARNESS_VERSION}::{bench_sha[:12]}::{mode}::{sid}::{c_fp}"

    with V2_CACHE_LOCK:
        if ckey in eval_cache:
            return dict(eval_cache[ckey]), True

    b64_cand = base64.b64encode(json.dumps(cand, ensure_ascii=False).encode("utf-8")).decode("ascii")
    pkey = f"{sample['language']}:{sample['sha']}:{sample['project_name']}"
    cmd = f"python3 /root/sqj_work/FT1/scripts/remote_eval_v2.py '{mode}' '{sid}' '{b64_cand}'"

    last_exc: Optional[Exception] = None
    for attempt in range(5):
        try:
            with PROJ_LOCKS[pkey]:
                with REMOTE_EVAL_SEM:
                    rc_c, out, err = run_ssh(cmd, timeout=320, retries=3)
            s_tag, e_tag = "===FT1_V2_EVAL_START===", "===FT1_V2_EVAL_END==="
            if s_tag in out and e_tag in out:
                ev = json.loads(out.split(s_tag, 1)[1].split(e_tag, 1)[0].strip())
                with V2_CACHE_LOCK:
                    eval_cache[ckey] = dict(ev)
                    if len(eval_cache) % 5 == 0:
                        save_v2_eval_cache(eval_cache_path, eval_cache)
                return ev, False
            last_exc = RuntimeError(f"V2 eval parse fail on {sid} (rc={rc_c}): {out[-300:]} {err[-200:]}")
        except Exception as e:
            last_exc = e
        time.sleep(3 * (attempt + 1))
    raise last_exc or RuntimeError(f"V2 remote eval failed on {sid}")


def compact_eval_dict(ev: Dict[str, Any]) -> Dict[str, Any]:
    ec = dict(ev)
    surv = ec.pop("survived_mutants", [])
    ec["survived_role_ids"] = [f"{m.get('role','target')}::{m['id']}" for m in surv if "id" in m]
    ec["survived_mutant_summaries"] = [
        f"[{m.get('role','target')}] {m.get('mutatedClass','').split('.')[-1]}.{m.get('mutatedMethod','')}:L{m.get('lineNumber',0)} ({m.get('mutator','').split('.')[-1]}: {m.get('description','')})"
        for m in surv
    ]
    return ec


def run_v2_strategy_loop(
    strategy: str,
    sample: Dict[str, Any],
    bench_sha: str,
    model: str,
    max_rounds: int,
    disk_cache: Dict[str, Dict[str, Any]],
    cache_path: Path,
    eval_cache: Dict[str, Dict[str, Any]],
    eval_cache_path: Path,
) -> Dict[str, Any]:
    lang = sample.get("language", "java")
    ttype = sample["test_type"]
    orig_methods: Dict[str, str] = {"victim": sample["orig_test_code"]}
    if (ttype == "OD" or lang == "python") and sample.get("orig_polluter_code"):
        orig_methods["polluter"] = sample["orig_polluter_code"]

    rounds_trace: List[Dict[str, Any]] = []
    prev_cand: Optional[Dict[str, Any]] = None
    feedback_msg = ""

    accepted_round: Optional[int] = None
    final_eval: Optional[Dict[str, Any]] = None
    final_cand: Optional[Dict[str, Any]] = None
    erosion_retries = 0
    ast_rejections = 0

    is_mutation_gated = strategy in ("B4_Scalar_Gate_Loop", "Ours_MuGuard_Flake")

    for r_idx in range(1, max_rounds + 1):
        prompt = build_v2_prompt(strategy, sample, r_idx, prev_cand, feedback_msg)
        prompt_sha = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]
        llm_resp, llm_cached = call_llm_cached(
            model=model,
            prompt=prompt,
            purpose=f"v2_{strategy}_{sample['sample_id']}_r{r_idx}",
            disk_cache=disk_cache,
            cache_path=cache_path,
        )
        cand = parse_v2_response(llm_resp["content"], sample)
        cand_fp = candidate_fingerprint(cand)
        if lang == "java" and ttype == "ID":
            cand_methods = {"victim": cand.get("test_code") or ""}
        else:
            cand_methods = {"victim": cand.get("victim_code") or ""}
            if cand.get("polluter_code"):
                cand_methods["polluter"] = cand["polluter_code"]

        # 1. Pre-execution Static / Sanity Gate
        if strategy == "B3_Static_AST_Guard":
            ast_ok, ast_errs = (
                check_py_static_ast_guard(orig_methods, cand_methods)
                if lang == "python"
                else check_static_ast_guard(orig_methods, cand_methods)
            )
            if not ast_ok:
                ast_rejections += 1
                feedback_msg = (
                    "STATIC AST GUARD REJECTED YOUR PATCH:\n- "
                    + "\n- ".join(ast_errs)
                    + "\nFix the flakiness while preserving all assertions and avoiding weak checks."
                )
                prev_cand = cand
                rounds_trace.append({
                    "round": r_idx,
                    "prompt_sha": prompt_sha,
                    "candidate_fp": cand_fp,
                    "llm_cached": llm_cached,
                    "eval_cached": False,
                    "llm_sec": llm_resp["latency_sec"],
                    "ast_rejected": True,
                    "ast_violations": ast_errs,
                    "candidate": cand,
                    "eval": None,
                    "decision": "AST_REJECT_RETRY" if r_idx < max_rounds else "AST_REJECT_FINAL",
                })
                continue
        elif strategy == "B3_Plus_Structural_Guard":
            b3p_ok, b3p_reasons = (
                check_py_b3_plus(orig_methods, cand_methods)
                if lang == "python"
                else check_java_b3_plus(orig_methods, cand_methods)
            )
            if not b3p_ok:
                ast_rejections += 1
                fmt_errs = [f"[{r['code']}] {r['detail']}" for r in b3p_reasons]
                feedback_msg = (
                    "STRUCTURAL CORRESPONDENCE GUARD (B3+) REJECTED YOUR PATCH:\n- "
                    + "\n- ".join(fmt_errs)
                    + "\nPreserve strong value-equality assertions, focal SUT calls, and avoid post-act state resets."
                )
                prev_cand = cand
                rounds_trace.append({
                    "round": r_idx,
                    "prompt_sha": prompt_sha,
                    "candidate_fp": cand_fp,
                    "llm_cached": llm_cached,
                    "eval_cached": False,
                    "llm_sec": llm_resp["latency_sec"],
                    "ast_rejected": True,
                    "ast_violations": fmt_errs,
                    "b3_plus_reason_codes": [r["code"] for r in b3p_reasons],
                    "candidate": cand,
                    "eval": None,
                    "decision": "B3PLUS_REJECT_RETRY" if r_idx < max_rounds else "B3PLUS_REJECT_FINAL",
                })
                continue
        else:
            san_ok, san_errs = (
                check_py_basic_sanity(orig_methods, cand_methods)
                if lang == "python"
                else check_java_basic_sanity(orig_methods, cand_methods)
            )
            if not san_ok:
                feedback_msg = "SYNTAX/SANITY REJECTION:\n- " + "\n- ".join(san_errs)
                prev_cand = cand
                rounds_trace.append({
                    "round": r_idx,
                    "prompt_sha": prompt_sha,
                    "candidate_fp": cand_fp,
                    "llm_cached": llm_cached,
                    "eval_cached": False,
                    "llm_sec": llm_resp["latency_sec"],
                    "ast_rejected": True,
                    "ast_violations": san_errs,
                    "candidate": cand,
                    "eval": None,
                    "decision": "SANITY_REJECT_RETRY" if r_idx < max_rounds else "SANITY_REJECT_FINAL",
                })
                continue

        # 2. Remote Execution Evaluation (`in_loop_gate`)
        ev_full, eval_cached = remote_evaluate_v2(sample, cand, bench_sha, eval_cache, eval_cache_path, mode="in_loop_gate")
        ev_comp = compact_eval_dict(ev_full)

        if not ev_full["compiled"]:
            feedback_msg = f"COMPILATION / SYNTAX FAILED:\n```\n{ev_full['compile_error']}\n```\nFix all errors."
            prev_cand = cand
            rounds_trace.append({
                "round": r_idx,
                "prompt_sha": prompt_sha,
                "candidate_fp": cand_fp,
                "llm_cached": llm_cached,
                "eval_cached": eval_cached,
                "llm_sec": llm_resp["latency_sec"],
                "ast_rejected": False,
                "candidate": cand,
                "eval": ev_comp,
                "decision": "COMPILE_RETRY" if r_idx < max_rounds else "ABSTAIN_COMPILE",
            })
            continue

        if not ev_full["w_plus_passed"]:
            feedback_msg = (
                f"STABILITY VALIDATION (W+) FAILED:\n```\n{ev_full['w_plus_diag']}\n```\n"
                "Your patch compiled, but failed NonDex / order execution."
            )
            prev_cand = cand
            rounds_trace.append({
                "round": r_idx,
                "prompt_sha": prompt_sha,
                "candidate_fp": cand_fp,
                "llm_cached": llm_cached,
                "eval_cached": eval_cached,
                "llm_sec": llm_resp["latency_sec"],
                "ast_rejected": False,
                "candidate": cand,
                "eval": ev_comp,
                "decision": "UNSTABLE_RETRY" if r_idx < max_rounds else "ABSTAIN_UNSTABLE",
            })
            continue

        # Candidate compiled AND passed in-loop W+!
        if not is_mutation_gated:
            accepted_round = r_idx
            final_eval = ev_comp
            final_cand = cand
            rounds_trace.append({
                "round": r_idx,
                "prompt_sha": prompt_sha,
                "candidate_fp": cand_fp,
                "llm_cached": llm_cached,
                "eval_cached": eval_cached,
                "llm_sec": llm_resp["latency_sec"],
                "ast_rejected": False,
                "candidate": cand,
                "eval": ev_comp,
                "decision": "ACCEPTED",
            })
            break

        # Closed-Fail Mutation Gate (`B4_Scalar_Gate_Loop` & `Ours_MuGuard_Flake`)
        override = ev_full.get("gate_status_override")
        if override == "ABSTAIN_PARTNER_UNCOVERED":
            if r_idx < max_rounds:
                feedback_msg = (
                    "PARTNER SCOPE UNVERIFIABLE: The partner test has zero baseline focal mutation obligations, "
                    "so modifying the partner test or shared fixture cannot be verified safe. "
                    "Leave the partner test completely unchanged and isolate/reset state strictly at the start of the target test!"
                )
                prev_cand = cand
                rounds_trace.append({
                    "round": r_idx,
                    "prompt_sha": prompt_sha,
                    "candidate_fp": cand_fp,
                    "llm_cached": llm_cached,
                    "eval_cached": eval_cached,
                    "llm_sec": llm_resp["latency_sec"],
                    "ast_rejected": False,
                    "candidate": cand,
                    "eval": ev_comp,
                    "decision": "PARTNER_UNCOVERED_RETRY",
                })
                continue
            else:
                rounds_trace.append({
                    "round": r_idx,
                    "prompt_sha": prompt_sha,
                    "candidate_fp": cand_fp,
                    "llm_cached": llm_cached,
                    "eval_cached": eval_cached,
                    "llm_sec": llm_resp["latency_sec"],
                    "ast_rejected": False,
                    "candidate": cand,
                    "eval": ev_comp,
                    "decision": "ABSTAIN_PARTNER_UNCOVERED",
                })
                break

        if not ev_full.get("pit_ran") or override == "PARTNER_MUT_EVAL_ERROR" or ev_full.get("joint_retention") is None:
            if r_idx < max_rounds:
                feedback_msg = (
                    f"FOCAL MUTATION PROBE ERROR:\n```\n{ev_full.get('w_plus_diag', '')}\n```\n"
                    "Ensure your test compiles and executes cleanly in standalone isolation."
                )
                prev_cand = cand
                rounds_trace.append({
                    "round": r_idx,
                    "prompt_sha": prompt_sha,
                    "candidate_fp": cand_fp,
                    "llm_cached": llm_cached,
                    "eval_cached": eval_cached,
                    "llm_sec": llm_resp["latency_sec"],
                    "ast_rejected": False,
                    "candidate": cand,
                    "eval": ev_comp,
                    "decision": "EVAL_ERROR_RETRY",
                })
                continue
            else:
                rounds_trace.append({
                    "round": r_idx,
                    "prompt_sha": prompt_sha,
                    "candidate_fp": cand_fp,
                    "llm_cached": llm_cached,
                    "eval_cached": eval_cached,
                    "llm_sec": llm_resp["latency_sec"],
                    "ast_rejected": False,
                    "candidate": cand,
                    "eval": ev_comp,
                    "decision": "ABSTAIN_EVAL_ERROR",
                })
                break

        if ev_full["gate_safe"]:
            accepted_round = r_idx
            final_eval = ev_comp
            final_cand = cand
            rounds_trace.append({
                "round": r_idx,
                "prompt_sha": prompt_sha,
                "candidate_fp": cand_fp,
                "llm_cached": llm_cached,
                "eval_cached": eval_cached,
                "llm_sec": llm_resp["latency_sec"],
                "ast_rejected": False,
                "candidate": cand,
                "eval": ev_comp,
                "decision": "SAFE_PASS",
            })
            break
        else:
            # `eroded == True`!
            if r_idx < max_rounds:
                erosion_retries += 1
                surv = ev_full.get("survived_mutants", [])
                surv_ids = {m["id"] for m in surv}
                non_surv = [m for m in sample["m_det"] if m["id"] not in surv_ids]
                fb_mode = "A1_ScalarRatio" if strategy == "B4_Scalar_Gate_Loop" else "A3_TrueWitness"
                fb_obj = build_matched_ablation_feedback(
                    language=lang,
                    total_obligations=ev_full["joint_m_det_total"],
                    survived_mutants=surv,
                    non_survived_pool=non_surv,
                    mode=fb_mode,
                )
                feedback_msg = fb_obj["text"]
                prev_cand = cand
                rounds_trace.append({
                    "round": r_idx,
                    "prompt_sha": prompt_sha,
                    "candidate_fp": cand_fp,
                    "llm_cached": llm_cached,
                    "eval_cached": eval_cached,
                    "llm_sec": llm_resp["latency_sec"],
                    "ast_rejected": False,
                    "feedback_meta": {k: fb_obj[k] for k in ("mode", "shown_count", "char_len", "approx_tokens")},
                    "candidate": cand,
                    "eval": ev_comp,
                    "decision": "EROSION_RETRY",
                })
                continue
            else:
                rounds_trace.append({
                    "round": r_idx,
                    "prompt_sha": prompt_sha,
                    "candidate_fp": cand_fp,
                    "llm_cached": llm_cached,
                    "eval_cached": eval_cached,
                    "llm_sec": llm_resp["latency_sec"],
                    "ast_rejected": False,
                    "candidate": cand,
                    "eval": ev_comp,
                    "decision": "ABSTAIN_EROSION",
                })
                break

    accepted = accepted_round is not None and final_eval is not None
    gate_state = rounds_trace[-1]["decision"] if rounds_trace else "UNRESOLVED"
    w_plus_ok = bool(accepted and final_eval.get("w_plus_passed"))
    eroded = bool(accepted and final_eval.get("eroded"))
    gate_safe = bool(accepted and final_eval.get("gate_safe"))
    sound_fix = bool(accepted and w_plus_ok and gate_safe and not eroded)

    return {
        "strategy": strategy,
        "harness_version": HARNESS_VERSION,
        "gate_state": gate_state,
        "accepted": accepted,
        "accepted_round": accepted_round,
        "w_plus_passed": w_plus_ok,
        "gate_safe": gate_safe,
        "eroded": eroded,
        "sound_fix": sound_fix,
        "target_retention": final_eval.get("target_retention") if final_eval else None,
        "partner_retention": final_eval.get("partner_retention") if final_eval else None,
        "joint_retention": final_eval.get("joint_retention") if final_eval else None,
        "retention_m_det": final_eval.get("joint_retention") if final_eval else None,
        "retention_m_gold": final_eval.get("retention_m_gold") if final_eval else None,
        "scope_kind": final_eval.get("scope_kind") if final_eval else None,
        "erosion_retries": erosion_retries,
        "ast_rejections": ast_rejections,
        "final_candidate": final_cand,
        "final_eval": final_eval,
        "rounds": rounds_trace,
    }


def run_eval_campaign(bench_data: Dict[str, Any], model: str, max_rounds: int, workers: int) -> None:
    src_dir = Path(__file__).resolve().parent
    for fn in ("v2_guards_and_attribution.py", "v2_pytest_attribution_runner.py", "remote_v2_freeze_benchmark_and_mext.py", "remote_eval_v2.py"):
        scp_to_remote(src_dir / fn, f"scripts/{fn}")

    bench_sha = bench_data["exec_benchmark_sha256"]
    samples = bench_data["samples"]
    ordered_ids = [s["sample_id"] for s in samples]
    strats_to_run = V2_STRATEGIES_K5 if max_rounds > 1 else V2_STRATEGIES_K1

    cache_path, disk_cache = load_disk_llm_cache(model)
    eval_cache_path, eval_cache = load_v2_eval_cache()

    tag_prefix = "v2_results" if max_rounds > 1 else "v2_round1"
    out_path = DATA_DIR / f"{tag_prefix}_{safe_model_tag(model)}.json"
    completed_map: Dict[str, Any] = {}
    if out_path.exists():
        for r in json.loads(out_path.read_text(encoding="utf-8")).get("results", []):
            if r.get("exec_benchmark_sha256") == bench_sha and set(strats_to_run).issubset(set(r.get("strategies", {}).keys())):
                completed_map[r["sample_id"]] = r

    todo = [s for s in samples if s["sample_id"] not in completed_map]
    log(f"[V2 EVAL {model} K={max_rounds}] {len(completed_map)}/{len(samples)} done; running {len(todo)} remaining...")

    def _save_now() -> None:
        ordered = [completed_map[sid] for sid in ordered_ids if sid in completed_map]
        pay = {
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "harness_version": HARNESS_VERSION,
            "exec_benchmark_sha256": bench_sha,
            "model": model,
            "max_rounds": max_rounds,
            "completed_count": len(ordered),
            "total_target": len(ordered_ids),
            "results": ordered,
        }
        tmp = out_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(pay, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(out_path)

    _save_now()

    def _do_sample(smp: Dict[str, Any]) -> Dict[str, Any]:
        t0 = time.time()
        s_res: Dict[str, Any] = {}
        for st in strats_to_run:
            s_res[st] = run_v2_strategy_loop(
                strategy=st,
                sample=smp,
                bench_sha=bench_sha,
                model=model,
                max_rounds=max_rounds,
                disk_cache=disk_cache,
                cache_path=cache_path,
                eval_cache=eval_cache,
                eval_cache_path=eval_cache_path,
            )
        b0 = s_res["B0_Standard_Validator"]
        b3p = s_res["B3_Plus_Structural_Guard"]
        ours = s_res["Ours_MuGuard_Flake"]
        log(
            f"[V2 {model} {smp['sample_id']}] ({time.time()-t0:.1f}s) | "
            f"B0:sf={int(b0['sound_fix'])},er={int(b0['eroded'])} | "
            f"B3+:sf={int(b3p['sound_fix'])},er={int(b3p['eroded'])} | "
            f"Ours:{ours['gate_state']}(r={ours['accepted_round']},sf={int(ours['sound_fix'])})"
        )
        return {
            "sample_id": smp["sample_id"],
            "v2_eligible": smp.get("v2_eligible", True),
            "exec_benchmark_sha256": bench_sha,
            "language": smp.get("language", "java"),
            "test_type": smp["test_type"],
            "od_subtype": smp.get("od_subtype", smp["test_type"]),
            "project_name": smp["project_name"],
            "test_full_name": smp["test_full_name"],
            "m_det_count": smp["m_det_count"],
            "polluter_m_det_count": smp.get("polluter_m_det_count", 0),
            "m_gold_count": len(smp.get("m_gold_ids") or []),
            "strategies": s_res,
        }

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_do_sample, s): s["sample_id"] for s in todo}
        for fut in as_completed(futs):
            sid = futs[fut]
            try:
                rec = fut.result()
            except Exception as e:
                log(f"[V2 ERROR {model} {sid}]: {e}")
                continue
            with SAVE_LOCK:
                completed_map[sid] = rec
                _save_now()
    save_disk_llm_cache(cache_path, disk_cache)
    save_v2_eval_cache(eval_cache_path, eval_cache)
    log(f"=== V2 EVAL COMPLETE ({model}, K={max_rounds}): {len(completed_map)}/{len(samples)} -> {out_path} ===")


def run_prefix_forked_ablation(bench_data: Dict[str, Any], models: List[str], max_rounds: int, workers: int) -> None:
    """
    Strictly controlled prefix-forked ablation from identical frozen `EROSION_RETRY` states:
      - Enrols every `(model, sample_id)` (`v2_eligible == True`) where the shared initial trajectory
        first hits `EROSION_RETRY` at round `r_0 < max_rounds` (and records `r_0 == max_rounds` zero-budget triggers).
      - `A1_ScalarRatio` and `A3_TrueWitness` match the exact post-fork suffixes of `B4_Scalar_Gate_Loop`
        and `Ours_MuGuard_Flake` (verified by `prefix_hash` + `trigger_candidate_fp`), while `A2_MisleadingLocation`
        forks from the exact same `r_0` state with matched item count & interleaved call order.
    """
    bench_sha = bench_data["exec_benchmark_sha256"]
    s_by_id = {s["sample_id"]: s for s in bench_data["samples"]}
    eval_cache_path, eval_cache = load_v2_eval_cache()
    out_path = DATA_DIR / "v2_forked_ablation_results.json"

    existing: Dict[str, Any] = {}
    if out_path.exists():
        for c in json.loads(out_path.read_text(encoding="utf-8")).get("cases", []):
            if c.get("exec_benchmark_sha256") == bench_sha:
                existing[f"{c['model']}::{c['sample_id']}"] = c

    enrolment_tasks: List[Tuple[int, str, Dict[str, Any], Dict[str, Any], Dict[str, Any], int]] = []
    zero_budget_r5_cases: List[Dict[str, Any]] = []

    idx_counter = 0
    for model in models:
        rp = DATA_DIR / f"v2_results_{safe_model_tag(model)}.json"
        if not rp.exists():
            continue
        rows = json.loads(rp.read_text(encoding="utf-8")).get("results", [])
        for r in rows:
            sid = r["sample_id"]
            smp = s_by_id.get(sid)
            if not smp or not smp.get("v2_eligible", True):
                continue
            ours = r["strategies"]["Ours_MuGuard_Flake"]
            b4 = r["strategies"]["B4_Scalar_Gate_Loop"]
            # Find first round where W+ passed and candidate eroded
            r0_idx = None
            for rd in ours["rounds"]:
                if rd["decision"] == "EROSION_RETRY":
                    r0_idx = rd["round"]
                    break
                if rd["round"] == max_rounds and rd["decision"] == "ABSTAIN_EROSION":
                    zero_budget_r5_cases.append({"model": model, "sample_id": sid, "trigger_round": max_rounds})
            if r0_idx is not None and r0_idx < max_rounds:
                key = f"{model}::{sid}"
                if key not in existing:
                    enrolment_tasks.append((idx_counter, model, smp, b4, ours, r0_idx))
                idx_counter += 1

    log(f"[V2 FORKED ABLATION] {len(enrolment_tasks)} new forks to run (existing={len(existing)}, R5_zero_budget={len(zero_budget_r5_cases)})...")
    m_caches = {m: load_disk_llm_cache(m) for m in models}

    def _run_fork(task_tuple: Tuple[int, str, Dict[str, Any], Dict[str, Any], Dict[str, Any], int]) -> Dict[str, Any]:
        idx, model, smp, b4_traj, ours_traj, r0 = task_tuple
        cache_path, disk_cache = m_caches[model]
        lang = smp.get("language", "java")
        ttype = smp["test_type"]
        orig_methods: Dict[str, str] = {"victim": smp["orig_test_code"]}
        if (ttype == "OD" or lang == "python") and smp.get("orig_polluter_code"):
            orig_methods["polluter"] = smp["orig_polluter_code"]

        # Verify shared prefix identity at `r0`!
        rd_ours_r0 = ours_traj["rounds"][r0 - 1]
        rd_b4_r0 = b4_traj["rounds"][r0 - 1]
        assert rd_ours_r0["prompt_sha"] == rd_b4_r0["prompt_sha"], f"Prefix mismatch on {model}::{smp['sample_id']}"
        assert rd_ours_r0["candidate_fp"] == rd_b4_r0["candidate_fp"], f"Candidate mismatch on {model}::{smp['sample_id']}"

        trig_cand = rd_ours_r0["candidate"]
        ev_r0_full, _ = remote_evaluate_v2(smp, trig_cand, bench_sha, eval_cache, eval_cache_path, mode="in_loop_gate")
        surv_r0 = ev_r0_full.get("survived_mutants", [])
        surv_ids_r0 = {m["id"] for m in surv_r0}
        non_surv_r0 = [m for m in smp["m_det"] if m["id"] not in surv_ids_r0]

        def _continue_from_r0(branch_mode: str) -> Dict[str, Any]:
            fb_init = build_matched_ablation_feedback(
                language=lang,
                total_obligations=ev_r0_full["joint_m_det_total"],
                survived_mutants=surv_r0,
                non_survived_pool=non_surv_r0,
                mode=branch_mode,
            )
            prev_c = trig_cand
            fb_msg = fb_init["text"]
            post_rounds = []
            rec_round = None
            fin_ev = None
            fin_c = None

            for r_i in range(r0 + 1, max_rounds + 1):
                pr = build_v2_prompt("Ours_MuGuard_Flake", smp, r_i, prev_c, fb_msg)
                l_resp, l_cached = call_llm_cached(model, pr, f"v2_fork_{branch_mode}_{smp['sample_id']}_r{r_i}", disk_cache, cache_path)
                c_i = parse_v2_response(l_resp["content"], smp)
                if lang == "java" and ttype == "ID":
                    cm = {"victim": c_i.get("test_code") or ""}
                else:
                    cm = {"victim": c_i.get("victim_code") or ""}
                    if c_i.get("polluter_code"):
                        cm["polluter"] = c_i["polluter_code"]

                san_ok, san_errs = (check_py_basic_sanity(orig_methods, cm) if lang == "python" else check_java_basic_sanity(orig_methods, cm))
                if not san_ok:
                    fb_msg = "SYNTAX/SANITY REJECTION:\n- " + "\n- ".join(san_errs)
                    prev_c = c_i
                    post_rounds.append({"round": r_i, "decision": "SANITY_REJECT_RETRY" if r_i < max_rounds else "SANITY_REJECT_FINAL"})
                    continue

                ev_i, ev_c = remote_evaluate_v2(smp, c_i, bench_sha, eval_cache, eval_cache_path, mode="in_loop_gate")
                if not ev_i["compiled"]:
                    fb_msg = f"COMPILATION / SYNTAX FAILED:\n```\n{ev_i['compile_error']}\n```\nFix all errors."
                    prev_c = c_i
                    post_rounds.append({"round": r_i, "decision": "COMPILE_RETRY" if r_i < max_rounds else "ABSTAIN_COMPILE"})
                    continue
                if not ev_i["w_plus_passed"]:
                    fb_msg = f"STABILITY VALIDATION (W+) FAILED:\n```\n{ev_i['w_plus_diag']}\n```\nYour patch compiled, but failed NonDex / order execution."
                    prev_c = c_i
                    post_rounds.append({"round": r_i, "decision": "UNSTABLE_RETRY" if r_i < max_rounds else "ABSTAIN_UNSTABLE"})
                    continue
                if ev_i.get("gate_status_override") == "ABSTAIN_PARTNER_UNCOVERED":
                    fb_msg = (
                        "PARTNER SCOPE UNVERIFIABLE: The partner test has zero baseline focal mutation obligations, "
                        "so modifying the partner test or shared fixture cannot be verified safe. "
                        "Leave the partner test completely unchanged and isolate/reset state strictly at the start of the target test!"
                    )
                    prev_c = c_i
                    post_rounds.append({"round": r_i, "decision": "PARTNER_UNCOVERED_RETRY" if r_i < max_rounds else "ABSTAIN_PARTNER_UNCOVERED"})
                    continue
                if not ev_i.get("pit_ran") or ev_i.get("joint_retention") is None:
                    fb_msg = f"FOCAL MUTATION PROBE ERROR:\n```\n{ev_i.get('w_plus_diag', '')}\n```\nEnsure your test compiles and executes cleanly in standalone isolation."
                    prev_c = c_i
                    post_rounds.append({"round": r_i, "decision": "EVAL_ERROR_RETRY" if r_i < max_rounds else "ABSTAIN_EVAL_ERROR"})
                    continue
                if ev_i["gate_safe"]:
                    rec_round = r_i
                    fin_ev = compact_eval_dict(ev_i)
                    fin_c = c_i
                    post_rounds.append({"round": r_i, "decision": "SAFE_PASS"})
                    break
                else:
                    if r_i < max_rounds:
                        s_i = ev_i.get("survived_mutants", [])
                        s_ids = {m["id"] for m in s_i}
                        ns_i = [m for m in smp["m_det"] if m["id"] not in s_ids]
                        fb_next = build_matched_ablation_feedback(lang, ev_i["joint_m_det_total"], s_i, ns_i, branch_mode)
                        fb_msg = fb_next["text"]
                        prev_c = c_i
                        post_rounds.append({"round": r_i, "decision": "EROSION_RETRY"})
                    else:
                        post_rounds.append({"round": r_i, "decision": "ABSTAIN_EROSION"})
                        break

            recovered = rec_round is not None
            return {
                "branch_mode": branch_mode,
                "initial_feedback_meta": {k: fb_init[k] for k in ("mode", "shown_count", "char_len", "approx_tokens")},
                "recovered": recovered,
                "recovery_round": rec_round,
                "post_fork_rounds_used": (rec_round - r0) if rec_round is not None else (max_rounds - r0),
                "terminal_decision": post_rounds[-1]["decision"] if post_rounds else "ABSTAIN_EROSION",
                "final_candidate": fin_c,
                "final_eval": fin_ev,
                "post_rounds": post_rounds,
            }

        # Interleave execution order across `[A1, A2, A3]` by `idx % 3`
        modes = ["A1_ScalarRatio", "A2_MisleadingLocation", "A3_TrueWitness"]
        rot = modes[idx % 3 :] + modes[: idx % 3]
        b_out: Dict[str, Any] = {}
        for bm in rot:
            b_out[bm] = _continue_from_r0(bm)

        log(
            f"  [FORK DONE {model}::{smp['sample_id']} r0={r0}] "
            f"A1={int(b_out['A1_ScalarRatio']['recovered'])}(r={b_out['A1_ScalarRatio']['recovery_round']}) | "
            f"A2={int(b_out['A2_MisleadingLocation']['recovered'])}(r={b_out['A2_MisleadingLocation']['recovery_round']}) | "
            f"A3={int(b_out['A3_TrueWitness']['recovered'])}(r={b_out['A3_TrueWitness']['recovery_round']})"
        )
        return {
            "model": model,
            "sample_id": smp["sample_id"],
            "project_name": smp["project_name"],
            "language": lang,
            "od_subtype": smp.get("od_subtype", smp["test_type"]),
            "exec_benchmark_sha256": bench_sha,
            "prefix_prompt_sha": rd_ours_r0["prompt_sha"],
            "trigger_candidate_fp": rd_ours_r0["candidate_fp"],
            "trigger_round": r0,
            "remaining_budget": max_rounds - r0,
            "A0_FrozenGateOnly": {"recovered": False, "recovery_round": None, "note": "Descriptive zero-retry baseline"},
            "A1_ScalarRatio": b_out["A1_ScalarRatio"],
            "A2_MisleadingLocation": b_out["A2_MisleadingLocation"],
            "A3_TrueWitness": b_out["A3_TrueWitness"],
        }

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(_run_fork, t) for t in enrolment_tasks]
        for fut in as_completed(futs):
            try:
                rec = fut.result()
            except Exception as e:
                log(f"[FORK ERROR]: {e}")
                continue
            with SAVE_LOCK:
                existing[f"{rec['model']}::{rec['sample_id']}"] = rec
                tmp_ab = out_path.with_suffix(".json.tmp")
                tmp_ab.write_text(
                    json.dumps({
                        "harness_version": HARNESS_VERSION,
                        "exec_benchmark_sha256": bench_sha,
                        "zero_budget_r5_triggers": zero_budget_r5_cases,
                        "cases": list(existing.values()),
                    }, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                tmp_ab.replace(out_path)
    for _, (cp, dc) in m_caches.items():
        save_disk_llm_cache(cp, dc)
    save_v2_eval_cache(eval_cache_path, eval_cache)
    log(f"=== V2 FORKED ABLATION COMPLETE: {len(existing)} cases -> {out_path} ===")


def run_post_freeze_heldout_validation(bench_data: Dict[str, Any], models: List[str], workers: int) -> None:
    """Evaluate all unique accepted candidates across K=5 models on `W+_heldout` and frozen `M_ext`."""
    bench_sha = bench_data["exec_benchmark_sha256"]
    s_by_id = {s["sample_id"]: s for s in bench_data["samples"]}
    eval_cache_path, eval_cache = load_v2_eval_cache()
    out_path = DATA_DIR / "v2_heldout_validation.json"

    unique_cands: Dict[Tuple[str, str], Tuple[Dict[str, Any], Dict[str, Any]]] = {}
    for model in models:
        rp = DATA_DIR / f"v2_results_{safe_model_tag(model)}.json"
        if not rp.exists():
            continue
        for r in json.loads(rp.read_text(encoding="utf-8")).get("results", []):
            sid = r["sample_id"]
            smp = s_by_id.get(sid)
            if not smp:
                continue
            for st_rec in r["strategies"].values():
                if st_rec.get("accepted") and st_rec.get("final_candidate"):
                    cand = st_rec["final_candidate"]
                    cfp = candidate_fingerprint(cand)
                    unique_cands[(sid, cfp)] = (smp, cand)

    log(f"[V2 HELDOUT VALIDATION] Evaluating {len(unique_cands)} unique accepted `(sample_id, candidate_fp)` pairs on W+_heldout & M_ext...")
    ho_map: Dict[str, Any] = {}
    if out_path.exists():
        old = json.loads(out_path.read_text(encoding="utf-8"))
        if old.get("exec_benchmark_sha256") == bench_sha:
            ho_map = old.get("evaluations", {})

    todo = [(sid, cfp, smp, cand) for (sid, cfp), (smp, cand) in unique_cands.items() if f"{sid}::{cfp}" not in ho_map]
    log(f"  Already cached={len(ho_map)}, todo={len(todo)}")

    def _eval_one(item: Tuple[str, str, Dict[str, Any], Dict[str, Any]]) -> Tuple[str, Dict[str, Any]]:
        sid, cfp, smp, cand = item
        ev, _ = remote_evaluate_v2(smp, cand, bench_sha, eval_cache, eval_cache_path, mode="post_freeze_heldout")
        log(f"  [HELDOUT {sid}::{cfp[:8]}] W+_ho={ev.get('w_plus_heldout_passed')} M_ext_ret={ev.get('m_ext_all_retention')}")
        return f"{sid}::{cfp}", ev

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(_eval_one, it) for it in todo]
        for fut in as_completed(futs):
            try:
                k, ev = fut.result()
            except Exception as e:
                log(f"[HELDOUT ERROR]: {e}")
                continue
            with SAVE_LOCK:
                ho_map[k] = ev
                if len(ho_map) % 10 == 0 or len(ho_map) == len(unique_cands):
                    out_path.write_text(
                        json.dumps({
                            "harness_version": HARNESS_VERSION,
                            "exec_benchmark_sha256": bench_sha,
                            "evaluations": ho_map,
                        }, indent=2, ensure_ascii=False),
                        encoding="utf-8",
                    )
    out_path.write_text(
        json.dumps({
            "harness_version": HARNESS_VERSION,
            "exec_benchmark_sha256": bench_sha,
            "evaluations": ho_map,
        }, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    save_v2_eval_cache(eval_cache_path, eval_cache)
    log(f"=== V2 HELDOUT VALIDATION COMPLETE ({len(ho_map)} unique accepted patches) -> {out_path} ===")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["eval", "forked_ablation", "heldout"], required=True)
    parser.add_argument("--bench", default=str(DATA_DIR / "v2_frozen_benchmark_light.json"))
    parser.add_argument("--model", default="deepseek-flash")
    parser.add_argument("--models", default="deepseek-flash,deepseek-v4-pro,qwen3.7-plus")
    parser.add_argument("--max-rounds", type=int, default=5)
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()

    bench_data = json.loads(Path(args.bench).read_text(encoding="utf-8"))
    if args.mode == "eval":
        run_eval_campaign(bench_data, args.model, args.max_rounds, args.workers)
    elif args.mode == "forked_ablation":
        ms = [m.strip() for m in args.models.split(",") if m.strip()]
        run_prefix_forked_ablation(bench_data, ms, args.max_rounds, args.workers)
    elif args.mode == "heldout":
        ms = [m.strip() for m in args.models.split(",") if m.strip()]
        run_post_freeze_heldout_validation(bench_data, ms, args.workers)


if __name__ == "__main__":
    main()
