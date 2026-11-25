"""
FT-1 v3.0 RQ4 Upgrades:
  1. Online Top-k (k=10 uniform random) live multi-round repair loop on confirmatory `qwen3.7-plus`
     (203 eligible tests), comparing true ONLINE leaked erosion, SoundFix%, and held-out M_ext loss
     against both Full `MuGuard-Flake` and the offline Top-10 simulation (Table 12).
  2. Warm/Cached CI Latency micro-benchmark across representative Java and Python repositories
     (measuring warm incremental test-compile + targeted probe vs. cold uncached latency).
"""
import argparse
import hashlib
import json
import random
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_full_study_matrix as rfsm
import run_v2_confirmatory_campaign as rv2
from remote_and_llm import DATA_DIR, scp_to_remote
from v2_guards_and_attribution import build_matched_ablation_feedback

OUT_TOPK = DATA_DIR / "v3_rq4_online_topk10_qwen.json"
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


def sample_topk_ids(sample: Dict[str, Any], k: int = 10) -> Tuple[Set[str], Set[str]]:
    """Deterministically sample up to `k` baseline obligations from target+partner pool (uniform random)."""
    seed_int = int(hashlib.sha256(f"online_topk_{k}_{sample['sample_id']}".encode("utf-8")).hexdigest()[:8], 16)
    rng = random.Random(seed_int)
    t_pool = [("target", m["id"]) for m in (sample.get("m_det") or [])]
    p_pool = [("partner", m["id"]) for m in (sample.get("polluter_m_det") or [])]
    joint = t_pool + p_pool
    if len(joint) <= k:
        chosen = joint
    else:
        chosen = rng.sample(joint, k)
    return (
        {mid for role, mid in chosen if role == "target"},
        {mid for role, mid in chosen if role == "partner"},
    )


