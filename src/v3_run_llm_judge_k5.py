"""
FT-1 v3.0 Full K=5 Multi-Round Cross-Vendor LLM-as-a-Judge Gate (`B6_LLM_Judge_Gate`).

Protocol parity with `MuGuard-Flake` and `B5_Coverage_Gate`:
  - Identical Round-1 prompt (`B2_Prompt_Guard` header -> 100% cache hit on Round 1!).
  - Identical K=5 retry budget, basic sanity check, compilation, and W+ short-circuit.
  - Once a candidate passes W+, an independent CROSS-VENDOR LLM Judge inspects
    (original test, candidate patch, unified diff, partner diff):
      * DeepSeek repair backbones (`deepseek-flash`, `deepseek-v4-pro`) -> judged by Alibaba `qwen3.7-plus`
      * Alibaba repair backbone (`qwen3.7-plus`) -> judged by DeepSeek `deepseek-flash`
  - If the Judge returns `SAFE_PASS`, the patch is accepted (we still record ground-truth
    mutation telemetry from `in_loop_gate` offline/blind to the Judge so leaked erosion and
    operational SoundFix% are measured identically!).
  - If the Judge returns `EROSION_DETECTED`, its semantic critique & repair guidance are
    fed back to the repair model for Round r+1; failing on Round K triggers `ABSTAIN_JUDGE`.
  - Runs post-freeze held-out validation (`W+_heldout` + `M_ext`) on all newly accepted patches!
"""
import argparse
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_full_study_matrix as rfsm
import run_v2_confirmatory_campaign as rv2
from remote_and_llm import DATA_DIR, scp_to_remote
from v3_llm_judge_baseline import CACHE_LOCK, call_judge_cached, cross_vendor_judge_for, load_cache, save_cache

LOCK = threading.Lock()


def load_json_or(path: Path, default: Any) -> Any:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return default


