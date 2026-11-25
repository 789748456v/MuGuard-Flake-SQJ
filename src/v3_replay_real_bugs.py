"""
FT-1 v3.0 Host Orchestrator for Real Historical Production Bug-Revert Audit.

Replays clean-revertable executable historical bug-fix commits from `data/v3_real_bug_candidates.json`
against `T_orig` AND every strategy's accepted repair patch (`B0`, `B3+`, `B4`, `MuGuard-Flake`, plus
`B5_Coverage` and `B6_LLM_Judge` when present) on the remote execution server.

Outputs: `data/v3_real_bug_replay_results.json`
"""
import argparse
import base64
import hashlib
import json
import sys
import threading
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from remote_and_llm import DATA_DIR, run_ssh, scp_to_remote

CAND_PATH = DATA_DIR / "v3_real_bug_candidates.json"
OUT_PATH = DATA_DIR / "v3_real_bug_replay_results.json"
LOCK = threading.Lock()
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


def collect_candidates_for_sample(sid: str, res_by_model: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Collect deduplicated accepted candidates labeled by `model::strategy`."""
    out: Dict[str, Dict[str, Any]] = {}
    for m, by_id in res_by_model.items():
        r = by_id.get(sid)
        if not r:
            continue
        for sn in ("B0_Standard_Validator", "B3_Plus_Structural_Guard", "B4_Scalar_Gate_Loop", "Ours_MuGuard_Flake"):
            st = (r.get("strategies") or {}).get(sn) or {}
            if st.get("accepted") and st.get("final_candidate"):
                out[f"{m}::{sn}"] = st["final_candidate"]
    # Also attach B5_Coverage & B6_LLM_Judge accepted patches if already generated!
    for prefix, sn in (("v3_b5_coverage_k5", "B5_Coverage_Gate"), ("v3_b6_llm_judge_k5", "B6_LLM_Judge_Gate")):
        for m in ("deepseek-flash", "deepseek-v4-pro", "qwen3.7-plus"):
            p = DATA_DIR / f"{prefix}_{m.replace('/', '_')}.json"
            if p.exists():
                d = json.loads(p.read_text(encoding="utf-8")).get("results", {})
                rec = d.get(sid)
                if rec and rec.get("accepted") and rec.get("final_candidate"):
                    out[f"{m}::{sn}"] = rec["final_candidate"]
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--eroded-only", action="store_true", default=True)
    ap.add_argument("--max-commits-per-test", type=int, default=5)
    ap.add_argument("--cand-file", default=str(CAND_PATH))
    ap.add_argument("--out-file", default=str(OUT_PATH))
    ap.add_argument("--remote-state", default="data/v3_bug_replay_state.json")
    args = ap.parse_args()

    src_dir = Path(__file__).resolve().parent
    for fn in ("v2_guards_and_attribution.py", "v3_remote_bug_replay.py"):
        scp_to_remote(src_dir / fn, f"scripts/{fn}")

    bm = json.loads((DATA_DIR / "v2_frozen_benchmark_light.json").read_text(encoding="utf-8"))
    s_by_id = {s["sample_id"]: s for s in bm["samples"]}
    res_by_model = {
        m: {r["sample_id"]: r for r in json.loads((DATA_DIR / f"v2_results_{m}.json").read_text(encoding="utf-8"))["results"]}
        for m in ("deepseek-flash", "deepseek-v4-pro", "qwen3.7-plus")
    }
    mined = json.loads(Path(args.cand_file).read_text(encoding="utf-8"))["samples"]
    out_p = Path(args.out_file)
    store = load_json_or(out_p, {"protocol_version": "3.0.0-real-bug-replay", "evaluated": {}})
    ev_map: Dict[str, Any] = store.setdefault("evaluated", {})

    jobs = []
    for item in mined:
        if args.eroded_only and not item.get("has_eroded_llm_patches"):
            continue
        sid = item["sample_id"]
        if sid not in s_by_id:
            continue
        cands = collect_candidates_for_sample(sid, res_by_model)
        if not cands:
            continue
        for c in item["candidate_commits"][: args.max_commits_per_test]:
            jk = f"{sid}::{c['commit_sha'][:12]}"
            if jk not in ev_map:
                jobs.append((jk, s_by_id[sid], item["git_root"], c, cands))

    print(f"[REAL BUG REPLAY] {len(ev_map)} evaluated; uploading {len(jobs)} candidate commits for single-SSH batch replay...", flush=True)
    if jobs:
        from remote_and_llm import scp_from_remote

        batch_jobs = []
        for jk, smp, git_root, commit, cands in jobs:
            slim_smp = {k: v for k, v in smp.items() if k not in ("m_det", "polluter_m_det", "m_ext_op_value_flow", "m_ext_op_call", "m_ext_operator", "m_ext_instance", "certified_v23_pools", "v2_dev_pr_eval", "v2_pr_manifest", "v2_b1_audit")}
            batch_jobs.append({"jk": jk, "sample": slim_smp, "git_root": git_root, "commit": commit, "candidates": cands})

        tmp_spec = DATA_DIR / "_tmp_bug_batch_spec.json"
        tmp_spec.write_text(json.dumps({"jobs": batch_jobs}, ensure_ascii=False), encoding="utf-8")
        try:
            scp_to_remote(tmp_spec, "scripts/_tmp_bug_batch_spec.json")
        finally:
            tmp_spec.unlink(missing_ok=True)

        rc, out, err = run_ssh(
            f"mkdir -p data && python3 scripts/v3_remote_bug_replay.py batch scripts/_tmp_bug_batch_spec.json {args.remote_state} {args.workers}",
            timeout=3600,
            retries=0,
        )
        print(out[-2500:], flush=True)
        if rc != 0:
            raise RuntimeError(f"Remote bug batch failed rc={rc}: {err[-400:]}")
        tmp_st = DATA_DIR / "_tmp_bug_replay_state.json"
        scp_from_remote(args.remote_state, tmp_st)
        remote_st = json.loads(tmp_st.read_text(encoding="utf-8"))
        tmp_st.unlink(missing_ok=True)
        ev_map.update(remote_st.get("evaluated") or {})
        save_json_atomic(out_p, store)

    # Summarize certified real bugs & silent bug misses by strategy!
    certified = [v for v in ev_map.values() if v.get("certified_real_bug")]
    by_strat = defaultdict(lambda: {"evaluated": 0, "bug_caught": 0, "bug_missed_silent_green": 0, "compile_fail": 0})
    for b in certified:
        for k, o in (b.get("candidate_outcomes") or {}).items():
            strat = k.split("::", 1)[1]
            by_strat[strat]["evaluated"] += 1
            if o.get("outcome") == "BUG_CAUGHT":
                by_strat[strat]["bug_caught"] += 1
            elif o.get("outcome") == "BUG_MISSED_SILENT_GREEN":
                by_strat[strat]["bug_missed_silent_green"] += 1
            else:
                by_strat[strat]["compile_fail"] += 1

    print(f"\n=== REAL HISTORICAL PRODUCTION BUG REPLAY SUMMARY ===")
    print(f"Certified reproducible historical bugs: {len(certified)} (out of {len(ev_map)} replayed commits)")
    for s, v in sorted(by_strat.items()):
        miss_pct = v["bug_missed_silent_green"] / max(1, v["evaluated"]) * 100
        print(f"  {s:26s}: eval={v['evaluated']} caught={v['bug_caught']} SILENT_MISS={v['bug_missed_silent_green']} ({miss_pct:.2f}%)")


if __name__ == "__main__":
    main()
