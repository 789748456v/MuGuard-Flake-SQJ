"""
FT-1 v3.0 Witness-Augmented Hybrid Triage Experiment (`MuGuard-Flake` + Witness-Informed LLM Triage).

Tests two critical questions for CI deployment:
  1. On the 9 Java PRs rejected by strict `MuGuard-Flake` (8 legitimate canonicalization/fixture fixes
     + 1 Nacos genuine weakening), does supplying `MuGuard-Flake`'s surviving-mutant witnesses to a
     cross-vendor LLM reviewer (`qwen3.7-plus` and `glm-5`) eliminate legitimate false rejections
     WITHOUT overriding the Nacos weakening?
  2. On the 60 active stratified eroded patches (where diff-only LLM-Judge misses >80%), how much does
     supplying `MuGuard-Flake`'s surviving-mutant witnesses lift the Judge's erosion recognition
     (preventing unsafe overrides in hybrid mode)?

Outputs: `data/v3_witness_hybrid_triage.json`
"""
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_v3_human_audit_pack import build_candidate_lookup
from remote_and_llm import DATA_DIR
from v3_llm_judge_baseline import call_judge_cached, cross_vendor_judge_for, load_cache, save_cache

OUT_PATH = DATA_DIR / "v3_witness_hybrid_triage.json"


def format_witness_block(muts: List[Any]) -> str:
    if not muts:
        return "=== SURVIVING MUTANTS FROM MUGUARD-FLAKE ===\n(None recorded)"
    lines = [
        "=== SURVIVING MUTANTS FROM MUGUARD-FLAKE ===",
        "The original test KILLED these production-code mutants (3/3 isolated runs), but the candidate patched test lets them SURVIVE:",
    ]
    for idx, m in enumerate(muts[:6], 1):
        if isinstance(m, dict):
            cls = m.get("mutatedClass", "").split(".")[-1]
            meth = m.get("mutatedMethod", "")
            ln = m.get("lineNumber", "")
            desc = m.get("description", "")
            sl = m.get("source_slice", "")
            lines.append(f"[{idx}] {cls}::{meth}:L{ln} -- {desc}")
            if sl:
                lines.append(sl[:400])
        else:
            lines.append(f"[{idx}] {str(m)[:260]}")
    lines.append(
        "Decide strictly: are these surviving mutants ONLY an unavoidable artifact of legitimate deterministic canonicalization/fixture isolation (SAFE_PASS), OR do they expose genuine fault-detection weakening / test responsibility shift (EROSION_DETECTED)?"
    )
    return "\n".join(lines)


