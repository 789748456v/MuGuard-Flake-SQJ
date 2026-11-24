import json
import re
import statistics
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent))
from remote_and_llm import (
    API_LOG_PATH,
    DATA_DIR,
    DEEPSEEK_KEY_PATH,
    FT1_ROOT,
    QWEN_KEY_PATH,
    REPO_ROOT,
    load_api_key,
    load_remote_host,
)


def summarize_b1(samples: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Summarize official published FlakyDoctor SOTA patches (GPT-4, Magicoder, and Best/Primary B1)."""
    out: Dict[str, Any] = {}
    for label, field in (("B1_FlakyDoctor_GPT4", "b1_gpt_eval"), ("B1_FlakyDoctor_Magicoder", "b1_magicoder_eval")):
        avail = [s for s in samples if s.get(field) is not None]
        comp = [s for s in avail if s[field].get("compiled")]
        w_ok = [s for s in comp if s[field].get("w_plus_passed")]
        eroded = [s for s in w_ok if s[field].get("eroded")]
        sound = [s for s in w_ok if s[field].get("sound_fix")]
        rets = [s[field]["retention_m_det"] for s in w_ok if s[field].get("retention_m_det") is not None]
        gold_rets = [s[field]["retention_m_gold"] for s in w_ok if s[field].get("retention_m_gold") is not None]
        out[label] = {
            "available": len(avail),
            "compiled": len(comp),
            "w_plus_count": len(w_ok),
            "pass_w_plus_pct_of_avail": round(100.0 * len(w_ok) / len(avail), 2) if avail else 0.0,
            "eroded_count": len(eroded),
            "erosion_rate_among_w_plus_pct": round(100.0 * len(eroded) / len(w_ok), 2) if w_ok else 0.0,
            "sound_fix_count": len(sound),
            "sound_fix_pct_of_avail": round(100.0 * len(sound) / len(avail), 2) if avail else 0.0,
            "mean_retention_m_det": round(statistics.mean(rets), 4) if rets else None,
            "mean_retention_m_gold": round(statistics.mean(gold_rets), 4) if gold_rets else None,
            "eroded_samples": [
                {
                    "sample_id": s["sample_id"],
                    "test_type": s["test_type"],
                    "retention_m_det": s[field]["retention_m_det"],
                    "m_det_killed": s[field]["m_det_killed"],
                    "m_det_total": s[field]["m_det_total"],
                    "salvaged_import": s[field].get("salvaged_missing_assert_import", False),
                }
                for s in eroded
            ],
        }
    return out


def summarize_step2(results_json: Dict[str, Any]) -> Dict[str, Any]:
    rows = results_json["results"]
    n_total = len(rows)
    summary: Dict[str, Any] = {"n_total": n_total, "strategies": {}, "round_trajectories": {}}

    for strat in ("B0_Standard_Validator", "B2_Prompt_Guard", "B3_Static_AST_Guard", "Ours_MuGuard_Flake"):
        s_recs = [r["strategies"][strat] for r in rows]
        w_ok = [x for x in s_recs if x["w_plus_passed"]]
        eroded = [x for x in w_ok if x["eroded"]]
        sound = [x for x in s_recs if x["sound_fix"]]
        rets = [x["retention_m_det"] for x in w_ok if x.get("retention_m_det") is not None]
        g_rets = [x["retention_m_gold"] for x in w_ok if x.get("retention_m_gold") is not None]

        # Collect PIT probe latencies and total verification wall-clock per sample
        pit_secs: List[float] = []
        eval_wall_secs: List[float] = []
        for x in s_recs:
            sample_eval_t = 0.0
            for rd in x["rounds"]:
                ev = rd.get("eval")
                if ev:
                    sample_eval_t += ev.get("total_eval_sec", 0.0)
                    if ev.get("pit_ran") and ev.get("pit_sec", 0.0) > 0:
                        pit_secs.append(ev["pit_sec"])
            eval_wall_secs.append(sample_eval_t)

        # Breakdown by ID vs OD
        by_type = {}
        for ttype in ("ID", "OD"):
            sub = [r["strategies"][strat] for r in rows if r["test_type"] == ttype]
            if sub:
                sw = [x for x in sub if x["w_plus_passed"]]
                se = [x for x in sw if x["eroded"]]
                ss = [x for x in sub if x["sound_fix"]]
                by_type[ttype] = {
                    "n": len(sub),
                    "w_plus": len(sw),
                    "w_plus_pct": round(100.0 * len(sw) / len(sub), 2),
                    "eroded": len(se),
                    "erosion_pct_among_w_plus": round(100.0 * len(se) / len(sw), 2) if sw else 0.0,
                    "sound_fix": len(ss),
                    "sound_fix_pct": round(100.0 * len(ss) / len(sub), 2),
                }

        # Cumulative Round 1..5 trajectory
        traj = {}
        for k in range(1, 6):
            w_k, e_k, s_k = 0, 0, 0
            # Also count ANY eroded W+-passing candidate encountered by round k (important for Ours pre-gate vs post-gate!)
            raw_eroded_seen_by_k = 0
            for x in s_recs:
                if x["accepted"] and x["accepted_round"] is not None and x["accepted_round"] <= k:
                    if x["w_plus_passed"]:
                        w_k += 1
                        if x["eroded"]:
                            e_k += 1
                        else:
                            s_k += 1
                if any(
                    rd["round"] <= k and rd.get("eval") and rd["eval"].get("w_plus_passed") and rd["eval"].get("eroded")
                    for rd in x["rounds"]
                ):
                    raw_eroded_seen_by_k += 1
            traj[f"R{k}"] = {
                "w_plus_count": w_k,
                "pass_w_plus_pct": round(100.0 * w_k / n_total, 2),
                "leaked_eroded_count": e_k,
                "leaked_erosion_rate_among_w_plus_pct": round(100.0 * e_k / w_k, 2) if w_k else 0.0,
                "sound_fix_count": s_k,
                "sound_fix_pct": round(100.0 * s_k / n_total, 2),
                "candidates_triggering_erosion_by_rk": raw_eroded_seen_by_k,
            }

        summary["strategies"][strat] = {
            "n_total": n_total,
            "w_plus_count": len(w_ok),
            "pass_w_plus_pct": round(100.0 * len(w_ok) / n_total, 2),
            "leaked_eroded_count": len(eroded),
            "leaked_erosion_rate_among_w_plus_pct": round(100.0 * len(eroded) / len(w_ok), 2) if w_ok else 0.0,
            "leaked_erosion_pct_of_all": round(100.0 * len(eroded) / n_total, 2),
            "sound_fix_count": len(sound),
            "sound_fix_pct": round(100.0 * len(sound) / n_total, 2),
            "mean_retention_m_det": round(statistics.mean(rets), 4) if rets else None,
            "mean_retention_m_gold": round(statistics.mean(g_rets), 4) if g_rets else None,
            "total_erosion_retries": sum(x["erosion_retries"] for x in s_recs),
            "total_ast_rejections": sum(x["ast_rejections"] for x in s_recs),
            "abstain_erosion_count": sum(1 for x in s_recs if x["gate_state"] == "ABSTAIN_EROSION"),
            "abstain_unstable_count": sum(1 for x in s_recs if x["gate_state"] == "ABSTAIN_UNSTABLE"),
            "median_pit_sec": round(statistics.median(pit_secs), 2) if pit_secs else None,
            "median_eval_wall_sec": round(statistics.median(eval_wall_secs), 2) if eval_wall_secs else None,
            "by_type": by_type,
        }
        summary["round_trajectories"][strat] = traj

    return summary


def run_security_and_isolation_audit() -> Dict[str, Any]:
    secrets = []
    try:
        _, host_ip, _ = load_remote_host()
        secrets.append(host_ip)
    except Exception:
        pass
    for kp in (DEEPSEEK_KEY_PATH, QWEN_KEY_PATH):
        if kp.exists():
            secrets.append(load_api_key(kp))

    leaks: List[str] = []
    scanned_files = 0
    for p in FT1_ROOT.rglob("*"):
        if p.is_file() and "__pycache__" not in str(p) and ".git" not in p.parts:
            scanned_files += 1
            txt = p.read_text(encoding="utf-8", errors="ignore")
            for sec in secrets:
                if sec and len(sec) > 5 and sec in txt:
                    leaks.append(f"Secret match in {p.relative_to(FT1_ROOT)}")
            if re.search(r"sk-[A-Za-z0-9]{16,}", txt):
                leaks.append(f"sk- token pattern in {p.relative_to(FT1_ROOT)}")
            pk1 = "BEGIN " + "RSA PRIVATE KEY"
            pk2 = "BEGIN " + "OPENSSH PRIVATE KEY"
            if pk1 in txt or pk2 in txt:
                leaks.append(f"Private key block in {p.relative_to(FT1_ROOT)}")

    git_proc = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "status", "--porcelain"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    outside_changes = []
    for ln in git_proc.stdout.splitlines():
        if not ln.strip():
            continue
        rel_path = ln[3:].strip().strip('"').replace("\\", "/")
        if REPO_ROOT != FT1_ROOT and not rel_path.startswith("research/FT1"):
            outside_changes.append(ln)

    api_calls = 0
    if API_LOG_PATH.exists():
        api_calls = sum(1 for ln in API_LOG_PATH.read_text(encoding="utf-8").splitlines() if ln.strip())

    return {
        "scanned_ft1_files": scanned_files,
        "secret_leaks_found": leaks,
        "outside_ft1_git_changes": outside_changes,
        "logged_api_calls": api_calls,
    }


def main() -> None:
    out: Dict[str, Any] = {}
    bench_p = DATA_DIR / "pilot_benchmark.json"
    if bench_p.exists():
        bench = json.loads(bench_p.read_text(encoding="utf-8"))
        samples = bench["samples"]
        pit_meds = [s["pit_median_sec"] for s in samples]
        m_counts = [s["m_det_count"] for s in samples]
        out["benchmark"] = {
            "total_samples": len(samples),
            "id_count": bench["id_count"],
            "od_count": bench["od_count"],
            "num_projects": len(bench["distinct_projects"]),
            "projects": bench["distinct_projects"],
            "m_det_total_mutants": sum(m_counts),
            "m_det_median": statistics.median(m_counts) if m_counts else 0,
            "m_det_mean": round(statistics.mean(m_counts), 2) if m_counts else 0,
            "m_det_min_max": [min(m_counts), max(m_counts)] if m_counts else [0, 0],
            "m_gold_samples": sum(1 for s in samples if s.get("m_gold_ids")),
            "m_gold_total_mutants": sum(len(s.get("m_gold_ids", [])) for s in samples),
            "pit_single_test_median_sec": round(statistics.median(pit_meds), 2) if pit_meds else 0,
            "pit_single_test_mean_sec": round(statistics.mean(pit_meds), 2) if pit_meds else 0,
            "pit_single_test_p90_sec": round(sorted(pit_meds)[int(0.9 * (len(pit_meds) - 1))], 2) if pit_meds else 0,
            "pit_single_test_max_sec": max(pit_meds) if pit_meds else 0,
        }
        out["b1_published_sota"] = summarize_b1(samples)

    for res_p in sorted(DATA_DIR.glob("pilot_results_*.json")):
        rdata = json.loads(res_p.read_text(encoding="utf-8"))
        out[res_p.stem] = summarize_step2(rdata)

    out["security_audit"] = run_security_and_isolation_audit()
    summary_p = DATA_DIR / "pilot_summary_metrics.json"
    summary_p.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
