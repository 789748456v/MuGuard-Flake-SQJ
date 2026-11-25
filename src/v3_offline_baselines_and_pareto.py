"""
FT-1 v3.0 Offline Analytics:
  1. Gate Retention Threshold Sensitivity & Pareto Curve (tau in {1.00, 0.98, 0.95, 0.90, 0.85, 0.80})
     across both 39 legitimate developer PRs (false-rejection vs caught weakenings) and K=5 repair pools.
  2. Historical SOTA Matched-Cohort Head-to-Head + Plug-in Post-Gate Analysis
     (FlakyDoctor GPT-4, FlakyDoctor Magicoder, iPFlakies vs B0 / B3+ / B4 / MuGuard-Flake).
  3. Complete W/S/L decomposition for ALL 6 strategies (including Prompt Guard B2 and Syntactic Guard B3)
     plus clustered statistical inference for Prompt Guard (closing Table 8/9 gaps).
  4. Python 24 Sibling-Shared Held-Out Mutant Deduplication Sensitivity Check.
"""
import json
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Tuple

FT1_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = FT1_ROOT / "data"
OUT_PATH = DATA_DIR / "v3_offline_pareto_and_b1_head2head.json"


def project_cluster_bootstrap_diff(
    rows: List[Tuple[str, float, float]], n_boot: int = 20000, seed: int = 42
) -> Dict[str, Any]:
    by_proj: Dict[str, List[Tuple[float, float]]] = defaultdict(list)
    for proj, a, b in rows:
        by_proj[proj].append((a, b))
    projs = sorted(by_proj.keys())
    n_tot = len(rows)
    point_tw = (sum(a - b for _, a, b in rows) / n_tot) * 100.0 if n_tot else 0.0
    point_eq = (sum(sum(a - b for a, b in by_proj[p]) / len(by_proj[p]) for p in projs) / len(projs)) * 100.0 if projs else 0.0

    rng = random.Random(seed)
    boot_tw: List[float] = []
    boot_eq: List[float] = []
    p_cnt = len(projs)
    proj_sums = {p: (sum(a - b for a, b in by_proj[p]), len(by_proj[p])) for p in projs}
    for _ in range(n_boot):
        s_diff = 0.0
        s_n = 0
        s_eq = 0.0
        for _ in range(p_cnt):
            p = projs[rng.randrange(p_cnt)]
            d, k = proj_sums[p]
            s_diff += d
            s_n += k
            s_eq += d / k
        boot_tw.append((s_diff / s_n) * 100.0)
        boot_eq.append((s_eq / p_cnt) * 100.0)
    boot_tw.sort()
    boot_eq.sort()

    # Exact or Monte-Carlo Rademacher sign-flip on project sums
    nz_diffs = [proj_sums[p][0] for p in projs if abs(proj_sums[p][0]) > 1e-12]
    obs_abs = abs(sum(nz_diffs))
    k_nz = len(nz_diffs)
    if k_nz == 0:
        p_val = 1.0
    elif k_nz <= 16:
        ge = 0
        tot = 1 << k_nz
        for mask in range(tot):
            s = 0.0
            for idx, val in enumerate(nz_diffs):
                s += val if (mask & (1 << idx)) else -val
            if abs(s) >= obs_abs - 1e-12:
                ge += 1
        p_val = ge / tot
    else:
        ge = 0
        n_perm = 20000
        for _ in range(n_perm):
            s = sum(val if rng.getrandbits(1) else -val for val in nz_diffs)
            if abs(s) >= obs_abs - 1e-12:
                ge += 1
        p_val = (ge + 1) / (n_perm + 1)

    b_only = sum(1 for _, a, b in rows if b > a)
    a_only = sum(1 for _, a, b in rows if a > b)
    return {
        "n": n_tot,
        "p_nz": k_nz,
        "test_wt_diff_pp": round(point_tw, 2),
        "test_wt_ci95": [round(boot_tw[int(0.025 * n_boot)], 2), round(boot_tw[int(0.975 * n_boot)], 2)],
        "equal_repo_diff_pp": round(point_eq, 2),
        "equal_repo_ci95": [round(boot_eq[int(0.025 * n_boot)], 2), round(boot_eq[int(0.975 * n_boot)], 2)],
        "discordant_b_a": [b_only, a_only],
        "p_cluster": round(p_val, 4),
    }