def save_json_atomic(path: Path, obj: Any) -> None:
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def run_k5_llm_judge(model: str, workers: int = 2) -> None:
    src_dir = Path(__file__).resolve().parent
    for fn in ("v2_guards_and_attribution.py", "v2_pytest_attribution_runner.py", "remote_v2_freeze_benchmark_and_mext.py", "remote_eval_v2.py"):
        scp_to_remote(src_dir / fn, f"scripts/{fn}")

    bm = json.loads((DATA_DIR / "v2_frozen_benchmark_light.json").read_text(encoding="utf-8"))
    bench_sha = bm["exec_benchmark_sha256"]
    elig = [s for s in bm["samples"] if s.get("v2_eligible")]
    s_by_id = {s["sample_id"]: s for s in elig}

    judge_model = cross_vendor_judge_for(model)
    judge_cache = load_cache()
    eval_cache_path, eval_cache = rv2.load_v2_eval_cache()
    cache_path, disk_cache = rfsm.load_disk_llm_cache(model)

    out_path = DATA_DIR / f"v3_b6_llm_judge_k5_{rfsm.safe_model_tag(model)}.json"
    done_obj = load_json_or(
        out_path,
        {"protocol_version": "3.0.0-b6-llm-judge-gate", "model": model, "judge_model": judge_model, "results": {}},
    )
    res_map: Dict[str, Any] = done_obj.setdefault("results", {})
    todo = [s for s in elig if s["sample_id"] not in res_map]
    print(f"[B6 LLM-JUDGE K5 {model} | judge={judge_model}] {len(res_map)}/{len(elig)} done; running {len(todo)}...", flush=True)

    def _run_one(smp: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        sid = smp["sample_id"]
        lang = smp.get("language", "java")
        ttype = smp["test_type"]
        orig_methods = {"victim": smp["orig_test_code"]}
        if (ttype == "OD" or lang == "python") and smp.get("orig_polluter_code"):
            orig_methods["polluter"] = smp["orig_polluter_code"]

        prev_cand = None
        fb_msg = ""
        rounds_trace = []
        accepted = False
        acc_round = None
        final_eval_mut = None
        final_cand = None
        judge_retries = 0
        gate_state = "ABSTAIN_UNSTABLE"

        for r_idx in range(1, 6):
            prompt = rv2.build_v2_prompt("B2_Prompt_Guard", smp, r_idx, prev_cand, fb_msg)
            l_resp, _ = rfsm.call_llm_cached(model, prompt, f"v3_B6_Judge_{sid}_r{r_idx}", disk_cache, cache_path)
            cand = rv2.parse_v2_response(l_resp["content"], smp)
            cand_fp = rfsm.candidate_fingerprint(cand)
            if lang == "java" and ttype == "ID":
                cm = {"victim": cand.get("test_code") or ""}
                cand_t = cand.get("test_code") or ""
                cand_p = ""
            else:
                cm = {"victim": cand.get("victim_code") or ""}
                cand_t = cand.get("victim_code") or ""
                cand_p = cand.get("polluter_code") or ""
                if cand_p:
                    cm["polluter"] = cand_p

            san_ok, san_errs = (rv2.check_py_basic_sanity(orig_methods, cm) if lang == "python" else rv2.check_java_basic_sanity(orig_methods, cm))
            if not san_ok:
                fb_msg = "SYNTAX/SANITY REJECTION:\n- " + "\n- ".join(san_errs)
                prev_cand = cand
                gate_state = "SANITY_REJECT_FINAL"
                rounds_trace.append({"round": r_idx, "candidate_fp": cand_fp, "decision": "SANITY_REJECT"})
                continue

            ev_full, _ = rv2.remote_evaluate_v2(smp, cand, bench_sha, eval_cache, eval_cache_path, mode="in_loop_gate")
            if not ev_full["compiled"]:
                fb_msg = f"COMPILATION / SYNTAX FAILED:\n```\n{ev_full['compile_error']}\n```\nFix all errors."
                prev_cand = cand
                gate_state = "ABSTAIN_COMPILE"
                rounds_trace.append({"round": r_idx, "candidate_fp": cand_fp, "decision": "COMPILE_FAIL"})
                continue
            if not ev_full["w_plus_passed"]:
                fb_msg = f"STABILITY VALIDATION (W+) FAILED:\n```\n{ev_full['w_plus_diag']}\n```\nFix the flakiness."
                prev_cand = cand
                gate_state = "ABSTAIN_UNSTABLE"
                rounds_trace.append({"round": r_idx, "candidate_fp": cand_fp, "decision": "W_PLUS_FAIL"})
                continue

            # Invoke cross-vendor LLM Judge (completely blind to mutation results!)
            jr = call_judge_cached(
                cache=judge_cache,
                judge_model=judge_model,
                lang=lang,
                flaky_type=smp.get("od_subtype") or ttype,
                test_name=smp.get("test_full_name", sid),
                orig_t=smp.get("orig_test_code", ""),
                cand_t=cand_t,
                orig_p=smp.get("orig_polluter_code", ""),
                cand_p=cand_p,
                purpose=f"v3_B6_k5_judge_{model}_{sid}_r{r_idx}",
            )
            if jr.get("verdict") == "SAFE_PASS":
                accepted = True
                acc_round = r_idx
                final_cand = cand
                final_eval_mut = rv2.compact_eval_dict(ev_full)
                gate_state = "JUDGE_SAFE_PASS"
                rounds_trace.append({
                    "round": r_idx,
                    "candidate_fp": cand_fp,
                    "judge_verdict": "SAFE_PASS",
                    "judge_sec": jr.get("latency_sec", 0.0),
                    "decision": "JUDGE_SAFE_PASS",
                })
                break
            else:
                judge_retries += 1
                if r_idx < 5:
                    fb_msg = (
                        f"SEMANTIC ORACLE AUDITOR ({judge_model}) REJECTED YOUR PATCH ({jr.get('category', 'EROSION')}):\n"
                        f"- Issue: {jr.get('reason', 'Oracle weakened or responsibility shifted.')}\n"
                        f"- Guidance: {jr.get('repair_guidance', 'Preserve all original production checks while fixing flakiness.')}\n"
                        "Revise your repair to fix the flakiness without weakening assertions or skipping production logic."
                    )
                    prev_cand = cand
                    rounds_trace.append({
                        "round": r_idx,
                        "candidate_fp": cand_fp,
                        "judge_verdict": jr.get("verdict"),
                        "judge_sec": jr.get("latency_sec", 0.0),
                        "decision": "JUDGE_RETRY",
                    })
                else:
                    gate_state = "ABSTAIN_JUDGE"
                    rounds_trace.append({
                        "round": r_idx,
                        "candidate_fp": cand_fp,
                        "judge_verdict": jr.get("verdict"),
                        "judge_sec": jr.get("latency_sec", 0.0),
                        "decision": "ABSTAIN_JUDGE",
                    })
                    break

        rec = {
            "sample_id": sid,
            "project_name": smp["project_name"],
            "language": lang,
            "strategy": "B6_LLM_Judge_Gate",
            "judge_model": judge_model,
            "gate_state": gate_state,
            "accepted": accepted,
            "accepted_round": acc_round,
            "w_plus_passed": bool(accepted),
            "eroded": bool(accepted and final_eval_mut and final_eval_mut.get("eroded")),
            "sound_fix": bool(accepted and final_eval_mut and final_eval_mut.get("sound_fix")),
            "joint_retention": final_eval_mut.get("joint_retention") if final_eval_mut else None,
            "judge_retries": judge_retries,
            "final_candidate_fp": rounds_trace[-1]["candidate_fp"] if (accepted and rounds_trace) else "",
            "final_candidate": final_cand,
            "final_eval": final_eval_mut,
            "rounds": rounds_trace,
        }
        print(f"  [B6 {model} {sid}] {gate_state} r={acc_round} sound={int(rec['sound_fix'])} leaked_er={int(rec['eroded'])}", flush=True)
        return sid, rec

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_run_one, s): s["sample_id"] for s in todo}
        done_n = 0
        for fut in as_completed(futs):
            sid = futs[fut]
            try:
                _, rec = fut.result()
                with LOCK:
                    res_map[sid] = rec
                    save_json_atomic(out_path, done_obj)
            except Exception as e:
                print(f"  [ERR B6 {model} {sid}]: {e}", flush=True)
            done_n += 1
            if done_n % 15 == 0:
                with CACHE_LOCK:
                    save_cache(judge_cache)

    with CACHE_LOCK:
        save_cache(judge_cache)

    # Post-freeze held-out validation on any newly accepted candidates
    ho_v2 = json.loads((DATA_DIR / "v2_heldout_validation.json").read_text(encoding="utf-8"))["evaluations"]
    ho_b5_path = DATA_DIR / "v3_b5_b6_heldout_validation.json"
    ho_b5 = load_json_or(ho_b5_path, {"evaluations": {}})
    ho_b5_evals = ho_b5.setdefault("evaluations", {})
    to_ho = []
    for sid, rec in res_map.items():
        if not rec.get("accepted") or not rec.get("final_candidate"):
            continue
        smp = s_by_id[sid]
        if smp.get("m_ext_total_count", 0) <= 0:
            continue
        ck = f"{sid}::{rec['final_candidate_fp']}"
        if ck not in ho_v2 and ck not in ho_b5_evals:
            to_ho.append((ck, smp, rec["final_candidate"]))
    print(f"[B6 HO {model}] Evaluating {len(to_ho)} new held-out candidates...", flush=True)
    if to_ho:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(rv2.remote_evaluate_v2, smp, cand, bench_sha, eval_cache, eval_cache_path, "post_freeze_heldout"): ck for ck, smp, cand in to_ho}
            for fut in as_completed(futs):
                ck = futs[fut]
                try:
                    ev, _ = fut.result()
                    with LOCK:
                        ho_b5_evals[ck] = ev
                        save_json_atomic(ho_b5_path, ho_b5)
                except Exception as e:
                    print(f"  [ERR B6 HO {ck}]: {e}", flush=True)

    rv2.save_v2_eval_cache(eval_cache_path, eval_cache)
    n_tot = len(res_map)
    n_acc = sum(1 for r in res_map.values() if r["accepted"])
    n_er = sum(1 for r in res_map.values() if r["eroded"])
    n_sf = sum(1 for r in res_map.values() if r["sound_fix"])
    print(f"\n=== [B6 LLM-JUDGE K5 {model}] n={n_tot} | Pass@W+={n_acc} | Leaked={n_er} ({n_er/max(1,n_acc)*100:.2f}%) | SoundFix={n_sf} ({n_sf/max(1,n_tot)*100:.2f}%) ===")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--workers", type=int, default=2)
    args = ap.parse_args()
    run_k5_llm_judge(model=args.model, workers=args.workers)
