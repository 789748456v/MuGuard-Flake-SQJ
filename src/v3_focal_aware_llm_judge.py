"""
FT-1 v3.1 Focal-Aware Cross-Vendor LLM-Judge Offline & PR Evaluation (`v3_focal_aware_llm_judge.py`).

Tests whether augmenting the cross-vendor LLM-Judge (`v3_llm_judge_baseline.py`) with the
dynamically executed focal production functions (`F <= 5` from `data/v3_flakyguard_dcg_context.json`)
closes the >2/3 erosion-detection blind spot across:
  1. All 400 unique W+-passing eroded patches (strictly cross-vendor: DeepSeek/Moonshot/Zhipu -> Qwen 3.7+;
     Qwen 3.7+ -> Zhipu GLM-5).
  2. All 43 W+-passing test-only Java developer pull requests (with full PR test-file unified diffs,
     evaluated on both `qwen3.7-plus` and `glm-5`).

Saves complete results and mechanism/language breakdowns to `data/v3_focal_aware_llm_judge.json`.
"""
import argparse
import json
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_v3_human_audit_pack import build_candidate_lookup
from remote_and_llm import DATA_DIR
from v3_llm_judge_baseline import (
    CACHE_LOCK,
    build_full_pr_unified_diff,
    call_judge_cached,
    cross_vendor_judge_for,
    load_cache,
    save_cache,
)

DCG_PATH = DATA_DIR / "v3_flakyguard_dcg_context.json"
OUT_PATH = DATA_DIR / "v3_focal_aware_llm_judge.json"