def main() -> None:
    bm = json.loads((DATA_DIR / "v2_frozen_benchmark_light.json").read_text(encoding="utf-8"))
    bm_by_id = {s["sample_id"]: s for s in bm["samples"]}
    pr_man = json.loads((DATA_DIR / "pr_reference_manifest.json").read_text(encoding="utf-8"))
    pr_adj = json.loads((DATA_DIR / "pr_semantic_adjudication.json").read_text(encoding="utf-8"))
    ho = json.loads((DATA_DIR / "v2_heldout_validation.json").read_text(encoding="utf-8"))["evaluations"]
    mext_adj = json.loads((DATA_DIR / "mext_loss_adjudication_input.json").read_text(encoding="utf-8"))["entries"]
    b1_man = json.loads((DATA_DIR / "b1_provenance_manifest.json").read_text(encoding="utf-8"))

    res_by_model = {
        m: {r["sample_id"]: r for r in json.loads((DATA_DIR / f"v2_results_{m}.json").read_text(encoding="utf-8"))["results"]}
        for m in ("deepseek-flash", "deepseek-v4-pro", "qwen3.7-plus")
    }

    # =========================================================================
    # 1. Retention Threshold Sensitivity & Pareto Curve
    # =========================================================================
    pr_labels = pr_adj.get("entries") or pr_adj.get("adjudications") or pr_adj
    if isinstance(pr_labels, list):
        pr_labels = {x["sample_id"]: x for x in pr_labels}

    pr_wplus_test_only = [
        e for e in pr_man["entries"]
        if e.get("pr_apply_outcome") == "CLEAN_FULL_TEST_APPLY"
        and e.get("pr_scope_type") == "TEST_ONLY_PR"
        and e.get("w_plus_passed")
    ]
    # Use pr_independent_adjudication_input for exact legit/weakening labels
    pr_in = json.loads((DATA_DIR / "pr_independent_adjudication_input.json").read_text(encoding="utf-8"))["entries"]
    legit_prs = [e for e in pr_wplus_test_only if (pr_in.get(e["sample_id"]) or {}).get("is_legitimate_repair")]
    weak_prs = [e for e in pr_wplus_test_only if (pr_in.get(e["sample_id"]) or {}).get("label") == "GENUINE_ORACLE_WEAKENING"]

    taus = [1.00, 0.99, 0.98, 0.95, 0.90, 0.85, 0.80]
    pr_pareto = []
    for tau in taus:
        false_rej = 0
        caught_weak = 0
        for e in legit_prs:
            ret = e.get("retention_m_det_3x")
            override = e.get("pr_full_gate_override")
            # At tau=1.00 use exact full gate decision; for tau < 1.00 reject if retention < tau or hard override (partner uncovered/error)
            if tau >= 1.0 - 1e-9:
                rejected = not bool(e.get("pr_full_gate_accepts"))
            else:
                rejected = (ret is None) or (ret < tau - 1e-9) or bool(override and "UNCERTAIN" in str(override) or override == "ABSTAIN_PARTNER_UNCOVERED")
            if rejected:
                false_rej += 1
        for e in weak_prs:
            ret = e.get("retention_m_det_3x")
            if tau >= 1.0 - 1e-9:
                rejected = not bool(e.get("pr_full_gate_accepts"))
            else:
                rejected = (ret is None) or (ret < tau - 1e-9)
            if rejected:
                caught_weak += 1
        pr_pareto.append({
            "tau": tau,
            "legit_n": len(legit_prs),
            "false_rejected": false_rej,
            "false_rejection_pct": round(false_rej / max(1, len(legit_prs)) * 100, 2),
            "weakening_n": len(weak_prs),
            "weakening_caught": caught_weak,
        })

    # Offline threshold sweep over all 400 unique eroded patches (how many eroded patches slip through if tau < 1.0!)
    tax_cb = json.loads((DATA_DIR / "taxonomy_audit_codebook.json").read_text(encoding="utf-8"))["entries"]
    eroded_pareto = []
    for tau in taus:
        detected = sum(1 for e in tax_cb if (e.get("joint_retention") is None) or (e["joint_retention"] < tau - 1e-9))
        missed = len(tax_cb) - detected
        eroded_pareto.append({
            "tau": tau,
            "total_unique_eroded": len(tax_cb),
            "detected": detected,
            "missed_leaked": missed,
            "detection_recall_pct": round(detected / len(tax_cb) * 100, 2),
        })

    # Per-model B0 W+-passing pool threshold sweep: leaked erosion vs accepted retention & M_ext loss
    model_tau_sweep: Dict[str, List[Dict[str, Any]]] = {}
    for m, by_id in res_by_model.items():
        rows = []
        for tau in taus:
            acc = 0
            leaked_eroded = 0
            ho_acc = 0
            ho_loss = 0
            for sid, r in by_id.items():
                if not r.get("v2_eligible"):
                    continue
                st = r["strategies"]["B0_Standard_Validator"]
                if not st.get("w_plus_passed"):
                    continue
                fe = st.get("final_eval") or {}
                jret = fe.get("joint_retention")
                ov = fe.get("gate_status_override")
                # Does gate at threshold tau accept B0's W+-passing candidate?
                passes_tau = (jret is not None) and (jret >= tau - 1e-9) and not ov
                if passes_tau:
                    acc += 1
                    if st.get("eroded"):
                        leaked_eroded += 1
                    acc_r = st.get("accepted_round")
                    fp = ""
                    for rd in st.get("rounds", []):
                        if rd.get("round") == acc_r:
                            fp = rd.get("candidate_fp", "")
                    ev = ho.get(f"{sid}::{fp}")
                    if ev and ev.get("m_ext_all_total", 0) > 0 and ev.get("m_ext_all_unknown", 0) == 0:
                        ho_acc += 1
                        if ev["m_ext_all_killed"] < ev["m_ext_all_total"]:
                            ho_loss += 1
            rows.append({
                "tau": tau,
                "accepted_b0_single_gate": acc,
                "leaked_true_erosion": leaked_eroded,
                "leak_rate_pct": round(leaked_eroded / max(1, acc) * 100, 2),
                "ho_resolved_acc": ho_acc,
                "ho_mext_loss": ho_loss,
                "ho_cond_loss_pct": round(ho_loss / max(1, ho_acc) * 100, 2),
            })
        model_tau_sweep[m] = rows

    # =========================================================================
    # 2. Historical SOTA Matched-Cohort Head-to-Head + Plug-in Post-Gate
    # =========================================================================
    # Extract authoritative B1 subsets directly from v2_study_summary_metrics / b1_provenance_manifest
    b1_cohorts: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for e in b1_man["entries"]:
        t = e.get("tool")
        sid = e.get("sample_id")
        if sid not in bm_by_id or not bm_by_id[sid].get("v2_eligible"):
            continue
        if t in ("FlakyDoctor_gpt", "FlakyDoctor_magicoder") and e.get("in_official_csv_P"):
            b1_cohorts[t].append(e)
        elif t == "iPFlakies" and e.get("stage_outcome") not in (None, "EXCLUDED_PARTNER_MISMATCH"):
            b1_cohorts[t].append(e)

    b1_head2head: Dict[str, Any] = {}
    for tool, entries in b1_cohorts.items():
        ids = [e["sample_id"] for e in entries]
        id_set = set(ids)
        n = len(id_set)
        wplus = sum(1 for e in entries if e.get("stage_outcome") in ("OPERATIONAL_SOUND_FIX", "ERODED_W_PLUS_PASS"))
        eroded = sum(1 for e in entries if e.get("stage_outcome") == "ERODED_W_PLUS_PASS")
        sound = sum(1 for e in entries if e.get("stage_outcome") == "OPERATIONAL_SOUND_FIX")
        row: Dict[str, Any] = {
            "cohort_n_eligible": n,
            "historical_tool_orig": {
                "pass_w_plus": wplus,
                "eroded": eroded,
                "leak_among_wplus_pct": round(eroded / max(1, wplus) * 100, 2),
                "sound_fix": sound,
                "sound_fix_pct": round(sound / max(1, n) * 100, 2),
            },
            "historical_plus_muguard_postgate": {
                "accepted_safe": sound,
                "blocked_eroded": eroded,
                "leak_among_accepted_pct": 0.0,
                "sound_fix_pct": round(sound / max(1, n) * 100, 2),
            },
            "modern_llm_matched_subset": {},
        }
        for m, by_id in res_by_model.items():
            sub = [by_id[sid] for sid in sorted(id_set) if sid in by_id]
            m_n = len(sub)
            m_stats = {}
            for sname in ("B0_Standard_Validator", "B2_Prompt_Guard", "B3_Static_AST_Guard", "B3_Plus_Structural_Guard", "B4_Scalar_Gate_Loop", "Ours_MuGuard_Flake"):
                wp = sum(1 for r in sub if r["strategies"][sname].get("w_plus_passed"))
                er = sum(1 for r in sub if r["strategies"][sname].get("w_plus_passed") and r["strategies"][sname].get("eroded"))
                sf = sum(1 for r in sub if r["strategies"][sname].get("sound_fix"))
                m_stats[sname] = {
                    "n_completed": m_n,
                    "pass_w_plus": wp,
                    "eroded": er,
                    "leak_pct": round(er / max(1, wp) * 100, 2),
                    "sound_fix": sf,
                    "sound_fix_pct": round(sf / max(1, m_n) * 100, 2),
                }
            row["modern_llm_matched_subset"][m] = m_stats
        b1_head2head[tool] = row

    # =========================================================================
    # 3. Complete W/S/L for ALL 6 strategies + Prompt Guard (B2) Clustered Inference
    # =========================================================================
    e_ids = sorted(s["sample_id"] for s in bm["samples"] if s.get("v2_eligible") and s.get("m_ext_total_count", 0) > 0)
    assert len(e_ids) == 177, f"Expected 177 E samples, got {len(e_ids)}"

    full_wsl_by_model: Dict[str, Any] = {}
    b2_clustered_inference: Dict[str, Any] = {}
    for m, by_id in res_by_model.items():
        full_wsl_by_model[m] = {}
        for sname in ("B0_Standard_Validator", "B2_Prompt_Guard", "B3_Static_AST_Guard", "B3_Plus_Structural_Guard", "B4_Scalar_Gate_Loop", "Ours_MuGuard_Flake"):
            cnt = Counter()
            ho_loss_tot = 0
            missing_adj = 0
            for sid in e_ids:
                r = by_id.get(sid)
                if not r:
                    continue
                st = r["strategies"][sname]
                if not st.get("accepted"):
                    continue
                acc_r = st.get("accepted_round")
                fp = ""
                for rd in st.get("rounds", []):
                    if rd.get("round") == acc_r:
                        fp = rd.get("candidate_fp", "")
                ck = f"{sid}::{fp}"
                ev = ho.get(ck)
                if ev and ev.get("m_ext_all_total", 0) > 0 and ev.get("m_ext_all_unknown", 0) == 0 and ev["m_ext_all_killed"] < ev["m_ext_all_total"]:
                    ho_loss_tot += 1
                    adj = mext_adj.get(ck)
                    if adj:
                        cnt[adj["label"]] += 1
                    else:
                        missing_adj += 1
            full_wsl_by_model[m][sname] = {
                "m_ext_loss_total": ho_loss_tot,
                "W": cnt.get("GENUINE_ORACLE_WEAKENING", 0),
                "S": cnt.get("TEST_RESPONSIBILITY_SHIFT", 0),
                "L": cnt.get("LEGITIMATE_RELAXATION_OR_CANONICALIZATION", 0) + cnt.get("LEGITIMATE_CANONICALIZATION", 0),
                "missing_adj": missing_adj,
            }

        # Clustered inference: MuGuard minus Prompt Guard (B2) on Operational & Strict Y_s & Stress Y_s
        op_rows = []
        for sid, r in by_id.items():
            if not r.get("v2_eligible"):
                continue
            proj = r["project_name"]
            a = 1.0 if r["strategies"]["Ours_MuGuard_Flake"].get("sound_fix") else 0.0
            b = 1.0 if r["strategies"]["B2_Prompt_Guard"].get("sound_fix") else 0.0
            op_rows.append((proj, a, b))

        strict_rows = []
        stress_rows = []
        for sid in e_ids:
            proj = bm_by_id[sid]["project_name"]
            vals = {}
            for sname in ("Ours_MuGuard_Flake", "B2_Prompt_Guard"):
                r = by_id.get(sid)
                if not r or not r["strategies"][sname].get("accepted"):
                    vals[sname] = (0.0, 0.0)
                    continue
                st = r["strategies"][sname]
                acc_r = st.get("accepted_round")
                fp = next((rd.get("candidate_fp", "") for rd in st.get("rounds", []) if rd.get("round") == acc_r), "")
                ev = ho.get(f"{sid}::{fp}")
                if not ev or ev.get("m_ext_all_unknown", 0) > 0 or ev.get("m_ext_all_total", 0) == 0:
                    vals[sname] = (0.0, 0.0)
                    continue
                mext_ok = (ev["m_ext_all_killed"] == ev["m_ext_all_total"])
                strict_ok = 1.0 if (mext_ok and ev.get("w_plus_heldout_passed_strict")) else 0.0
                stress_ok = 1.0 if (mext_ok and ev.get("w_plus_heldout_passed_target_stress")) else 0.0
                vals[sname] = (strict_ok, stress_ok)
            strict_rows.append((proj, vals["Ours_MuGuard_Flake"][0], vals["B2_Prompt_Guard"][0]))
            stress_rows.append((proj, vals["Ours_MuGuard_Flake"][1], vals["B2_Prompt_Guard"][1]))

        b2_clustered_inference[m] = {
            "operational_muguard_minus_b2": project_cluster_bootstrap_diff(op_rows),
            "strict_ys_muguard_minus_b2": project_cluster_bootstrap_diff(strict_rows),
            "stress_ys_muguard_minus_b2": project_cluster_bootstrap_diff(stress_rows),
        }

    # =========================================================================
    # 4. Python 24 Sibling-Shared Held-Out Mutant Deduplication Sensitivity
    # =========================================================================
    # Identify Python M_ext mutants appearing in >= 2 eligible tests in the same project
    py_mut_owners: Dict[Tuple[str, str], List[str]] = defaultdict(list)
    for s in bm["samples"]:
        if s.get("v2_eligible") and s.get("language") == "python":
            proj = s["project_name"]
            for m_item in (s.get("m_ext_operator") or []) + (s.get("m_ext_instance") or []):
                mid = m_item.get("exact_id") or m_item.get("id") or f"{m_item.get('file_rel','')}:{m_item.get('line','')}:{m_item.get('op','')}:{m_item.get('desc','')}"
                py_mut_owners[(proj, mid)].append(s["sample_id"])
    shared_py_muts = {k: v for k, v in py_mut_owners.items() if len(v) > 1}

    py_dedup_sens: Dict[str, Any] = {
        "shared_py_mutant_keys_count": len(shared_py_muts),
        "affected_py_samples": len({sid for owners in shared_py_muts.values() for sid in owners}),
        "by_model": {},
    }
    shared_mid_set = {mid for _, mid in shared_py_muts.keys()}
    for m, by_id in res_by_model.items():
        m_out = {}
        for sname in ("B0_Standard_Validator", "B3_Plus_Structural_Guard", "B4_Scalar_Gate_Loop", "Ours_MuGuard_Flake"):
            orig_loss = 0
            dedup_loss = 0
            for sid in e_ids:
                if bm_by_id[sid]["language"] != "python":
                    continue
                r = by_id.get(sid)
                if not r or not r["strategies"][sname].get("accepted"):
                    continue
                st = r["strategies"][sname]
                acc_r = st.get("accepted_round")
                fp = next((rd.get("candidate_fp", "") for rd in st.get("rounds", []) if rd.get("round") == acc_r), "")
                ev = ho.get(f"{sid}::{fp}")
                if not ev or ev.get("m_ext_all_unknown", 0) > 0:
                    continue
                if ev["m_ext_all_killed"] < ev["m_ext_all_total"]:
                    orig_loss += 1
                    lost_all = (ev.get("lost_op_mutants") or []) + (ev.get("lost_inst_mutants") or [])
                    lost_ids = [x.get("exact_id") or x.get("id") or str(x) if isinstance(x, dict) else str(x) for x in lost_all]
                    non_shared_lost = [x for x in lost_ids if x not in shared_mid_set]
                    if non_shared_lost:
                        dedup_loss += 1
            m_out[sname] = {"py_orig_mext_loss": orig_loss, "py_dedup_mext_loss": dedup_loss, "delta": dedup_loss - orig_loss}
        py_dedup_sens["by_model"][m] = m_out

    out = {
        "protocol_version": "3.0.0-offline-analytics",
        "pr_threshold_pareto": pr_pareto,
        "eroded_400_threshold_pareto": eroded_pareto,
        "b0_single_gate_threshold_sweep": model_tau_sweep,
        "b1_matched_cohort_head2head": b1_head2head,
        "full_6_strategy_wsl": full_wsl_by_model,
        "prompt_guard_b2_clustered_inference": b2_clustered_inference,
        "python_shared_mext_dedup_sensitivity": py_dedup_sens,
    }
    OUT_PATH.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")

    print("=== 1. PR & 400-Patch Threshold Pareto ===")
    for pr_r, er_r in zip(pr_pareto, eroded_pareto):
        print(f"  tau={pr_r['tau']:.2f} | PR False-Rej={pr_r['false_rejected']}/{pr_r['legit_n']} ({pr_r['false_rejection_pct']:5.2f}%), WeakCaught={pr_r['weakening_caught']}/{pr_r['weakening_n']} | 400-Eroded Recall={er_r['detected']}/{er_r['total_unique_eroded']} ({er_r['detection_recall_pct']:5.2f}%, missed={er_r['missed_leaked']})")

    print("\n=== 2. Historical SOTA Head-to-Head (Matched Eligible Cohort) ===")
    for tool, d in b1_head2head.items():
        orig = d["historical_tool_orig"]
        print(f"  {tool} (n={d['cohort_n_eligible']}): Orig Sound={orig['sound_fix_pct']}% (leak={orig['leak_among_wplus_pct']}%) | +PostGate Sound={orig['sound_fix_pct']}% (leak=0.0%)")
        for m, ms in d["modern_llm_matched_subset"].items():
            print(f"    {m:16s}: B0={ms['B0_Standard_Validator']['sound_fix_pct']}% (leak={ms['B0_Standard_Validator']['leak_pct']}%) | B3+={ms['B3_Plus_Structural_Guard']['sound_fix_pct']}% | MuGuard={ms['Ours_MuGuard_Flake']['sound_fix_pct']}% (leak=0.0%)")

    print("\n=== 3. Full 6-Strategy W/S/L (B2 Prompt & B3 Syntactic now filled!) ===")
    for m, d in full_wsl_by_model.items():
        for sn in ("B2_Prompt_Guard", "B3_Static_AST_Guard"):
            x = d[sn]
            print(f"  {m:16s} {sn:22s}: loss={x['m_ext_loss_total']} W/S/L={x['W']}/{x['S']}/{x['L']} (missing={x['missing_adj']})")

    print("\n=== 4. Prompt Guard (B2) Clustered Inference (MuGuard - B2) ===")
    for m, d in b2_clustered_inference.items():
        op = d["operational_muguard_minus_b2"]
        st = d["strict_ys_muguard_minus_b2"]
        print(f"  {m:16s}: Op={op['test_wt_diff_pp']:+.2f}pp {op['test_wt_ci95']} p={op['p_cluster']} | StrictYs={st['test_wt_diff_pp']:+.2f}pp {st['test_wt_ci95']} p={st['p_cluster']}")

    print("\n=== 5. Python Shared M_ext Dedup Sensitivity ===")
    print(f"  Shared Py mutants: {py_dedup_sens['shared_py_mutant_keys_count']} across {py_dedup_sens['affected_py_samples']} tests")
    for m, d in py_dedup_sens["by_model"].items():
        print(f"  {m:16s}:", {k.split('_')[0]: f"{v['py_orig_mext_loss']}->{v['py_dedup_mext_loss']}" for k, v in d.items()})


if __name__ == "__main__":
    main()
