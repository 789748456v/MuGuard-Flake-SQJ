"""
FT-1 v3.0 Cross-Vendor LLM-Judge K=1 Screening & Cache Pre-Warmer for K=5 Backbones
(`deepseek-flash`, `deepseek-v4-pro` judged by `qwen3.7-plus`; `qwen3.7-plus` judged by `glm-5`).

Zero Docker usage — runs strictly over cached Round-1 W+-passing patches in `v2_results_*.json`
to:
  1. Measure K=1 cross-vendor LLM-Judge leaked erosion vs. sound-fix false rejections on all 5 models.
  2. Pre-warm `v3_llm_judge_cache.json` so Full K=5 `B6_LLM_Judge_Gate` hits 100% cache on Round 1!
"""
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict

sys.path.insert(0, str(Path(__file__).resolve().parent))
from remote_and_llm import DATA_DIR
from v3_llm_judge_baseline import call_judge_cached, cross_vendor_judge_for, load_cache, save_cache

OUT_PATH = DATA_DIR / "v3_k1_llm_judge_k5models.json"


def main() -> None:
    cache = load_cache()
    bm = json.loads((DATA_DIR / "v2_frozen_benchmark_light.json").read_text(encoding="utf-8"))
    bm_by_id = {s["sample_id"]: s for s in bm["samples"]}

    summary: Dict[str, Any] = {"protocol_version": "3.0.0-k1-judge-k5models", "models": {}}

    for m in ("qwen3.7-plus", "deepseek-flash", "deepseek-v4-pro"):
        jm = cross_vendor_judge_for(m)
        res = json.loads((DATA_DIR / f"v2_results_{m}.json").read_text(encoding="utf-8"))["results"]
        elig = [r for r in res if bm_by_id.get(r["sample_id"], {}).get("v2_eligible")]

        # Evaluate on B2_Prompt_Guard accepted patches (same guarded prompt as MuGuard-Flake!)
        jobs = []
        for r in elig:
            sid = r["sample_id"]
            st = r["strategies"]["B2_Prompt_Guard"]
            if not st.get("w_plus_passed"):
                continue
            cand = st.get("final_candidate") or (st["rounds"][-1].get("candidate") if st.get("rounds") else {}) or {}
            s = bm_by_id[sid]
            cand_t = cand.get("test_code") or cand.get("victim_code") or ""
            cand_p = cand.get("polluter_code") or ""
            jobs.append((sid, s, st, cand_t, cand_p))

        print(f"[PREWARM JUDGE {m} | judge={jm}] Judging {len(jobs)} W+-passing B2 patches...", flush=True)
        verdicts: Dict[str, Dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=8) as ex:
            futs = {
                ex.submit(
                    call_judge_cached,
                    cache,
                    jm,
                    s["language"],
                    s.get("od_subtype") or s.get("test_type", ""),
                    s.get("test_full_name", sid),
                    s.get("orig_test_code", ""),
                    cand_t,
                    s.get("orig_polluter_code", ""),
                    cand_p,
                    "",
                    f"v3_prewarm_judge_{m}",
                ): sid
                for sid, s, st, cand_t, cand_p in jobs
            }
            done_n = 0
            for fut in as_completed(futs):
                sid = futs[fut]
                verdicts[sid] = fut.result()
                done_n += 1
                if done_n % 30 == 0 or done_n == len(futs):
                    save_cache(cache)
                    print(f"  {m} progress: {done_n}/{len(futs)}", flush=True)

        n_elig = len(elig)
        acc = leaked = unver = sf_acc = sf_rej = er_caught = 0
        lats = []
        for sid, s, st, _, _ in jobs:
            vr = verdicts[sid]
            if vr.get("latency_sec"):
                lats.append(vr["latency_sec"])
            j_acc = (vr.get("verdict") == "SAFE_PASS")
            is_er = bool(st.get("eroded"))
            is_sf = bool(st.get("sound_fix"))
            if j_acc:
                acc += 1
                if is_sf:
                    sf_acc += 1
                elif is_er:
                    leaked += 1
                else:
                    unver += 1
            else:
                if is_sf:
                    sf_rej += 1
                elif is_er:
                    er_caught += 1

        summary["models"][m] = {
            "judge_model": jm,
            "n_eligible": n_elig,
            "b2_w_plus_pool": len(jobs),
            "judge_accepted": acc,
            "leaked_eroded": leaked,
            "unver": unver,
            "leak_among_accepted_pct": round(leaked / max(1, acc) * 100, 2),
            "sound_fix": sf_acc,
            "sound_fix_pct": round(sf_acc / n_elig * 100, 2),
            "eroded_caught": er_caught,
            "sound_false_rejected": sf_rej,
            "median_judge_sec": round(sorted(lats)[len(lats) // 2], 2) if lats else 0.0,
        }

    save_cache(cache)
    OUT_PATH.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print("\n=== K=5 BACKBONES POST-GATE LLM-JUDGE SUMMARY ===")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
