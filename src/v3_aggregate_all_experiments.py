"""
FT-1 v3.0 Master Statistical & Comparative Aggregator (`v3_aggregate_all_experiments.py`).

Unifies EVERY v3.0 experimental artifact alongside certified v2.3 baselines with
20,000-resample whole-repository cluster bootstrap CIs and exact Rademacher sign-flip
permutation tests into `data/v3_master_summary.json`:
  1. Complete 8-Strategy Comparison on Confirmatory `Qwen 3.7 Plus` (including `B5_Coverage_Gate`
     and `B6_LLM_Judge_Gate` with project-clustered paired inference & break-even lambda*).
  2. 4-Paradigm Mechanism Detection Matrix across all 400 eroded patches + 43 Java PRs.
  3. Gate Threshold Pareto Curve (`tau = 1.00 .. 0.80`) + Witness-Augmented Hybrid Triage.
  4. Historical SOTA Matched-Cohort Head-to-Head + Plug-in Post-Gate.
  5. 5-Model Cross-Vendor `LLM-Judge` Post-Gate Screening.
  6. Fair Python Indentation Normalizer Ablation (full cohort + clustered delta).
  7. Online Top-10 Live Loop vs. Offline Top-10 vs. Full `MuGuard-Flake` (operational + held-out).
  8. Real Historical Production Bug-Revert Audit (file-level + line-targeted).
  9. Human Dual-Rater Blinded Audit (when available).
"""
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from remote_and_llm import DATA_DIR
from v3_offline_baselines_and_pareto import project_cluster_bootstrap_diff

OUT_PATH = DATA_DIR / "v3_master_summary.json"


