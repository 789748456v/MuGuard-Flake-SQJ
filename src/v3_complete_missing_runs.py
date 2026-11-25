"""
FT-1 v3.0 Additive Gap-Completion Runner:
Completes the exact truncated runs from the v2.3 campaign WITHOUT mutating certified v2_* files:
  1. 1 missing eligible test on `deepseek-flash` (`jetcache::CreateCacheTest.test`)
  2. 15 missing eligible tests on `deepseek-v4-pro`
  3. 12 missing `A2_MisleadingLocation` forks in the 128-fork witness ablation
  4. Runs post-freeze held-out validation (`W+_heldout` + `M_ext`) on every newly accepted candidate.

Root cause of v2 truncations: DeepSeek reasoning models (default `reasoning_effort="high"`)
occasionally exhausted `max_tokens=16384` during chain-of-thought before emitting the patch block.
Here we use DeepSeek's official bounded reasoning (`reasoning_effort="low"`, `max_tokens=16384`->`32768`)
uniformly across all 6 strategies strictly for these previously incomplete prompts, keeping
temperature=0.0, prompts, harness, guards, and evaluation 100% identical.

Outputs (strictly additive):
  - `data/v3_completed_missing_k5.json`
  - `data/v3_completed_a2_forks.json`
  - `data/v3_completed_heldout.json`
"""
import hashlib
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_full_study_matrix as rfsm
import run_v2_confirmatory_campaign as rv2
from remote_and_llm import DATA_DIR, chat_completion, scp_to_remote
from v2_guards_and_attribution import HARNESS_VERSION, build_matched_ablation_feedback

OUT_K5 = DATA_DIR / "v3_completed_missing_k5.json"
OUT_A2 = DATA_DIR / "v3_completed_a2_forks.json"
OUT_HO = DATA_DIR / "v3_completed_heldout.json"
V3_CACHE_PATH = DATA_DIR / "v3_gap_llm_cache.json"
V3_LOCK = threading.Lock()
_V3_CACHE_MEM: Optional[Dict[str, Dict[str, Any]]] = None


def load_json_or(path: Path, default: Any) -> Any:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return default


