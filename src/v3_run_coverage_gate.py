"""
FT-1 v3.0 Host Orchestrator for Dynamic Coverage Gate (`B5_Coverage_Gate`):
  1. `--mode offline`:
       - Freezes baseline focal line coverage (`data/v3_baseline_coverage.json`) across all 203 eligible tests
         using real JaCoCo 0.8.10 (Java) + coverage.py 7.10.7 (Python).
       - Evaluates ALL 43 W+-passing test-only Java PRs under `B5_Coverage_Gate` (PR false-rejection vs weakening recall).
       - Evaluates ALL 400 unique eroded patches under `B5_Coverage_Gate` (exact detection recall by M1-M7 mechanism
         directly proving why Coverage Gate catches branch/call-skip erosion yet misses assertion/delimiter/oracle weakening!).
  2. `--mode full_k5 --model <model>`:
       - Runs the complete K=5 multi-round `B5_Coverage_Gate` repair loop with localized lost-line coverage witness feedback,
         explicit `ABSTAIN_COVERAGE` on round K, exact Coverage Gate latency vs Mutation Gate latency,
         and full in-loop mutation audit + post-freeze held-out `M_ext` validation!
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
import run_full_study_matrix as rfsm
import run_v2_confirmatory_campaign as rv2
from build_v3_human_audit_pack import build_candidate_lookup
from remote_and_llm import DATA_DIR, run_ssh, scp_to_remote
from v2_guards_and_attribution import HARNESS_VERSION

BASE_COV_PATH = DATA_DIR / "v3_baseline_coverage.json"
COV_EVAL_CACHE_PATH = DATA_DIR / "v3_cov_eval_cache.json"
OFFLINE_COV_OUT = DATA_DIR / "v3_coverage_gate_offline_and_pr.json"

LOCK = threading.Lock()
SSH_PACER = threading.Lock()
PROJ_LOCKS: Dict[str, threading.Lock] = defaultdict(threading.Lock)


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


def remote_cov_call(mode: str, payload: Dict[str, Any], proj_key: str, timeout: int = 180) -> Dict[str, Any]:
    raw_b64 = base64.b64encode(json.dumps(payload, ensure_ascii=False).encode("utf-8")).decode("ascii")
    # Pass payload via temp file on remote via stdin pipe
    cmd = f"python3 -c 'import base64,sys; sys.stdout.buffer.write(base64.b64decode(sys.argv[1]))' '{raw_b64}' | python3 scripts/v3_remote_coverage_gate.py {mode}"
    with PROJ_LOCKS[proj_key]:
        if len(raw_b64) > 18000:
            tag = hashlib.sha256(raw_b64.encode("ascii")).hexdigest()[:12]
            tmp_l = DATA_DIR / f"_tmp_cov_{tag}.json"
            tmp_l.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            try:
                with SSH_PACER:
                    scp_to_remote(tmp_l, f"scripts/_tmp_cov_{tag}.json")
                    time.sleep(0.8)
            finally:
                tmp_l.unlink(missing_ok=True)
            cmd = f"python3 scripts/v3_remote_coverage_gate.py {mode} < scripts/_tmp_cov_{tag}.json; rc=$?; rm -f scripts/_tmp_cov_{tag}.json; exit $rc"
        with SSH_PACER:
            time.sleep(0.8)
        rc, out, err = run_ssh(cmd, timeout=timeout, retries=4)
    for ln in out.splitlines():
        if ln.startswith("FT1_V3_COV_RESULT="):
            return json.loads(ln.split("FT1_V3_COV_RESULT=", 1)[1])
    raise RuntimeError(f"remote_cov_call({mode}) failed rc={rc}: {out[-250:]} | {err[-250:]}")


def ensure_remote_scripts() -> None:
    src_dir = Path(__file__).resolve().parent
    for fn in ("v3_remote_coverage_gate.py",):
        for att in range(4):
            try:
                time.sleep(1.5 * (att + 1))
                scp_to_remote(src_dir / fn, f"scripts/{fn}")
                break
            except Exception:
                if att == 3:
                    raise


def freeze_baseline_coverage(bm_samples: List[Dict[str, Any]], workers: int = 2) -> Dict[str, Any]:
    base_cov = load_json_or(BASE_COV_PATH, {"protocol_version": "3.0.0-baseline-coverage", "samples": {}})
    cov_map: Dict[str, Any] = base_cov.setdefault("samples", {})
    elig = [s for s in bm_samples if s.get("v2_eligible")]
    todo = [s for s in elig if s["sample_id"] not in cov_map or not cov_map[s["sample_id"]].get("ok_target")]
    print(f"[COV BASELINE] {len(elig) - len(todo)}/{len(elig)} ready; freezing {len(todo)} remaining...", flush=True)
    if not todo:
        return cov_map

    def _one(s: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        res = remote_cov_call("baseline_cov", {"sample": s}, s["project_name"], timeout=150)
        print(
            f"  [BASE COV] {s['sample_id']}: ok={res['ok_target']} lines={res['target_cov_count']} "
            f"(partner={res['partner_cov_count']}) {res['target_cov_sec']}s",
            flush=True,
        )
        return s["sample_id"], res

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_one, s): s["sample_id"] for s in todo}
        for fut in as_completed(futs):
            sid = futs[fut]
            try:
                _, res = fut.result()
                with LOCK:
                    cov_map[sid] = res
                    save_json_atomic(BASE_COV_PATH, base_cov)
            except Exception as e:
                print(f"  [ERR BASE COV {sid}]: {e}", flush=True)
    return cov_map


def format_coverage_witness_feedback(cov_ev: Dict[str, Any]) -> str:
    lost = cov_ev.get("lost_lines_count", 0)
    tot = cov_ev.get("base_lines_total", 0)
    wits = cov_ev.get("cov_witnesses") or []
    lines = [
        f"DYNAMIC COVERAGE GATE REJECTED YOUR PATCH ({lost}/{tot} baseline focal production lines lost coverage).",
        "Your patch stabilized the test, but no longer executes the following focal production lines that the original test covered:",
    ]
    for idx, w in enumerate(wits[:4], 1):
        lines.append(f"\n[Lost Line {idx}] ({w['role']}) {w['location']}\n{w['source_slice']}")
    lines.append(
        "\nRevise your repair so the test remains deterministic AND restores execution coverage over all listed production lines without weakening assertions."
    )
    return "\n".join(lines)


def run_offline_pr_and_400(workers: int = 2) -> None:
    """Single-SSH remote batch runner for 203 baselines + 43 PRs + 400 eroded patches (zero SSH rate-limit issues!)."""
    from remote_and_llm import scp_from_remote

    ensure_remote_scripts()
    bm = json.loads((DATA_DIR / "v2_frozen_benchmark_light.json").read_text(encoding="utf-8"))
    elig = [s for s in bm["samples"] if s.get("v2_eligible")]
    bm_by_id = {s["sample_id"]: s for s in elig}

    seed_base = load_json_or(BASE_COV_PATH, {"samples": {}}).get("samples", {})
    prev_off = load_json_or(OFFLINE_COV_OUT, {})
    seed_eroded = prev_off.get("eroded_records") or {}
    pr_diff_cache = load_json_or(DATA_DIR / "v3_pr_diff_cache.json", {})

    pr_man = json.loads((DATA_DIR / "pr_reference_manifest.json").read_text(encoding="utf-8"))["entries"]
    pr_adj = json.loads((DATA_DIR / "pr_independent_adjudication_input.json").read_text(encoding="utf-8"))["entries"]
    pr_43 = [
        e for e in pr_man
        if e.get("pr_apply_outcome") == "CLEAN_FULL_TEST_APPLY"
        and e.get("pr_scope_type") == "TEST_ONLY_PR"
        and e.get("w_plus_passed")
        and e["sample_id"] in bm_by_id
    ]
    import re
    pr_jobs = []
    for e in pr_43:
        slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", e["pr_url"].strip().rstrip("/")) + ".diff"
        pr_jobs.append({
            "sample_id": e["sample_id"],
            "pr_url": e["pr_url"],
            "expected_tree_sha256": e.get("applied_tree_sha256", ""),
            "pr_diff": pr_diff_cache.get(slug, ""),
        })

    by_cand_key, by_unique_key = build_candidate_lookup(bm_by_id)
    tax_cb = json.loads((DATA_DIR / "taxonomy_audit_codebook.json").read_text(encoding="utf-8"))["entries"]
    er_jobs = []
    er_meta = {}
    for e in tax_cb:
        uk = e["unique_patch_key"]
        sid = e["sample_id"]
        if sid not in bm_by_id:
            continue
        tail_fp = uk.split("::")[-1]
        rec = by_unique_key.get(uk) or by_cand_key.get(f"{sid}::{tail_fp}")
        if not rec:
            continue
        er_jobs.append({
            "uk": uk,
            "sample_id": sid,
            "candidate": {
                "test_code": rec["cand_test_code"],
                "victim_code": rec["cand_test_code"],
                "polluter_code": rec.get("cand_partner_code") or "",
                "imports": rec.get("imports") or [],
            },
        })
        er_meta[uk] = e

    # Strip heavy m_det lists not needed by coverage gate so spec stays compact (~2 MB)
    slim_samples = []
    for s in elig:
        slim = {k: v for k, v in s.items() if k not in ("m_ext_op_value_flow", "m_ext_op_call", "m_ext_operator", "m_ext_instance", "certified_v23_pools", "v2_dev_pr_eval", "v2_pr_manifest", "v2_b1_audit")}
        if s["language"] == "java":
            slim["m_det"] = [{"mutatedClass": m.get("mutatedClass")} for m in (s.get("m_det") or [])]
            slim["polluter_m_det"] = [{"mutatedClass": m.get("mutatedClass")} for m in (s.get("polluter_m_det") or [])]
        else:
            slim["m_det"] = []
            slim["polluter_m_det"] = []
        slim_samples.append(slim)

    spec_obj = {
        "samples": slim_samples,
        "seed_baselines": seed_base,
        "seed_eroded_evals": seed_eroded,
        "force_rerun_prs": True,
        "pr_jobs": pr_jobs,
        "eroded_jobs": er_jobs,
    }
    tmp_spec = DATA_DIR / "_tmp_cov_batch_spec.json"
    tmp_spec.write_text(json.dumps(spec_obj, ensure_ascii=False), encoding="utf-8")
    try:
        for att in range(4):
            try:
                time.sleep(2.0 * (att + 1))
                scp_to_remote(tmp_spec, "scripts/_tmp_cov_batch_spec.json")
                break
            except Exception:
                if att == 3:
                    raise
    finally:
        tmp_spec.unlink(missing_ok=True)
    time.sleep(2.0)

    print(f"[BATCH COV] Uploaded single batch spec (samples={len(slim_samples)}, PR={len(pr_jobs)}, Eroded={len(er_jobs)}). Running remotely...", flush=True)
    rc, out, err = run_ssh(
        f"mkdir -p data && python3 scripts/v3_remote_coverage_gate.py batch_offline scripts/_tmp_cov_batch_spec.json data/v3_cov_batch_state.json {workers}",
        timeout=3600,
        retries=0,
    )
    print(out[-2000:], flush=True)
    if rc != 0:
        raise RuntimeError(f"Remote batch_offline failed rc={rc}: {err[-500:]}")

    local_state = DATA_DIR / "_tmp_cov_batch_state.json"
    for att in range(4):
        try:
            time.sleep(2.0 * (att + 1))
            scp_from_remote("data/v3_cov_batch_state.json", local_state)
            break
        except Exception:
            if att == 3:
                raise
    state = json.loads(local_state.read_text(encoding="utf-8"))
    local_state.unlink(missing_ok=True)

    cov_map = state["baselines"]
    save_json_atomic(BASE_COV_PATH, {"protocol_version": "3.0.0-baseline-coverage", "samples": cov_map})

    pr_out = state["pr_evals"]
    er_out = state["eroded_evals"]

    # Populate cov_eval_cache so full_k5 gets instant cache hits on Round-1 candidates!
    cov_cache = load_json_or(COV_EVAL_CACHE_PATH, {})
    for j in er_jobs:
        uk = j["uk"]
        if uk in er_out and er_out[uk].get("cov_ran"):
            fp = rfsm.candidate_fingerprint(j["candidate"])
            cov_cache[f"cov::{j['sample_id']}::{fp}"] = er_out[uk]
    save_json_atomic(COV_EVAL_CACHE_PATH, cov_cache)

    merged_rej = merged_tot = legit_rej = legit_tot = weak_caught = weak_tot = shift_caught = shift_tot = ran_pr_tot = 0
    for e in pr_43:
        sid = e["sample_id"]
        adj = pr_adj.get(sid) or {}
        r = pr_out.get(sid) or {}
        if not r.get("cov_ran"):
            continue
        ran_pr_tot += 1
        rej = not bool(r.get("cov_safe"))
        if e.get("idoft_status") == "Accepted":
            merged_tot += 1
            if rej:
                merged_rej += 1
        if adj.get("is_legitimate_repair"):
            legit_tot += 1
            if rej:
                legit_rej += 1
        elif adj.get("label") == "GENUINE_ORACLE_WEAKENING":
            weak_tot += 1
            if rej:
                weak_caught += 1
        elif adj.get("label") == "TEST_RESPONSIBILITY_SHIFT":
            shift_tot += 1
            if rej:
                shift_caught += 1

    by_mech = defaultdict(lambda: {"total": 0, "cov_ran": 0, "cov_detected": 0, "cov_missed": 0})
    by_lang = defaultdict(lambda: {"total": 0, "cov_ran": 0, "cov_detected": 0, "cov_missed": 0})
    lats_java, lats_py = [], []
    tot_ran = tot_det = tot_miss = 0
    for j in er_jobs:
        uk = j["uk"]
        e = er_meta[uk]
        s = bm_by_id[j["sample_id"]]
        r = er_out.get(uk) or {}
        mech = (e.get("coding") or {}).get("primary_mechanism", "UNKNOWN")
        lang = s["language"]
        by_mech[mech]["total"] += 1
        by_lang[lang]["total"] += 1
        if r.get("cov_ran"):
            tot_ran += 1
            by_mech[mech]["cov_ran"] += 1
            by_lang[lang]["cov_ran"] += 1
            if lang == "java":
                lats_java.append(r.get("target_cov_sec", 0.0))
            else:
                lats_py.append(r.get("target_cov_sec", 0.0))
            if r.get("cov_eroded"):
                tot_det += 1
                by_mech[mech]["cov_detected"] += 1
                by_lang[lang]["cov_detected"] += 1
            else:
                tot_miss += 1
                by_mech[mech]["cov_missed"] += 1
                by_lang[lang]["cov_missed"] += 1

    summary = {
        "protocol_version": "3.0.0-coverage-gate-offline-and-pr",
        "baseline_cov_ready": sum(1 for v in cov_map.values() if v.get("ok_target")),
        "pr_43_summary": {
            "ran_of_43": ran_pr_tot,
            "blocked_argline": len(pr_43) - ran_pr_tot,
            "merged_rej": f"{merged_rej}/{merged_tot} ({merged_rej/max(1,merged_tot)*100:.1f}%)",
            "legit_false_rej": f"{legit_rej}/{legit_tot}",
            "legit_false_rej_with_pct": f"{legit_rej}/{legit_tot} ({legit_rej/max(1,legit_tot)*100:.1f}%)",
            "legit_false_rej_pct": round(legit_rej / max(1, legit_tot) * 100, 2),
            "legit_plus_shift_rej": f"{legit_rej+shift_caught}/{legit_tot+shift_tot} ({(legit_rej+shift_caught)/max(1,legit_tot+shift_tot)*100:.1f}%)",
            "weakening_caught": f"{weak_caught}/{weak_tot}",
            "weakening_caught_with_pct": f"{weak_caught}/{weak_tot} ({weak_caught/max(1,weak_tot)*100:.1f}%)",
            "shift_caught": f"{shift_caught}/{shift_tot}",
        },
        "eroded_400_summary": {
            "evaluated_ran": tot_ran,
            "cov_detected": tot_det,
            "cov_missed_bypassed": tot_miss,
            "cov_recall_pct": round(tot_det / max(1, tot_ran) * 100, 2),
            "cov_bypass_pct": round(tot_miss / max(1, tot_ran) * 100, 2),
            "median_target_cov_sec_java": round(sorted(lats_java)[len(lats_java) // 2], 2) if lats_java else 0.0,
            "median_target_cov_sec_python": round(sorted(lats_py)[len(lats_py) // 2], 2) if lats_py else 0.0,
            "by_mechanism": {
                m: {
                    **v,
                    "cov_recall_pct": round(v["cov_detected"] / max(1, v["cov_ran"]) * 100, 2),
                    "cov_bypass_pct": round(v["cov_missed"] / max(1, v["cov_ran"]) * 100, 2),
                }
                for m, v in sorted(by_mech.items())
            },
            "by_language": {
                l: {
                    **v,
                    "cov_recall_pct": round(v["cov_detected"] / max(1, v["cov_ran"]) * 100, 2),
                    "cov_bypass_pct": round(v["cov_missed"] / max(1, v["cov_ran"]) * 100, 2),
                }
                for l, v in sorted(by_lang.items())
            },
        },
        "pr_records": pr_out,
        "eroded_records": er_out,
    }
    save_json_atomic(OFFLINE_COV_OUT, summary)
    print("\n=== COVERAGE GATE OFFLINE & PR SUMMARY ===")
    print(json.dumps({k: v for k, v in summary.items() if k not in ("pr_records", "eroded_records")}, indent=2))


def prebatch_round1_cov(model: str, elig: List[Dict[str, Any]], cov_map: Dict[str, Any], cov_cache: Dict[str, Any], workers: int = 2) -> None:
    """Evaluate all Round-1 B2_Prompt_Guard W+-passing candidates in ONE remote SSH batch to warm cov_cache."""
    from remote_and_llm import scp_from_remote

    v2_path = DATA_DIR / f"v2_results_{rfsm.safe_model_tag(model)}.json"
    if not v2_path.exists():
        return
    v2_by_id = {r["sample_id"]: r for r in json.loads(v2_path.read_text(encoding="utf-8"))["results"]}
    s_by_id = {s["sample_id"]: s for s in elig}

    er_jobs = []
    for s in elig:
        sid = s["sample_id"]
        if not cov_map.get(sid, {}).get("ok_target"):
            continue
        r = v2_by_id.get(sid)
        if not r:
            continue
        st = r["strategies"].get("B2_Prompt_Guard") or {}
        if not st.get("w_plus_passed") or not st.get("rounds"):
            continue
        for rd in st["rounds"]:
            ev = rd.get("eval") or {}
            cand = rd.get("candidate")
            if ev.get("w_plus_passed") and cand:
                fp = rfsm.candidate_fingerprint(cand)
                ck = f"cov::{sid}::{fp}"
                if ck not in cov_cache or not cov_cache[ck].get("cov_ran"):
                    er_jobs.append({"uk": ck, "sample_id": sid, "candidate": cand})
                break

    if not er_jobs:
        print(f"[B5 PREBATCH {model}] All Round-1 candidates already in cov_cache!", flush=True)
        return

    slim_samples = []
    used_sids = {j["sample_id"] for j in er_jobs}
    for sid in sorted(used_sids):
        s = s_by_id[sid]
        slim = {k: v for k, v in s.items() if k not in ("m_ext_op_value_flow", "m_ext_op_call", "m_ext_operator", "m_ext_instance", "certified_v23_pools", "v2_dev_pr_eval", "v2_pr_manifest", "v2_b1_audit")}
        slim["m_det"] = [{"mutatedClass": m.get("mutatedClass")} for m in (s.get("m_det") or [])] if s["language"] == "java" else []
        slim["polluter_m_det"] = []
        slim_samples.append(slim)

    spec_obj = {"samples": slim_samples, "seed_baselines": {sid: cov_map[sid] for sid in used_sids}, "pr_jobs": [], "eroded_jobs": er_jobs}
    tmp_spec = DATA_DIR / f"_tmp_b5_r1_{rfsm.safe_model_tag(model)}.json"
    tmp_spec.write_text(json.dumps(spec_obj, ensure_ascii=False), encoding="utf-8")
    try:
        scp_to_remote(tmp_spec, f"scripts/{tmp_spec.name}")
    finally:
        tmp_spec.unlink(missing_ok=True)

    print(f"[B5 PREBATCH {model}] Running single-SSH batch for {len(er_jobs)} Round-1 W+ candidates...", flush=True)
    rc, out, err = run_ssh(
        f"mkdir -p data && rm -f data/v3_b5_r1_state.json && python3 scripts/v3_remote_coverage_gate.py batch_offline scripts/{tmp_spec.name} data/v3_b5_r1_state.json {workers}",
        timeout=2400,
        retries=0,
    )
    print(out[-1200:], flush=True)
    if rc == 0:
        tmp_st = DATA_DIR / "_tmp_b5_r1_state.json"
        scp_from_remote("data/v3_b5_r1_state.json", tmp_st)
        st_obj = json.loads(tmp_st.read_text(encoding="utf-8"))
        tmp_st.unlink(missing_ok=True)
        for ck, r in (st_obj.get("eroded_evals") or {}).items():
            if r.get("cov_ran"):
                cov_cache[ck] = r
        save_json_atomic(COV_EVAL_CACHE_PATH, cov_cache)
        print(f"[B5 PREBATCH {model}] Cached {len(st_obj.get('eroded_evals') or {})} Round-1 coverage evals!", flush=True)


def run_k5_coverage_gate(model: str, workers: int = 2) -> None:
    ensure_remote_scripts()
    bm = json.loads((DATA_DIR / "v2_frozen_benchmark_light.json").read_text(encoding="utf-8"))
    bench_sha = bm["exec_benchmark_sha256"]
    elig = [s for s in bm["samples"] if s.get("v2_eligible")]
    cov_map = load_json_or(BASE_COV_PATH, {"samples": {}}).get("samples", {})
    cov_cache = load_json_or(COV_EVAL_CACHE_PATH, {})
    prebatch_round1_cov(model, elig, cov_map, cov_cache, workers=2)
    eval_cache_path, eval_cache = rv2.load_v2_eval_cache()
    cache_path, disk_cache = rfsm.load_disk_llm_cache(model)

    out_path = DATA_DIR / f"v3_b5_coverage_k5_{rfsm.safe_model_tag(model)}.json"
    done_obj = load_json_or(out_path, {"protocol_version": "3.0.0-b5-coverage-gate", "model": model, "results": {}})
    res_map: Dict[str, Any] = done_obj.setdefault("results", {})
    todo = [s for s in elig if s["sample_id"] not in res_map and cov_map.get(s["sample_id"], {}).get("ok_target")]
    print(f"[B5 COVERAGE K5 {model}] {len(res_map)}/{len(elig)} done; running {len(todo)}...", flush=True)

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
        cov_retries = 0
        gate_state = "ABSTAIN_UNSTABLE"

        for r_idx in range(1, 6):
            prompt = rv2.build_v2_prompt("B2_Prompt_Guard", smp, r_idx, prev_cand, fb_msg)
            l_resp, l_cached = rfsm.call_llm_cached(model, prompt, f"v3_B5_Cov_{sid}_r{r_idx}", disk_cache, cache_path)
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

            # Evaluate compile + W+ (+ post-hoc ground-truth mutation telemetry stored in eval_cache — NOT shown to Coverage Gate!)
            ev_full, ev_cached = rv2.remote_evaluate_v2(smp, cand, bench_sha, eval_cache, eval_cache_path, mode="in_loop_gate")
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

            # Now run ONLY Coverage Gate (blind to mutation results!)
            ck_cov = f"cov::{sid}::{cand_fp}"
            with LOCK:
                cov_ev = cov_cache.get(ck_cov)
            if not cov_ev or not cov_ev.get("cov_ran"):
                cov_ev = remote_cov_call("eval_cand_cov", {"sample": smp, "candidate": cand, "baseline_cov": cov_map[sid]}, smp["project_name"])
                with LOCK:
                    cov_cache[ck_cov] = cov_ev
                    save_json_atomic(COV_EVAL_CACHE_PATH, cov_cache)

            if cov_ev.get("cov_ran") and cov_ev.get("cov_safe"):
                accepted = True
                acc_round = r_idx
                final_cand = cand
                final_eval_mut = rv2.compact_eval_dict(ev_full)
                gate_state = "COV_SAFE_PASS"
                rounds_trace.append({"round": r_idx, "candidate_fp": cand_fp, "cov_sec": cov_ev.get("cov_sec", 0.0), "cov_lost": 0, "decision": "COV_SAFE_PASS"})
                break
            else:
                cov_retries += 1
                if r_idx < 5:
                    fb_msg = format_coverage_witness_feedback(cov_ev) if cov_ev.get("cov_ran") else "COVERAGE PROBE ERROR: ensure standalone execution passes."
                    prev_cand = cand
                    rounds_trace.append({"round": r_idx, "candidate_fp": cand_fp, "cov_sec": cov_ev.get("cov_sec", 0.0), "cov_lost": cov_ev.get("lost_lines_count", -1), "decision": "COV_RETRY"})
                else:
                    gate_state = "ABSTAIN_COVERAGE"
                    rounds_trace.append({"round": r_idx, "candidate_fp": cand_fp, "cov_sec": cov_ev.get("cov_sec", 0.0), "cov_lost": cov_ev.get("lost_lines_count", -1), "decision": "ABSTAIN_COVERAGE"})
                    break

        rec = {
            "sample_id": sid,
            "project_name": smp["project_name"],
            "language": lang,
            "strategy": "B5_Coverage_Gate",
            "gate_state": gate_state,
            "accepted": accepted,
            "accepted_round": acc_round,
            "w_plus_passed": bool(accepted),
            "eroded": bool(accepted and final_eval_mut and final_eval_mut.get("eroded")),
            "sound_fix": bool(accepted and final_eval_mut and final_eval_mut.get("sound_fix")),
            "joint_retention": final_eval_mut.get("joint_retention") if final_eval_mut else None,
            "cov_retries": cov_retries,
            "final_candidate_fp": rounds_trace[-1]["candidate_fp"] if (accepted and rounds_trace) else "",
            "final_candidate": final_cand,
            "final_eval": final_eval_mut,
            "rounds": rounds_trace,
        }
        print(f"  [B5 {model} {sid}] {gate_state} r={acc_round} sound={int(rec['sound_fix'])} leaked_er={int(rec['eroded'])}", flush=True)
        return sid, rec

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_run_one, s): s["sample_id"] for s in todo}
        for fut in as_completed(futs):
            sid = futs[fut]
            try:
                _, rec = fut.result()
                with LOCK:
                    res_map[sid] = rec
                    save_json_atomic(out_path, done_obj)
            except Exception as e:
                print(f"  [ERR B5 {model} {sid}]: {e}", flush=True)

    # Also run post-freeze held-out validation on any newly accepted candidates not already in v2_heldout_validation!
    ho_v2 = json.loads((DATA_DIR / "v2_heldout_validation.json").read_text(encoding="utf-8"))["evaluations"]
    ho_b5_path = DATA_DIR / "v3_b5_b6_heldout_validation.json"
    ho_b5 = load_json_or(ho_b5_path, {"evaluations": {}})
    ho_b5_evals = ho_b5.setdefault("evaluations", {})
    s_by_id = {s["sample_id"]: s for s in elig}
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
    print(f"[B5 HO {model}] Evaluating {len(to_ho)} new held-out candidates...", flush=True)
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
                    print(f"  [ERR B5 HO {ck}]: {e}", flush=True)

    rv2.save_v2_eval_cache(eval_cache_path, eval_cache)
    n_tot = len(res_map)
    n_acc = sum(1 for r in res_map.values() if r["accepted"])
    n_er = sum(1 for r in res_map.values() if r["eroded"])
    n_sf = sum(1 for r in res_map.values() if r["sound_fix"])
    print(f"\n=== [B5 COVERAGE GATE K5 {model}] n={n_tot} | Pass@W+={n_acc} | Leaked={n_er} ({n_er/max(1,n_acc)*100:.2f}%) | SoundFix={n_sf} ({n_sf/max(1,n_tot)*100:.2f}%) ===")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["offline", "full_k5"], required=True)
    ap.add_argument("--model", default="qwen3.7-plus")
    ap.add_argument("--workers", type=int, default=2)
    args = ap.parse_args()
    if args.mode == "offline":
        run_offline_pr_and_400(workers=args.workers)
    else:
        run_k5_coverage_gate(model=args.model, workers=args.workers)