def run_online_topk(model: str = "qwen3.7-plus", k: int = 10, workers: int = 2) -> None:
    src_dir = Path(__file__).resolve().parent
    for fn in ("v2_guards_and_attribution.py", "v2_pytest_attribution_runner.py", "remote_v2_freeze_benchmark_and_mext.py", "remote_eval_v2.py"):
        scp_to_remote(src_dir / fn, f"scripts/{fn}")

    bm = json.loads((DATA_DIR / "v2_frozen_benchmark_light.json").read_text(encoding="utf-8"))
    bench_sha = bm["exec_benchmark_sha256"]
    elig = [s for s in bm["samples"] if s.get("v2_eligible")]
    s_by_id = {s["sample_id"]: s for s in elig}

    eval_cache_path, eval_cache = rv2.load_v2_eval_cache()
    cache_path, disk_cache = rfsm.load_disk_llm_cache(model)

    store = load_json_or(OUT_TOPK, {"protocol_version": f"3.0.0-online-topk{k}", "model": model, "k": k, "results": {}})
    res_map: Dict[str, Any] = store.setdefault("results", {})
    todo = [s for s in elig if s["sample_id"] not in res_map]
    print(f"[ONLINE TOP-{k} {model}] {len(res_map)}/{len(elig)} done; running {len(todo)}...", flush=True)

    def _run_one(smp: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        sid = smp["sample_id"]
        lang = smp.get("language", "java")
        ttype = smp["test_type"]
        orig_methods = {"victim": smp["orig_test_code"]}
        if (ttype == "OD" or lang == "python") and smp.get("orig_polluter_code"):
            orig_methods["polluter"] = smp["orig_polluter_code"]

        sub_t_ids, sub_p_ids = sample_topk_ids(smp, k=k)
        prev_cand = None
        fb_msg = ""
        rounds_trace = []
        accepted = False
        acc_round = None
        final_eval_full = None
        final_cand = None
        gate_state = "ABSTAIN_UNSTABLE"

        for r_idx in range(1, 6):
            prompt = rv2.build_v2_prompt("Ours_MuGuard_Flake", smp, r_idx, prev_cand, fb_msg)
            l_resp, _ = rfsm.call_llm_cached(model, prompt, f"v3_OnlineTop{k}_{sid}_r{r_idx}", disk_cache, cache_path)
            cand = rv2.parse_v2_response(l_resp["content"], smp)
            cand_fp = rfsm.candidate_fingerprint(cand)
            if lang == "java" and ttype == "ID":
                cm = {"victim": cand.get("test_code") or ""}
            else:
                cm = {"victim": cand.get("victim_code") or ""}
                if cand.get("polluter_code"):
                    cm["polluter"] = cand["polluter_code"]

            san_ok, san_errs = (rv2.check_py_basic_sanity(orig_methods, cm) if lang == "python" else rv2.check_java_basic_sanity(orig_methods, cm))
            if not san_ok:
                fb_msg = "SYNTAX/SANITY REJECTION:\n- " + "\n- ".join(san_errs)
                prev_cand = cand
                gate_state = "SANITY_REJECT_FINAL"
                rounds_trace.append({"round": r_idx, "candidate_fp": cand_fp, "decision": "SANITY_REJECT"})
                continue

            # Remote eval gives full surviving list (100% cache hit on Round 1 and known trajectories!),
            # and the ONLINE Top-k gate ONLY sees mutants inside `(sub_t_ids, sub_p_ids)`!
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
            if ev_full.get("gate_status_override") == "ABSTAIN_PARTNER_UNCOVERED":
                if r_idx < 5:
                    fb_msg = "PARTNER SCOPE UNVERIFIABLE: Leave partner test unchanged and isolate state inside target test."
                    prev_cand = cand
                    rounds_trace.append({"round": r_idx, "candidate_fp": cand_fp, "decision": "PARTNER_UNCOVERED_RETRY"})
                    continue
                gate_state = "ABSTAIN_PARTNER_UNCOVERED"
                rounds_trace.append({"round": r_idx, "candidate_fp": cand_fp, "decision": "ABSTAIN_PARTNER_UNCOVERED"})
                break
            if not ev_full.get("pit_ran") or ev_full.get("joint_retention") is None:
                if r_idx < 5:
                    fb_msg = "FOCAL MUTATION PROBE ERROR: ensure standalone execution passes."
                    prev_cand = cand
                    rounds_trace.append({"round": r_idx, "candidate_fp": cand_fp, "decision": "EVAL_ERROR_RETRY"})
                    continue
                gate_state = "ABSTAIN_EVAL_ERROR"
                break

            # Filter surviving mutants strictly to the pre-sampled Top-k subset!
            surv_all = ev_full.get("survived_mutants") or []
            surv_sub = [
                m for m in surv_all
                if (m.get("role", "target") == "target" and m["id"] in sub_t_ids)
                or (m.get("role") == "partner" and m["id"] in sub_p_ids)
            ]
            if not surv_sub:
                accepted = True
                acc_round = r_idx
                final_cand = cand
                final_eval_full = rv2.compact_eval_dict(ev_full)
                gate_state = "TOPK_SAFE_PASS"
                rounds_trace.append({"round": r_idx, "candidate_fp": cand_fp, "full_survived": len(surv_all), "sub_survived": 0, "decision": "TOPK_SAFE_PASS"})
                break
            else:
                if r_idx < 5:
                    sub_tot = len(sub_t_ids) + (len(sub_p_ids) if ev_full.get("affects_partner_or_shared") else 0)
                    fb_obj = build_matched_ablation_feedback(lang, max(1, sub_tot), surv_sub, [], "A3_TrueWitness")
                    fb_msg = fb_obj["text"]
                    prev_cand = cand
                    rounds_trace.append({"round": r_idx, "candidate_fp": cand_fp, "full_survived": len(surv_all), "sub_survived": len(surv_sub), "decision": "TOPK_RETRY"})
                else:
                    gate_state = "ABSTAIN_EROSION"
                    rounds_trace.append({"round": r_idx, "candidate_fp": cand_fp, "full_survived": len(surv_all), "sub_survived": len(surv_sub), "decision": "ABSTAIN_EROSION"})
                    break

        rec = {
            "sample_id": sid,
            "project_name": smp["project_name"],
            "language": lang,
            "gate_state": gate_state,
            "accepted": accepted,
            "accepted_round": acc_round,
            "eroded_full": bool(accepted and final_eval_full and final_eval_full.get("eroded")),
            "sound_fix_full": bool(accepted and final_eval_full and final_eval_full.get("sound_fix")),
            "final_candidate_fp": rounds_trace[-1]["candidate_fp"] if (accepted and rounds_trace) else "",
            "final_candidate": final_cand,
            "final_eval": final_eval_full,
            "rounds": rounds_trace,
        }
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
                    save_json_atomic(OUT_TOPK, store)
            except Exception as e:
                print(f"  [ERR TOPK {sid}]: {e}", flush=True)
            done_n += 1
            if done_n % 25 == 0 or done_n == len(futs):
                print(f"  Online Top-{k} progress: {done_n}/{len(futs)}", flush=True)

    # Held-out validation on any newly accepted Top-k candidates!
    ho_v2 = json.loads((DATA_DIR / "v2_heldout_validation.json").read_text(encoding="utf-8"))["evaluations"]
    ho_v3_path = DATA_DIR / "v3_b5_b6_heldout_validation.json"
    ho_v3 = load_json_or(ho_v3_path, {"evaluations": {}})
    ho_v3_evals = ho_v3.setdefault("evaluations", {})
    to_ho = []
    for sid, rec in res_map.items():
        if not rec.get("accepted") or not rec.get("final_candidate"):
            continue
        smp = s_by_id[sid]
        if smp.get("m_ext_total_count", 0) <= 0:
            continue
        ck = f"{sid}::{rec['final_candidate_fp']}"
        if ck not in ho_v2 and ck not in ho_v3_evals:
            to_ho.append((ck, smp, rec["final_candidate"]))
    if to_ho:
        print(f"[ONLINE TOP-{k} HO] Evaluating {len(to_ho)} new held-out candidates...", flush=True)
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(rv2.remote_evaluate_v2, smp, cand, bench_sha, eval_cache, eval_cache_path, "post_freeze_heldout"): ck for ck, smp, cand in to_ho}
            for fut in as_completed(futs):
                ck = futs[fut]
                try:
                    ev, _ = fut.result()
                    with LOCK:
                        ho_v3_evals[ck] = ev
                        save_json_atomic(ho_v3_path, ho_v3)
                except Exception as e:
                    print(f"  [ERR TOPK HO {ck}]: {e}", flush=True)

    rv2.save_v2_eval_cache(eval_cache_path, eval_cache)
    n_tot = len(res_map)
    n_acc = sum(1 for r in res_map.values() if r["accepted"])
    n_er = sum(1 for r in res_map.values() if r["eroded_full"])
    n_sf = sum(1 for r in res_map.values() if r["sound_fix_full"])
    print(f"\n=== [ONLINE TOP-{k} {model}] n={n_tot} | Acc={n_acc} | Leaked={n_er} ({n_er/max(1,n_acc)*100:.2f}%) | SoundFix={n_sf} ({n_sf/max(1,n_tot)*100:.2f}%) ===")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen3.7-plus")
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--workers", type=int, default=2)
    args = ap.parse_args()
    run_online_topk(model=args.model, k=args.k, workers=args.workers)