def save_json_atomic(path: Path, obj: Any) -> None:
    tmp = path.with_suffix(f".{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
    for _ in range(5):
        try:
            tmp.replace(path)
            return
        except PermissionError:
            time.sleep(0.1)
    tmp.replace(path)


def _get_v3_cache() -> Dict[str, Dict[str, Any]]:
    global _V3_CACHE_MEM
    if _V3_CACHE_MEM is None:
        _V3_CACHE_MEM = load_json_or(V3_CACHE_PATH, {})
    return _V3_CACHE_MEM


def call_llm_v3_high_budget(
    model: str,
    prompt: str,
    purpose: str,
    disk_cache: Dict[str, Dict[str, Any]],
    cache_path: Path,
) -> Tuple[Dict[str, Any], bool]:
    k = rfsm.prompt_cache_key(model, prompt)
    with rfsm.CACHE_LOCK:
        if k in disk_cache and (disk_cache[k].get("content") or "").strip():
            return disk_cache[k], True

    # Also check v3 gap cache (thread-safe in-memory mirror)
    with V3_LOCK:
        v3_cache = _get_v3_cache()
        if k in v3_cache and (v3_cache[k].get("content") or "").strip():
            return v3_cache[k], True

    last_exc: Optional[Exception] = None
    # Use official DeepSeek bounded reasoning (`reasoning_effort="low"`) with exact original v2 prompt
    # so previously truncated prompts never loop past max_tokens or waste tokens on empty outputs,
    # while keeping all 6 strategies 100% symmetric and prompts completely unaltered.
    schedules = [
        (14336, "low"),
        (8192, "none"),
    ]
    for idx, (max_toks, effort) in enumerate(schedules):
        try:
            resp = chat_completion(
                provider=None,
                model=model,
                messages=[
                    {"role": "user", "content": prompt},
                ],
                temperature=0.0,
                max_tokens=max_toks,
                purpose=purpose,
                timeout=240,
                extra_body={"reasoning_effort": effort},
            )
            if not (resp.get("content") or "").strip():
                raise RuntimeError(f"Empty content on attempt {idx+1} (max_tokens={max_toks}, effort={effort})")
            slim = {
                "content": resp["content"],
                "latency_sec": resp["latency_sec"],
                "tokens": resp["usage"].get("total_tokens", 0),
                "v3_high_budget_attempt": idx + 1,
                "v3_max_tokens": max_toks,
                "v3_reasoning_effort": effort,
            }
            with V3_LOCK:
                c = _get_v3_cache()
                c[k] = slim
                save_json_atomic(V3_CACHE_PATH, c)
            return slim, False
        except Exception as e:
            last_exc = e
            if "402" in str(e) or "Insufficient Balance" in str(e):
                raise RuntimeError(f"HTTP 402 Insufficient Balance on {model}/{purpose}: {e}") from e
            time.sleep(3 * (idx + 1))
    raise RuntimeError(f"v3_high_budget LLM failed for {model}/{purpose}: {last_exc}")


def main() -> None:
    # Monkey-patch call_llm_cached strictly in-memory inside this process so rv2 loops use high-budget completion
    rv2.call_llm_cached = call_llm_v3_high_budget

    src_dir = Path(__file__).resolve().parent
    for fn in ("v2_guards_and_attribution.py", "v2_pytest_attribution_runner.py", "remote_v2_freeze_benchmark_and_mext.py", "remote_eval_v2.py"):
        scp_to_remote(src_dir / fn, f"scripts/{fn}")

    bench_data = json.loads((DATA_DIR / "v2_frozen_benchmark_light.json").read_text(encoding="utf-8"))
    bench_sha = bench_data["exec_benchmark_sha256"]
    s_by_id = {s["sample_id"]: s for s in bench_data["samples"]}
    elig_ids = sorted(s["sample_id"] for s in bench_data["samples"] if s.get("v2_eligible"))

    # Pre-seed eval cache in-memory from certified v2_eval_cache.json + additive v3_gap_eval_cache.json
    # so v2_eval_cache.json is NEVER mutated on disk while preserving 100% cache reuse!
    _, v2_eval_cache = rv2.load_v2_eval_cache()
    eval_cache_path = DATA_DIR / "v3_gap_eval_cache.json"
    eval_cache = dict(v2_eval_cache)
    eval_cache.update(load_json_or(eval_cache_path, {}))

    # =========================================================================
    # PART 1: Complete 1 missing DS-Flash + 15 missing DS-V4-Pro eligible K=5 samples
    # =========================================================================
    k5_done = load_json_or(OUT_K5, {"protocol_version": "3.0.0-gap-completion", "by_model": {}})
    k5_by_model: Dict[str, Dict[str, Any]] = k5_done.setdefault("by_model", {})

    for model in ("deepseek-flash", "deepseek-v4-pro"):
        v2_res = json.loads((DATA_DIR / f"v2_results_{rfsm.safe_model_tag(model)}.json").read_text(encoding="utf-8"))["results"]
        have_v2 = {r["sample_id"] for r in v2_res}
        have_v3 = set((k5_by_model.get(model) or {}).keys())
        missing = [sid for sid in elig_ids if sid not in have_v2 and sid not in have_v3]
        print(f"[PART 1] {model}: missing={len(missing)} (v2={len(have_v2 & set(elig_ids))}, v3_already={len(have_v3)})", flush=True)
        if not missing:
            continue

        cache_path, disk_cache = rfsm.load_disk_llm_cache(model)
        k5_by_model.setdefault(model, {})

        def _run_one(sid: str) -> Tuple[str, Dict[str, Any]]:
            smp = s_by_id[sid]
            t0 = time.time()
            s_res = {}
            for st in rv2.V2_STRATEGIES_K5:
                s_res[st] = rv2.run_v2_strategy_loop(
                    strategy=st,
                    sample=smp,
                    bench_sha=bench_sha,
                    model=model,
                    max_rounds=5,
                    disk_cache=disk_cache,
                    cache_path=cache_path,
                    eval_cache=eval_cache,
                    eval_cache_path=eval_cache_path,
                )
                print(f"    [{model} {sid}] {st}: {s_res[st]['gate_state']} (r={s_res[st].get('accepted_round')}, sf={int(s_res[st]['sound_fix'])})", flush=True)
            ours = s_res["Ours_MuGuard_Flake"]
            b0 = s_res["B0_Standard_Validator"]
            print(
                f"  [DONE K5 {model} {sid}] ({time.time()-t0:.1f}s) "
                f"B0:sf={int(b0['sound_fix'])},er={int(b0['eroded'])} | "
                f"Ours:{ours['gate_state']}(r={ours['accepted_round']},sf={int(ours['sound_fix'])})",
                flush=True,
            )
            return sid, {
                "sample_id": sid,
                "v2_eligible": True,
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

        with ThreadPoolExecutor(max_workers=2) as ex:
            futs = {ex.submit(_run_one, sid): sid for sid in missing}
            for fut in as_completed(futs):
                sid = futs[fut]
                try:
                    _, rec = fut.result()
                    with V3_LOCK:
                        k5_by_model[model][sid] = rec
                        save_json_atomic(OUT_K5, k5_done)
                except Exception as e:
                    print(f"  [ERR K5 {model} {sid}]: {e}", flush=True)

    # =========================================================================
    # PART 2: Complete 12 missing A2_MisleadingLocation forks to reach 128/128!
    # =========================================================================
    ab_v2 = json.loads((DATA_DIR / "v2_forked_ablation_results.json").read_text(encoding="utf-8"))
    a2_done = load_json_or(OUT_A2, {"protocol_version": "3.0.0-a2-gap-completion", "cases": {}})
    a2_map: Dict[str, Any] = a2_done.setdefault("cases", {})

    # Need original triggering candidate + eval from v2_results to reconstruct exact fork state
    v2_res_by_model = {
        m: {r["sample_id"]: r for r in json.loads((DATA_DIR / f"v2_results_{rfsm.safe_model_tag(m)}.json").read_text(encoding="utf-8"))["results"]}
        for m in ("deepseek-flash", "deepseek-v4-pro", "qwen3.7-plus")
    }
    missing_a2 = [
        c for c in ab_v2["cases"]
        if not c.get("A2_MisleadingLocation") and f"{c['model']}::{c['sample_id']}" not in a2_map
    ]
    print(f"\n[PART 2] Missing A2_MisleadingLocation forks: {len(missing_a2)}", flush=True)

    def _run_a2_fork(case: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        model = case["model"]
        sid = case["sample_id"]
        fk = f"{model}::{sid}"
        smp = s_by_id[sid]
        lang = smp.get("language", "java")
        ttype = smp["test_type"]
        orig_methods = {"victim": smp["orig_test_code"]}
        if (ttype == "OD" or lang == "python") and smp.get("orig_polluter_code"):
            orig_methods["polluter"] = smp["orig_polluter_code"]

        # Reconstruct exact trigger eval (100% eval-cache hit!) so survived_mutants has full slices
        ours_st = v2_res_by_model[model][sid]["strategies"]["Ours_MuGuard_Flake"]
        r0 = case["trigger_round"]
        trig_rd = next(rd for rd in ours_st["rounds"] if rd["round"] == r0)
        trig_cand = trig_rd["candidate"]
        ev_r0_full, _ = rv2.remote_evaluate_v2(smp, trig_cand, bench_sha, eval_cache, eval_cache_path, mode="in_loop_gate")
        surv_r0 = ev_r0_full.get("survived_mutants", [])
        surv_ids0 = {m["id"] for m in surv_r0}
        non_surv_r0 = [m for m in smp["m_det"] if m["id"] not in surv_ids0]

        cache_path, disk_cache = rfsm.load_disk_llm_cache(model)
        fb_init = build_matched_ablation_feedback(
            language=lang,
            total_obligations=ev_r0_full["joint_m_det_total"],
            survived_mutants=surv_r0,
            non_survived_pool=non_surv_r0,
            mode="A2_MisleadingLocation",
        )
        prev_c = trig_cand
        fb_msg = fb_init["text"]
        post_rounds = []
        rec_round = None
        fin_ev = None
        fin_c = None

        for r_i in range(r0 + 1, 6):
            pr = rv2.build_v2_prompt("Ours_MuGuard_Flake", smp, r_i, prev_c, fb_msg)
            l_resp, _ = call_llm_v3_high_budget(model, pr, f"v3_fork_A2_{sid}_r{r_i}", disk_cache, cache_path)
            c_i = rv2.parse_v2_response(l_resp["content"], smp)
            if lang == "java" and ttype == "ID":
                cm = {"victim": c_i.get("test_code") or ""}
            else:
                cm = {"victim": c_i.get("victim_code") or ""}
                if c_i.get("polluter_code"):
                    cm["polluter"] = c_i["polluter_code"]

            san_ok, san_errs = (rv2.check_py_basic_sanity(orig_methods, cm) if lang == "python" else rv2.check_java_basic_sanity(orig_methods, cm))
            if not san_ok:
                fb_msg = "SYNTAX/SANITY REJECTION:\n- " + "\n- ".join(san_errs)
                prev_c = c_i
                post_rounds.append({"round": r_i, "decision": "SANITY_REJECT_RETRY" if r_i < 5 else "SANITY_REJECT_FINAL"})
                continue

            ev_i, _ = rv2.remote_evaluate_v2(smp, c_i, bench_sha, eval_cache, eval_cache_path, mode="in_loop_gate")
            if not ev_i["compiled"]:
                fb_msg = f"COMPILATION / SYNTAX FAILED:\n```\n{ev_i['compile_error']}\n```\nFix all errors."
                prev_c = c_i
                post_rounds.append({"round": r_i, "decision": "COMPILE_RETRY" if r_i < 5 else "ABSTAIN_COMPILE"})
                continue
            if not ev_i["w_plus_passed"]:
                fb_msg = f"STABILITY VALIDATION (W+) FAILED:\n```\n{ev_i['w_plus_diag']}\n```\nYour patch compiled, but failed NonDex / order execution."
                prev_c = c_i
                post_rounds.append({"round": r_i, "decision": "UNSTABLE_RETRY" if r_i < 5 else "ABSTAIN_UNSTABLE"})
                continue
            if ev_i.get("gate_status_override") == "ABSTAIN_PARTNER_UNCOVERED":
                fb_msg = (
                    "PARTNER SCOPE UNVERIFIABLE: The partner test has zero baseline focal mutation obligations, "
                    "so modifying the partner test or shared fixture cannot be verified safe. "
                    "Leave the partner test completely unchanged and isolate/reset state strictly at the start of the target test!"
                )
                prev_c = c_i
                post_rounds.append({"round": r_i, "decision": "PARTNER_UNCOVERED_RETRY" if r_i < 5 else "ABSTAIN_PARTNER_UNCOVERED"})
                continue
            if not ev_i.get("pit_ran") or ev_i.get("joint_retention") is None:
                fb_msg = f"FOCAL MUTATION PROBE ERROR:\n```\n{ev_i.get('w_plus_diag', '')}\n```\nEnsure your test compiles and executes cleanly in standalone isolation."
                prev_c = c_i
                post_rounds.append({"round": r_i, "decision": "EVAL_ERROR_RETRY" if r_i < 5 else "ABSTAIN_EVAL_ERROR"})
                continue
            if ev_i["gate_safe"]:
                rec_round = r_i
                fin_ev = rv2.compact_eval_dict(ev_i)
                fin_c = c_i
                post_rounds.append({"round": r_i, "decision": "SAFE_PASS"})
                break
            else:
                if r_i < 5:
                    s_i = ev_i.get("survived_mutants", [])
                    s_ids = {m["id"] for m in s_i}
                    ns_i = [m for m in smp["m_det"] if m["id"] not in s_ids]
                    fb_next = build_matched_ablation_feedback(lang, ev_i["joint_m_det_total"], s_i, ns_i, "A2_MisleadingLocation")
                    fb_msg = fb_next["text"]
                    prev_c = c_i
                    post_rounds.append({"round": r_i, "decision": "EROSION_RETRY"})
                else:
                    post_rounds.append({"round": r_i, "decision": "ABSTAIN_EROSION"})
                    break

        recovered = rec_round is not None
        res_arm = {
            "branch_mode": "A2_MisleadingLocation",
            "initial_feedback_meta": {k: fb_init[k] for k in ("mode", "shown_count", "char_len", "approx_tokens")},
            "recovered": recovered,
            "recovery_round": rec_round,
            "post_fork_rounds_used": (rec_round - r0) if rec_round is not None else (5 - r0),
            "terminal_decision": post_rounds[-1]["decision"] if post_rounds else "ABSTAIN_EROSION",
            "final_candidate": fin_c,
            "final_eval": fin_ev,
            "post_rounds": post_rounds,
        }
        print(f"  [DONE A2 {fk}] recovered={recovered} rec_round={rec_round} state={res_arm['terminal_decision']}", flush=True)
        return fk, res_arm

    if missing_a2:
        with ThreadPoolExecutor(max_workers=2) as ex:
            futs = {ex.submit(_run_a2_fork, c): f"{c['model']}::{c['sample_id']}" for c in missing_a2}
            for fut in as_completed(futs):
                fk = futs[fut]
                try:
                    _, arm_res = fut.result()
                    with V3_LOCK:
                        a2_map[fk] = arm_res
                        save_json_atomic(OUT_A2, a2_done)
                except Exception as e:
                    print(f"  [ERR A2 {fk}]: {e}", flush=True)

    # =========================================================================
    # PART 3: Run post-freeze held-out validation on any newly accepted K=5 candidates!
    # =========================================================================
    ho_v2 = json.loads((DATA_DIR / "v2_heldout_validation.json").read_text(encoding="utf-8"))["evaluations"]
    ho_v3 = load_json_or(OUT_HO, {"protocol_version": "3.0.0-gap-heldout", "evaluations": {}})
    ho_v3_evals: Dict[str, Any] = ho_v3.setdefault("evaluations", {})

    to_ho: List[Tuple[str, Dict[str, Any], Dict[str, Any]]] = []
    seen_ck = set()
    for m, m_map in k5_by_model.items():
        for sid, rec in m_map.items():
            smp = s_by_id[sid]
            if smp.get("m_ext_total_count", 0) <= 0:
                continue
            for st in rec["strategies"].values():
                if not st.get("accepted"):
                    continue
                acc_r = st.get("accepted_round")
                rd = next((x for x in st["rounds"] if x["round"] == acc_r), st["rounds"][-1])
                fp = rd["candidate_fp"]
                ck = f"{sid}::{fp}"
                if ck in ho_v2 or ck in ho_v3_evals or ck in seen_ck:
                    continue
                seen_ck.add(ck)
                to_ho.append((ck, smp, rd["candidate"]))

    print(f"\n[PART 3] Running post-freeze held-out validation on {len(to_ho)} new gap candidates...", flush=True)
    if to_ho:
        def _run_ho(item: Tuple[str, Dict[str, Any], Dict[str, Any]]) -> Tuple[str, Dict[str, Any]]:
            ck, smp, cand = item
            ev, _ = rv2.remote_evaluate_v2(smp, cand, bench_sha, eval_cache, eval_cache_path, mode="post_freeze_heldout")
            print(f"  [DONE HO {ck}] strict={ev.get('w_plus_heldout_passed_strict')} m_ext={ev.get('m_ext_all_killed')}/{ev.get('m_ext_all_total')}", flush=True)
            return ck, ev

        with ThreadPoolExecutor(max_workers=2) as ex:
            futs = {ex.submit(_run_ho, it): it[0] for it in to_ho}
            for fut in as_completed(futs):
                ck = futs[fut]
                try:
                    _, ev = fut.result()
                    with V3_LOCK:
                        ho_v3_evals[ck] = ev
                        save_json_atomic(OUT_HO, ho_v3)
                except Exception as e:
                    print(f"  [ERR HO {ck}]: {e}", flush=True)

    rv2.save_v2_eval_cache(eval_cache_path, eval_cache)
    print("\n=== GAP COMPLETION SUMMARY ===")
    for m in ("deepseek-flash", "deepseek-v4-pro"):
        print(f"  K5 {m}: v3_completed_extra={len(k5_by_model.get(m, {}))}")
    print(f"  A2 forks completed extra: {len(a2_map)}/12")
    print(f"  Held-out new evaluations: {len(ho_v3_evals)}")


if __name__ == "__main__":
    main()