def format_focal_extra_context(dcg_entry: Dict[str, Any], lang: str, max_total_chars: int = 5500) -> str:
    funcs = dcg_entry.get("selected_functions") or []
    if not funcs:
        return ""
    blocks = ["=== FOCAL PRODUCTION SOURCE FUNCTIONS EXECUTED BY TEST (Dynamic Call Graph, F<=5) ==="]
    used = 0
    fence = "java" if lang == "java" else "python"
    for idx, fn in enumerate(funcs[:5], 1):
        code = (fn.get("code") or "").strip()
        if used + len(code) > max_total_chars:
            rem = max(300, max_total_chars - used)
            code = code[:rem] + "\n/* ... truncated ... */"
        blk = (
            f"[Focal #{idx}] `{fn['signature']}` (`{fn['rel_file']}:L{fn['start_line']}-L{fn['end_line']}`)\n"
            f"```{fence}\n{code}\n```"
        )
        blocks.append(blk)
        used += len(code)
        if used >= max_total_chars:
            break
    return "\n".join(blocks)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    assert DCG_PATH.exists(), f"Missing {DCG_PATH}; run v31_build_flakyguard_dcg.py first!"
    dcg_samples = json.loads(DCG_PATH.read_text(encoding="utf-8"))["samples"]
    cache = load_cache()

    bm = json.loads((DATA_DIR / "v2_frozen_benchmark_light.json").read_text(encoding="utf-8"))
    bm_by_id = {s["sample_id"]: s for s in bm["samples"]}
    by_cand_key, by_unique_key = build_candidate_lookup(bm_by_id)

    # 1. All 43 W+-passing test-only Java PRs with full PR diff + F<=5 focal production source!
    pr_man = json.loads((DATA_DIR / "pr_reference_manifest.json").read_text(encoding="utf-8"))["entries"]
    pr_adj = json.loads((DATA_DIR / "pr_independent_adjudication_input.json").read_text(encoding="utf-8"))["entries"]
    pr_diff_cache = json.loads((DATA_DIR / "v3_pr_diff_cache.json").read_text(encoding="utf-8"))
    pr_43 = [
        e for e in pr_man
        if e.get("pr_apply_outcome") == "CLEAN_FULL_TEST_APPLY"
        and e.get("pr_scope_type") == "TEST_ONLY_PR"
        and e.get("w_plus_passed")
    ]

    pr_jobs = []
    for e in pr_43:
        sid = e["sample_id"]
        s = bm_by_id[sid]
        full_pr_diff = build_full_pr_unified_diff(s, e, pr_diff_cache)
        focal_ctx = format_focal_extra_context(dcg_samples.get(sid) or {}, "java")
        for jm in ("qwen3.7-plus", "glm-5"):
            pr_jobs.append((sid, jm, s, e, full_pr_diff, focal_ctx))

    print(f"[FOCAL-JUDGE] Evaluating {len(pr_43)} full-diff PRs x 2 judges with F<=5 focal source...", flush=True)
    pr_results: Dict[str, Dict[str, Any]] = defaultdict(dict)
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {
            ex.submit(
                call_judge_cached,
                cache,
                jm,
                "java",
                s.get("od_subtype") or s.get("test_type", ""),
                s.get("test_full_name", sid),
                s.get("orig_test_code", ""),
                e.get("applied_victim_code", ""),
                s.get("orig_polluter_code", ""),
                e.get("applied_polluter_code", ""),
                focal_ctx,
                f"v31_focal_pr_judge_{jm}",
                full_pr_diff,
            ): (sid, jm)
            for sid, jm, s, e, full_pr_diff, focal_ctx in pr_jobs
        }
        done_n = 0
        for fut in as_completed(futs):
            sid, jm = futs[fut]
            pr_results[sid][jm] = fut.result()
            done_n += 1
            if done_n % 20 == 0 or done_n == len(futs):
                with CACHE_LOCK:
                    save_cache(cache)
                print(f"  [FOCAL PR] {done_n}/{len(futs)}", flush=True)

    pr_summary: Dict[str, Any] = {}
    for jm in ("qwen3.7-plus", "glm-5"):
        merged_rej = merged_tot = legit_rej = legit_tot = weak_caught = weak_tot = shift_caught = shift_tot = 0
        lats = []
        for e in pr_43:
            sid = e["sample_id"]
            adj = pr_adj.get(sid) or {}
            is_legit = bool(adj.get("is_legitimate_repair"))
            lbl = adj.get("label", "")
            jr = pr_results[sid][jm]
            j_rej = jr.get("verdict") != "SAFE_PASS"
            if jr.get("latency_sec"):
                lats.append(jr["latency_sec"])
            if e.get("idoft_status") == "Accepted":
                merged_tot += 1
                if j_rej:
                    merged_rej += 1
            if is_legit:
                legit_tot += 1
                if j_rej:
                    legit_rej += 1
            elif lbl == "GENUINE_ORACLE_WEAKENING":
                weak_tot += 1
                if j_rej:
                    weak_caught += 1
            elif lbl == "TEST_RESPONSIBILITY_SHIFT":
                shift_tot += 1
                if j_rej:
                    shift_caught += 1
        ls_rej = legit_rej + shift_caught
        ls_tot = legit_tot + shift_tot
        pr_summary[jm] = {
            "merged_rej": f"{merged_rej}/{merged_tot} ({merged_rej/max(1,merged_tot)*100:.1f}%)",
            "legit_false_rej": f"{legit_rej}/{legit_tot}",
            "legit_false_rej_pct": round(legit_rej / max(1, legit_tot) * 100, 2),
            "legit_plus_shift_rej": f"{ls_rej}/{ls_tot} ({ls_rej/max(1,ls_tot)*100:.1f}%)",
            "weakening_caught": f"{weak_caught}/{weak_tot}",
            "shift_caught": f"{shift_caught}/{shift_tot}",
            "median_judge_sec": round(sorted(lats)[len(lats) // 2], 2) if lats else 0.0,
        }

    # 2. All 400 unique eroded patches with F<=5 focal production source!
    tax_cb = json.loads((DATA_DIR / "taxonomy_audit_codebook.json").read_text(encoding="utf-8"))["entries"]
    eroded_jobs = []
    for e in tax_cb:
        uk = e["unique_patch_key"]
        sid = e["sample_id"]
        tail_fp = uk.split("::")[-1]
        rec = by_unique_key.get(uk) or by_cand_key.get(f"{sid}::{tail_fp}")
        if not rec:
            continue
        jm = cross_vendor_judge_for(e.get("first_seen_model", ""))
        focal_ctx = format_focal_extra_context(dcg_samples.get(sid) or {}, rec["language"])
        eroded_jobs.append((uk, jm, e, rec, focal_ctx))

    print(f"[FOCAL-JUDGE] Evaluating {len(eroded_jobs)}/400 unique eroded patches with F<=5 focal source...", flush=True)
    eroded_results: Dict[str, Dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {
            ex.submit(
                call_judge_cached,
                cache,
                jm,
                rec["language"],
                rec.get("od_subtype", ""),
                rec.get("test_full_name", rec["sample_id"]),
                rec["orig_test_code"],
                rec["cand_test_code"],
                rec.get("orig_partner_code", ""),
                rec.get("cand_partner_code", ""),
                focal_ctx,
                f"v31_focal_eroded400_{jm}",
            ): (uk, e)
            for uk, jm, e, rec, focal_ctx in eroded_jobs
        }
        done_n = 0
        for fut in as_completed(futs):
            uk, _ = futs[fut]
            eroded_results[uk] = fut.result()
            done_n += 1
            if done_n % 25 == 0 or done_n == len(futs):
                with CACHE_LOCK:
                    save_cache(cache)
                print(f"  [FOCAL ERODED-400] {done_n}/{len(futs)}", flush=True)

    with CACHE_LOCK:
        save_cache(cache)

    by_mech = defaultdict(lambda: {"total": 0, "judge_detected": 0, "judge_missed": 0})
    by_lang = defaultdict(lambda: {"total": 0, "judge_detected": 0, "judge_missed": 0})
    tot_eval = tot_det = tot_miss = 0
    lats_all = []
    for uk, jm, e, rec, _ in eroded_jobs:
        jr = eroded_results[uk]
        mech = (e.get("coding") or {}).get("primary_mechanism", "UNKNOWN")
        lang = rec["language"]
        tot_eval += 1
        by_mech[mech]["total"] += 1
        by_lang[lang]["total"] += 1
        if jr.get("latency_sec"):
            lats_all.append(jr["latency_sec"])
        if jr.get("verdict") == "EROSION_DETECTED":
            tot_det += 1
            by_mech[mech]["judge_detected"] += 1
            by_lang[lang]["judge_detected"] += 1
        else:
            tot_miss += 1
            by_mech[mech]["judge_missed"] += 1
            by_lang[lang]["judge_missed"] += 1

    for d in list(by_mech.values()) + list(by_lang.values()):
        d["recall_pct"] = round(d["judge_detected"] / max(1, d["total"]) * 100, 2)
        d["bypass_pct"] = round(d["judge_missed"] / max(1, d["total"]) * 100, 2)

    out_obj = {
        "protocol_version": "3.1.0-focal-aware-cross-vendor-llm-judge",
        "focal_context_source": "v3_flakyguard_dcg_context.json (F<=5 dynamically executed focal functions)",
        "pr_43_summary": pr_summary,
        "eroded_400_summary": {
            "total_evaluated": tot_eval,
            "judge_detected": tot_det,
            "judge_missed_leaked": tot_miss,
            "detection_recall_pct": round(tot_det / max(1, tot_eval) * 100, 2),
            "bypass_rate_pct": round(tot_miss / max(1, tot_eval) * 100, 2),
            "median_judge_sec": round(sorted(lats_all)[len(lats_all) // 2], 2) if lats_all else 0.0,
            "by_mechanism": dict(sorted(by_mech.items())),
            "by_language": dict(sorted(by_lang.items())),
        },
        "pr_43_verdicts": dict(pr_results),
        "eroded_400_verdicts": eroded_results,
    }
    OUT_PATH.write_text(json.dumps(out_obj, indent=2, ensure_ascii=False), encoding="utf-8")
    print("=== FOCAL-AWARE LLM-JUDGE SUMMARY ===", flush=True)
    print(json.dumps({"pr_43_summary": pr_summary, "eroded_400_summary": out_obj["eroded_400_summary"]}, indent=2), flush=True)


if __name__ == "__main__":
    main()