def load_if(name: str) -> Any:
    p = DATA_DIR / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def main() -> None:
    bm = load_if("v2_frozen_benchmark_light.json")["samples"]
    s_by_id = {s["sample_id"]: s for s in bm if s.get("v2_eligible")}
    elig_ids = sorted(s_by_id.keys())
    e_ids = sorted(sid for sid, s in s_by_id.items() if s.get("m_ext_total_count", 0) > 0)

    q_v2 = {r["sample_id"]: r for r in load_if("v2_results_qwen3.7-plus.json")["results"]}
    ho_v2 = load_if("v2_heldout_validation.json")["evaluations"]
    ho_v3 = (load_if("v3_b5_b6_heldout_validation.json") or {}).get("evaluations", {})
    ho_gap = (load_if("v3_completed_heldout.json") or {}).get("evaluations", {})
    p3 = load_if("v3_python_indent_ablation.json") or {}
    ho_all = {**ho_v2, **ho_v3, **ho_gap, **(p3.get("heldout_evals") or {})}

    gap_k5 = (load_if("v3_completed_missing_k5.json") or {}).get("by_model", {})
    gap_a2 = (load_if("v3_completed_a2_forks.json") or {}).get("cases", {})

    b5 = (load_if("v3_b5_coverage_k5_qwen3.7-plus.json") or {}).get("results", {})
    b6 = (load_if("v3_b6_llm_judge_k5_qwen3.7-plus.json") or {}).get("results", {})
    topk = (load_if("v3_rq4_online_topk10_qwen.json") or {}).get("results", {})

    def v2_fp(sid: str, sn: str) -> str:
        st = q_v2[sid]["strategies"][sn]
        if not st.get("accepted"):
            return ""
        ar = st.get("accepted_round")
        return next((rd["candidate_fp"] for rd in st["rounds"] if rd["round"] == ar), "")

    def eval_ho_flags(sid: str, fp: str) -> Tuple[bool, bool, bool, bool]:
        """Returns (accepted, mext_loss, strict_ys, stress_ys)."""
        if not fp:
            return False, False, False, False
        ev = ho_all.get(f"{sid}::{fp}")
        if not ev or ev.get("m_ext_all_unknown", 0) > 0 or ev.get("m_ext_all_total", 0) == 0:
            return True, False, False, False
        ok_m = (ev["m_ext_all_killed"] == ev["m_ext_all_total"])
        return True, (not ok_m), bool(ok_m and ev.get("w_plus_heldout_passed_strict")), bool(ok_m and ev.get("w_plus_heldout_passed_target_stress"))

    # 1. Clustered inference on Qwen 3.7 Plus: MuGuard minus B6_LLM_Judge (n=203, |E|=177) & MuGuard minus B5_Coverage (matched n=195, |E_cov|=169)
    clustered_new: Dict[str, Any] = {}
    if len(b6) == 203:
        op_rows = [(s_by_id[i]["project_name"], 1.0 if q_v2[i]["strategies"]["Ours_MuGuard_Flake"]["sound_fix"] else 0.0, 1.0 if b6[i]["sound_fix"] else 0.0) for i in elig_ids]
        st_rows, sr_rows = [], []
        for i in e_ids:
            _, _, mg_s, mg_r = eval_ho_flags(i, v2_fp(i, "Ours_MuGuard_Flake"))
            _, _, b6_s, b6_r = eval_ho_flags(i, b6[i]["final_candidate_fp"] if b6[i]["accepted"] else "")
            st_rows.append((s_by_id[i]["project_name"], 1.0 if mg_s else 0.0, 1.0 if b6_s else 0.0))
            sr_rows.append((s_by_id[i]["project_name"], 1.0 if mg_r else 0.0, 1.0 if b6_r else 0.0))
        clustered_new["qwen_muguard_minus_b6_llm_judge"] = {
            "operational": project_cluster_bootstrap_diff(op_rows),
            "strict_ys": project_cluster_bootstrap_diff(st_rows),
            "stress_ys": project_cluster_bootstrap_diff(sr_rows),
        }

    if b5:
        b5_ids = sorted(i for i in elig_ids if i in b5)
        e_b5 = sorted(i for i in e_ids if i in b5)
        op_rows = [(s_by_id[i]["project_name"], 1.0 if q_v2[i]["strategies"]["Ours_MuGuard_Flake"]["sound_fix"] else 0.0, 1.0 if b5[i]["sound_fix"] else 0.0) for i in b5_ids]
        st_rows, sr_rows = [], []
        for i in e_b5:
            _, _, mg_s, mg_r = eval_ho_flags(i, v2_fp(i, "Ours_MuGuard_Flake"))
            _, _, b5_s, b5_r = eval_ho_flags(i, b5[i]["final_candidate_fp"] if b5[i]["accepted"] else "")
            st_rows.append((s_by_id[i]["project_name"], 1.0 if mg_s else 0.0, 1.0 if b5_s else 0.0))
            sr_rows.append((s_by_id[i]["project_name"], 1.0 if mg_r else 0.0, 1.0 if b5_r else 0.0))
        clustered_new["qwen_muguard_minus_b5_coverage_matched"] = {
            "n_op": len(b5_ids),
            "n_e": len(e_b5),
            "operational": project_cluster_bootstrap_diff(op_rows),
            "strict_ys": project_cluster_bootstrap_diff(st_rows),
            "stress_ys": project_cluster_bootstrap_diff(sr_rows),
        }

    # 2. Online Top-10 full summary (operational + heldout)
    topk_summary = None
    if topk:
        t_ids = sorted(i for i in elig_ids if i in topk)
        te_ids = sorted(i for i in e_ids if i in topk)
        t_acc = sum(1 for i in t_ids if topk[i]["accepted"])
        t_er = sum(1 for i in t_ids if topk[i]["eroded_full"])
        t_sf = sum(1 for i in t_ids if topk[i]["sound_fix_full"])
        ho_acc = ho_loss = ho_unk = ho_strict = ho_stress = 0
        for i in te_ids:
            if not topk[i]["accepted"]:
                continue
            ho_acc += 1
            ev = ho_all.get(f"{i}::{topk[i]['final_candidate_fp']}")
            if not ev or ev.get("m_ext_all_unknown", 0) > 0 or ev.get("m_ext_all_total", 0) == 0:
                ho_unk += 1
                continue
            ok_m = (ev["m_ext_all_killed"] == ev["m_ext_all_total"])
            if not ok_m:
                ho_loss += 1
            if ok_m and ev.get("w_plus_heldout_passed_strict"):
                ho_strict += 1
            if ok_m and ev.get("w_plus_heldout_passed_target_stress"):
                ho_stress += 1
        op_rows = [(s_by_id[i]["project_name"], 1.0 if q_v2[i]["strategies"]["Ours_MuGuard_Flake"]["sound_fix"] else 0.0, 1.0 if topk[i]["sound_fix_full"] else 0.0) for i in t_ids]
        topk_summary = {
            "n": len(t_ids),
            "accepted": t_acc,
            "leaked_eroded": t_er,
            "leak_pct": round(t_er / max(1, t_acc) * 100, 2),
            "sound_fix": t_sf,
            "sound_fix_pct": round(t_sf / max(1, len(t_ids)) * 100, 2),
            "heldout_n": len(te_ids),
            "heldout_acc": ho_acc,
            "heldout_unk": ho_unk,
            "heldout_mext_loss": ho_loss,
            "heldout_cond_loss_pct": round(ho_loss / max(1, ho_acc - ho_unk) * 100, 2),
            "heldout_strict_ys": ho_strict,
            "heldout_strict_ys_pct": round(ho_strict / max(1, len(te_ids)) * 100, 2),
            "heldout_stress_ys": ho_stress,
            "heldout_stress_ys_pct": round(ho_stress / max(1, len(te_ids)) * 100, 2),
            "muguard_minus_online_topk10_op": project_cluster_bootstrap_diff(op_rows),
        }

    # 3. Combine Real Historical Bug Replay (file-level + line-targeted!)
    bug_files = ["v3_real_bug_replay_results.json", "v3_line_targeted_bug_replay_results.json"]
    merged_bugs: Dict[str, Any] = {}
    for bf in bug_files:
        d = load_if(bf)
        if d:
            merged_bugs.update(d.get("evaluated") or {})
    cert_bugs = [v for v in merged_bugs.values() if v.get("certified_real_bug")]

    # 4. 100% Gap-Completed Multi-Round K=5 Cohort (203/203 across all 3 backbones) + 128/128 A2 Witness Ablation
    strats_6 = [
        "B0_Standard_Validator",
        "B2_Prompt_Guard",
        "B3_Static_AST_Guard",
        "B3_Plus_Structural_Guard",
        "B4_Scalar_Gate_Loop",
        "Ours_MuGuard_Flake",
    ]
    full_203_k5: Dict[str, Any] = {}
    for m in ("deepseek-flash", "deepseek-v4-pro", "qwen3.7-plus"):
        base_map = {r["sample_id"]: r for r in load_if(f"v2_results_{m}.json")["results"] if r.get("v2_eligible")}
        merged_m = {**base_map, **(gap_k5.get(m) or {})}
        m_ids = sorted(i for i in elig_ids if i in merged_m)
        me_ids = sorted(i for i in e_ids if i in merged_m)
        by_st: Dict[str, Any] = {}
        for sn in strats_6:
            acc = sum(1 for i in m_ids if merged_m[i]["strategies"][sn].get("accepted"))
            er = sum(1 for i in m_ids if merged_m[i]["strategies"][sn].get("eroded"))
            sf = sum(1 for i in m_ids if merged_m[i]["strategies"][sn].get("sound_fix"))
            h_acc = h_unk = h_loss = h_strict = h_stress = 0
            for i in me_ids:
                st = merged_m[i]["strategies"][sn]
                if not st.get("accepted"):
                    continue
                h_acc += 1
                ar = st.get("accepted_round")
                fp = next((rd["candidate_fp"] for rd in st["rounds"] if rd["round"] == ar), "")
                ev = ho_all.get(f"{i}::{fp}")
                if not ev or ev.get("m_ext_all_unknown", 0) > 0 or ev.get("m_ext_all_total", 0) == 0:
                    h_unk += 1
                    continue
                ok_m = (ev["m_ext_all_killed"] == ev["m_ext_all_total"])
                if not ok_m:
                    h_loss += 1
                if ok_m and ev.get("w_plus_heldout_passed_strict"):
                    h_strict += 1
                if ok_m and ev.get("w_plus_heldout_passed_target_stress"):
                    h_stress += 1
            by_st[sn] = {
                "n_completed": len(m_ids),
                "accepted": acc,
                "accepted_pct_203": round(acc / 203.0 * 100, 2),
                "leaked_eroded": er,
                "cond_leak_pct": round(er / max(1, acc) * 100, 2),
                "sound_fix": sf,
                "sound_fix_pct_203": round(sf / 203.0 * 100, 2),
                "itt_sound_fix_pct_208": round(sf / 208.0 * 100, 2),
                "heldout_acc": h_acc,
                "heldout_unk": h_unk,
                "heldout_mext_loss": h_loss,
                "heldout_cond_loss_pct": round(h_loss / max(1, h_acc - h_unk) * 100, 2),
                "heldout_strict_ys": h_strict,
                "heldout_strict_ys_pct_177": round(h_strict / 177.0 * 100, 2),
                "heldout_stress_ys": h_stress,
                "heldout_stress_ys_pct_177": round(h_stress / 177.0 * 100, 2),
            }
        # Clustered diffs: Ours minus each non-mutation baseline on completed cohort
        diffs_m: Dict[str, Any] = {}
        if len(m_ids) == 203:
            for bsn in ("B0_Standard_Validator", "B2_Prompt_Guard", "B3_Static_AST_Guard", "B3_Plus_Structural_Guard", "B4_Scalar_Gate_Loop"):
                rows = [
                    (s_by_id[i]["project_name"], 1.0 if merged_m[i]["strategies"]["Ours_MuGuard_Flake"]["sound_fix"] else 0.0, 1.0 if merged_m[i]["strategies"][bsn]["sound_fix"] else 0.0)
                    for i in elig_ids
                ]
                diffs_m[f"Ours_minus_{bsn}_op"] = project_cluster_bootstrap_diff(rows)
        full_203_k5[m] = {"n_completed": len(m_ids), "strategies": by_st, "clustered_diffs": diffs_m}

    ab_v2 = load_if("v2_forked_ablation_results.json")
    full_128_ablation = None
    if ab_v2:
        cases_merged = []
        for c in ab_v2["cases"]:
            c2 = dict(c)
            fk = f"{c['model']}::{c['sample_id']}"
            if not c2.get("A2_MisleadingLocation") and fk in gap_a2:
                c2["A2_MisleadingLocation"] = gap_a2[fk]
            cases_merged.append(c2)
        n_f = len(cases_merged)
        a1_rec = sum(1 for c in cases_merged if (c.get("A1_ScalarRatio") or {}).get("recovered"))
        a2_present = [c for c in cases_merged if c.get("A2_MisleadingLocation")]
        a2_rec = sum(1 for c in a2_present if c["A2_MisleadingLocation"].get("recovered"))
        a3_rec = sum(1 for c in cases_merged if (c.get("A3_TrueWitness") or {}).get("recovered"))
        a3_vs_a1 = project_cluster_bootstrap_diff([
            (s_by_id[c["sample_id"]]["project_name"], 1.0 if (c.get("A3_TrueWitness") or {}).get("recovered") else 0.0, 1.0 if (c.get("A1_ScalarRatio") or {}).get("recovered") else 0.0)
            for c in cases_merged
        ])
        a3_vs_a2 = project_cluster_bootstrap_diff([
            (s_by_id[c["sample_id"]]["project_name"], 1.0 if (c.get("A3_TrueWitness") or {}).get("recovered") else 0.0, 1.0 if c["A2_MisleadingLocation"].get("recovered") else 0.0)
            for c in a2_present
        ]) if a2_present else None
        a2_vs_a1 = project_cluster_bootstrap_diff([
            (s_by_id[c["sample_id"]]["project_name"], 1.0 if c["A2_MisleadingLocation"].get("recovered") else 0.0, 1.0 if (c.get("A1_ScalarRatio") or {}).get("recovered") else 0.0)
            for c in a2_present
        ]) if a2_present else None
        full_128_ablation = {
            "n_total_forks": n_f,
            "n_a2_completed": len(a2_present),
            "A1_ScalarRatio_recovered": a1_rec,
            "A1_ScalarRatio_pct": round(a1_rec / n_f * 100, 2),
            "A2_MisleadingLocation_recovered": a2_rec,
            "A2_MisleadingLocation_pct": round(a2_rec / max(1, len(a2_present)) * 100, 2),
            "A3_TrueWitness_recovered": a3_rec,
            "A3_TrueWitness_pct": round(a3_rec / n_f * 100, 2),
            "A3_minus_A1_clustered": a3_vs_a1,
            "A3_minus_A2_clustered": a3_vs_a2,
            "A2_minus_A1_clustered": a2_vs_a1,
        }

    codex_10s = load_if("v3_codex_10session_blinded_audit.json")
    codex_10s_slim = {k: v for k, v in (codex_10s or {}).items() if k != "boundary_and_disagreement_ledger"} if codex_10s else None

    master = {
        "protocol_version": "3.0.0-master-summary",
        "full_203_completed_k5": full_203_k5,
        "full_128_witness_ablation": full_128_ablation,
        "codex_10session_blinded_audit": codex_10s_slim,
        "offline_pareto_and_b1": load_if("v3_offline_pareto_and_b1_head2head.json"),
        "llm_judge_offline_and_pr": {k: v for k, v in (load_if("v3_llm_judge_offline_and_pr.json") or {}).items() if k not in ("pr_43_records", "eroded_400_records")},
        "coverage_gate_offline_and_pr": {k: v for k, v in (load_if("v3_coverage_gate_offline_and_pr.json") or {}).items() if k not in ("pr_records", "eroded_records")},
        "witness_hybrid_triage": {k: v for k, v in (load_if("v3_witness_hybrid_triage.json") or {}).items() if k not in ("pr_details", "tax_details")},
        "k1_llm_judge_screening": load_if("v3_k1_llm_judge_screening.json"),
        "k5_postgate_llm_judge": load_if("v3_k1_llm_judge_k5models.json"),
        "clustered_inference_new_baselines": clustered_new,
        "online_topk10_qwen": topk_summary,
        "real_bug_replay_summary": {
            "total_replayed_commits": len(merged_bugs),
            "certified_reproducible_bugs": len(cert_bugs),
            "certified_list": [
                {
                    "sample_id": b["sample_id"],
                    "language": b["language"],
                    "commit_sha": b["commit_sha"][:10],
                    "date": b["commit_date"],
                    "subject": b["commit_subject"],
                    "outcomes": {k: v.get("outcome") for k, v in (b.get("candidate_outcomes") or {}).items()},
                }
                for b in cert_bugs
            ],
        },
        "human_dual_rater_audit": load_if("v3_human_dual_rater_reliability.json"),
    }
    OUT_PATH.write_text(json.dumps(master, indent=2, ensure_ascii=False), encoding="utf-8")
    print("[OK] Wrote master summary:", OUT_PATH)
    print("\n=== CLUSTERED INFERENCE (NEW MULTI-ROUND BASELINES ON QWEN 3.7 PLUS) ===")
    print(json.dumps(clustered_new, indent=2))
    if topk_summary:
        print("\n=== ONLINE TOP-10 SUMMARY ===")
        print(json.dumps(topk_summary, indent=2))


if __name__ == "__main__":
    main()