def main() -> None:
    cache = load_cache()
    bm = json.loads((DATA_DIR / "v2_frozen_benchmark_light.json").read_text(encoding="utf-8"))
    bm_by_id = {s["sample_id"]: s for s in bm["samples"]}
    by_cand_key, by_unique_key = build_candidate_lookup(bm_by_id)

    # 1. All MuGuard-rejected W+-passing test-only Java PRs (8 legit + 1 Nacos weakening = 9)
    pr_man = json.loads((DATA_DIR / "pr_reference_manifest.json").read_text(encoding="utf-8"))["entries"]
    pr_adj = json.loads((DATA_DIR / "pr_independent_adjudication_input.json").read_text(encoding="utf-8"))["entries"]
    rej_prs = [
        e for e in pr_man
        if e.get("pr_apply_outcome") == "CLEAN_FULL_TEST_APPLY"
        and e.get("pr_scope_type") == "TEST_ONLY_PR"
        and e.get("w_plus_passed")
        and not e.get("pr_full_gate_accepts")
    ]
    print(f"[HYBRID TRIAGE] Testing {len(rej_prs)} MuGuard-rejected PRs with surviving-mutant witnesses...")

    pr_out: Dict[str, Dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {}
        for e in rej_prs:
            sid = e["sample_id"]
            s = bm_by_id[sid]
            dev_ev = s.get("v2_dev_pr_eval") or {}
            muts = dev_ev.get("survived_mutant_summaries") or dev_ev.get("lost_mutants") or [f"{e.get('lost_mutant_count_3x', '?')} baseline PIT mutants survived (retention={e.get('retention_m_det_3x')})"]
            wb = format_witness_block(muts)
            for jm in ("qwen3.7-plus", "glm-5"):
                futs[ex.submit(
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
                    wb,
                    f"v3_witness_triage_pr_{jm}",
                )] = (sid, jm)
        for fut in as_completed(futs):
            sid, jm = futs[fut]
            pr_out.setdefault(sid, {})[jm] = fut.result()

    # 2. All 60 active stratified eroded patches (from v3_human_audit_key.json) with vs without witnesses!
    hkey = json.loads((DATA_DIR / "v3_human_audit_key.json").read_text(encoding="utf-8"))["tax_60"]
    tax_cb = {e["unique_patch_key"]: e for e in json.loads((DATA_DIR / "taxonomy_audit_codebook.json").read_text(encoding="utf-8"))["entries"]}

    print(f"[HYBRID TRIAGE] Testing {len(hkey)} active stratified eroded patches with vs without witnesses...")
    tax_out: Dict[str, Any] = {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {}
        for bid, meta in sorted(hkey.items()):
            uk = meta["unique_patch_key"]
            sid = meta["sample_id"]
            tail_fp = uk.split("::")[-1]
            rec = by_unique_key.get(uk) or by_cand_key.get(f"{sid}::{tail_fp}")
            cb_e = tax_cb.get(uk) or {}
            jm = cross_vendor_judge_for(cb_e.get("first_seen_model", ""))
            muts = rec.get("survived_summaries") or cb_e.get("survived_summaries") or []
            wb = format_witness_block(muts)
            # Without witness (cache hit from v3_llm_judge_baseline!)
            futs[ex.submit(
                call_judge_cached,
                cache, jm, rec["language"], rec.get("od_subtype", ""), rec.get("test_full_name", sid),
                rec["orig_test_code"], rec["cand_test_code"], rec.get("orig_partner_code", ""), rec.get("cand_partner_code", ""),
                "", f"v3_triage_tax_nowit_{jm}",
            )] = (bid, "no_witness", meta)
            # With MuGuard surviving-mutant witnesses!
            futs[ex.submit(
                call_judge_cached,
                cache, jm, rec["language"], rec.get("od_subtype", ""), rec.get("test_full_name", sid),
                rec["orig_test_code"], rec["cand_test_code"], rec.get("orig_partner_code", ""), rec.get("cand_partner_code", ""),
                wb, f"v3_triage_tax_withwit_{jm}",
            )] = (bid, "with_witness", meta)

        for fut in as_completed(futs):
            bid, arm, meta = futs[fut]
            tax_out.setdefault(bid, {"meta": meta})[arm] = fut.result()

    save_cache(cache)

    # Summarize results
    pr_sum = {}
    for jm in ("qwen3.7-plus", "glm-5"):
        legit_still_rej = 0
        legit_n = 0
        weak_still_caught = 0
        weak_n = 0
        for e in rej_prs:
            sid = e["sample_id"]
            adj = pr_adj.get(sid) or {}
            verdict = pr_out[sid][jm].get("verdict")
            rej = (verdict != "SAFE_PASS")
            if adj.get("is_legitimate_repair"):
                legit_n += 1
                if rej:
                    legit_still_rej += 1
            elif adj.get("label") == "GENUINE_ORACLE_WEAKENING":
                weak_n += 1
                if rej:
                    weak_still_caught += 1
        pr_sum[jm] = {
            "legit_false_rej_after_witness_triage": f"{legit_still_rej}/39 ({legit_still_rej/39*100:.2f}%)",
            "legit_resolved_of_8": f"{legit_n - legit_still_rej}/{legit_n}",
            "nacos_weakening_still_caught": f"{weak_still_caught}/{weak_n}",
        }

    # On 60 stratified eroded patches (excluding legitimate canonicalization if any)
    nw_det = sum(1 for v in tax_out.values() if v["no_witness"].get("verdict") == "EROSION_DETECTED")
    ww_det = sum(1 for v in tax_out.values() if v["with_witness"].get("verdict") == "EROSION_DETECTED")
    nonlegit = [v for v in tax_out.values() if v["meta"].get("stage1_severity") != "LEGITIMATE_RELAXATION_OR_CANONICALIZATION"]
    nw_nl = sum(1 for v in nonlegit if v["no_witness"].get("verdict") == "EROSION_DETECTED")
    ww_nl = sum(1 for v in nonlegit if v["with_witness"].get("verdict") == "EROSION_DETECTED")

    summary = {
        "protocol_version": "3.0.0-witness-hybrid-triage",
        "pr_9_muguard_rejected_triage": pr_sum,
        "tax_60_erosion_recognition": {
            "total_60": len(tax_out),
            "diff_only_detected": nw_det,
            "diff_only_pct": round(nw_det / max(1, len(tax_out)) * 100, 2),
            "with_witness_detected": ww_det,
            "with_witness_pct": round(ww_det / max(1, len(tax_out)) * 100, 2),
            "lift_pp": round((ww_det - nw_det) / max(1, len(tax_out)) * 100, 2),
            "nonlegit_n": len(nonlegit),
            "nonlegit_diff_only_pct": round(nw_nl / max(1, len(nonlegit)) * 100, 2),
            "nonlegit_with_witness_pct": round(ww_nl / max(1, len(nonlegit)) * 100, 2),
        },
        "pr_details": pr_out,
        "tax_details": tax_out,
    }
    OUT_PATH.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print("\n=== WITNESS-AUGMENTED HYBRID TRIAGE SUMMARY ===")
    print(json.dumps({k: v for k, v in summary.items() if k not in ("pr_details", "tax_details")}, indent=2))


if __name__ == "__main__":
    main()
