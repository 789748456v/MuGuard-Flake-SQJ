"""
FT-1 v3.0 Cross-Vendor LLM-Judge Single-Pass (K=1) Gate Screening on Kimi K2.5 & GLM-5.

Evaluates `B6_LLM_Judge_Gate` (using independent Alibaba `qwen3.7-plus` as cross-vendor
semantic judge over Moonshot `kimi-k2.5` and Zhipu `glm-5` Round-1 W+-passing patches)
with ZERO extra Docker executions (all Round-1 W+ & mutation ground truth is already frozen
in `v2_round1_kimi-k2.5.json` and `v2_round1_glm-5.json`).

Measures BOTH:
  1. Leaked erosion rate (eroded patches falsely accepted by LLM-Judge)
  2. False-rejection cost on sound patches (sound Round-1 fixes wrongly rejected by LLM-Judge)
  3. Resulting operational `SoundFix%` directly comparable to Table 7 (`Unconstrained`, `Prompt`,
     `Syntactic`, `Structural`, `MuGuard-Flake`)!
"""
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent))
from remote_and_llm import DATA_DIR
from v3_llm_judge_baseline import call_judge_cached, load_cache, save_cache

OUT_PATH = DATA_DIR / "v3_k1_llm_judge_screening.json"


def main() -> None:
    cache = load_cache()
    bm = json.loads((DATA_DIR / "v2_frozen_benchmark_light.json").read_text(encoding="utf-8"))
    bm_by_id = {s["sample_id"]: s for s in bm["samples"]}

    judge_model = "qwen3.7-plus"
    summary: Dict[str, Any] = {"protocol_version": "3.0.0-k1-llm-judge", "judge_model": judge_model, "models": {}}

    for m in ("kimi-k2.5", "glm-5"):
        res = json.loads((DATA_DIR / f"v2_round1_{m}.json").read_text(encoding="utf-8"))["results"]
        elig = [r for r in res if bm_by_id.get(r["sample_id"], {}).get("v2_eligible")]

        # Note: Round-1 guarded prompt (`B2_Prompt_Guard`) is identical to MuGuard-Flake's Round-1 draft!
        jobs = []
        for r in elig:
            sid = r["sample_id"]
            st = r["strategies"]["B2_Prompt_Guard"]
            if not st.get("w_plus_passed"):
                continue
            cand = st.get("final_candidate") or (st["rounds"][0].get("candidate") if st.get("rounds") else {}) or {}
            s = bm_by_id[sid]
            cand_t = cand.get("test_code") or cand.get("victim_code") or ""
            cand_p = cand.get("polluter_code") or ""
            jobs.append((sid, s, st, cand_t, cand_p))

        print(f"[K1 LLM-JUDGE {m}] Judging {len(jobs)} W+-passing Round-1 patches with {judge_model}...", flush=True)
        verdicts: Dict[str, Dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=8) as ex:
            futs = {
                ex.submit(
                    call_judge_cached,
                    cache,
                    judge_model,
                    s["language"],
                    s.get("od_subtype") or s.get("test_type", ""),
                    s.get("test_full_name", sid),
                    s.get("orig_test_code", ""),
                    cand_t,
                    s.get("orig_polluter_code", ""),
                    cand_p,
                    "",
                    f"v3_k1_judge_{m}",
                ): sid
                for sid, s, st, cand_t, cand_p in jobs
            }
            done_n = 0
            for fut in as_completed(futs):
                sid = futs[fut]
                verdicts[sid] = fut.result()
                done_n += 1
                if done_n % 25 == 0 or done_n == len(futs):
                    save_cache(cache)
                    print(f"  {m} progress: {done_n}/{len(futs)}", flush=True)

        # Compute Table-7-aligned metrics!
        n_elig = len(elig)
        acc = 0
        leaked_eroded = 0
        unver = 0
        sound_acc = 0
        sound_false_rej = 0
        eroded_caught = 0
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
                    sound_acc += 1
                elif is_er:
                    leaked_eroded += 1
                else:
                    unver += 1
            else:
                if is_sf:
                    sound_false_rej += 1
                elif is_er:
                    eroded_caught += 1

        summary["models"][m] = {
            "n_eligible": n_elig,
            "b2_w_plus_pool": len(jobs),
            "judge_accepted_w_plus": acc,
            "judge_accepted_pct": round(acc / n_elig * 100, 2),
            "leaked_eroded": leaked_eroded,
            "unver": unver,
            "leak_among_accepted_pct": round(leaked_eroded / max(1, acc) * 100, 2),
            "sound_fix": sound_acc,
            "sound_fix_pct": round(sound_acc / n_elig * 100, 2),
            "eroded_caught": eroded_caught,
            "sound_false_rejected": sound_false_rej,
            "median_judge_sec": round(sorted(lats)[len(lats) // 2], 2) if lats else 0.0,
        }

    save_cache(cache)
    OUT_PATH.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print("\n=== K=1 CROSS-VENDOR LLM-JUDGE SCREENING SUMMARY ===")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
