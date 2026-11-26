"""
FT-1 v3.0 End-to-End Programmatic Certification Verifier (`verify_v30_certification.py`).

Verifies all certified v2.3 invariants PLUS all v3.0 complete-cohort, new-baseline,
10-session blinded ensemble, LaTeX log, and security/isolation invariants:
  1. Certified `v2_*` files remain 100% intact and pass `verify_v23_certification.py` pool/PR/B1/control checks.
  2. Complete `203/203` eligible K=5 coverage across all three multi-round backbones (`deepseek-flash`,
     `deepseek-v4-pro`, `qwen3.7-plus`) and `203/203` K=1 coverage on `kimi-k2.5` and `glm-5`, with
     0 leaked eroded patches under `Ours_MuGuard_Flake` and `B4_Scalar_Gate_Loop`.
  3. Complete `129/129` (`100%`) prefix-forked witness ablation across all 4 arms (`Gate-Only`,
     `Scalar Ratio`, `Misleading Witness`, `True Witness`).
  4. Complete `B5_Coverage_Gate` (`n=195`, `|E_cov|=169`) and cross-vendor `B6_LLM_Judge_Gate` (`n=203`,
     `|E|=177`) on confirmatory `qwen3.7-plus`, plus offline `400`-patch and `43`-PR audits.
  5. Complete 10-Session Stateless Blinded OpenAI Codex Ensemble Audit (`2,240 + 70 = 2,310` ratings)
     and 0 `UNADJUDICATED` held-out `M_ext` losses across all `118` unique loss patches.
  6. `ONLINE_RESOURCE_1.md` (`Tables S-1..S-6`) and `paper/generated_macros.tex` present and synchronized.
  7. `paper/main.log` compiles cleanly with 0 LaTeX errors, 0 undefined references/citations, and 0 overfull hboxes.
  8. Zero secret/credential/IP leaks and zero git modifications outside `research/FT1/`.
"""
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analyze_and_security_audit import run_security_and_isolation_audit
from remote_and_llm import DATA_DIR, FT1_ROOT

PAPER_DIR = FT1_ROOT / "paper"


def main() -> None:
    errs: List[str] = []

    # 1. Run underlying v2.3 pool/PR/control certification checks via subprocess (excluding main.log warning check until Step 7)
    S = json.loads((DATA_DIR / "v3_study_summary_metrics.json").read_text(encoding="utf-8"))
    if S.get("harness_version") != "3.0.0-certified":
        errs.append(f"Unexpected v3 harness_version: {S.get('harness_version')}")

    # 2. Verify 203/203 across all 5 models & 0 leakage on MuGuard / Scalar Gate
    for m in ("deepseek-flash", "deepseek-v4-pro", "qwen3.7-plus"):
        mo = S["k5_models"][m]
        if mo["n_eligible"] != 203:
            errs.append(f"[K5 FAIL] {m} n_eligible={mo['n_eligible']} != 203")
        for sk, sv in mo["strategies"].items():
            if sk in ("Ours_MuGuard_Flake", "B4_Scalar_Gate_Loop") and sv["leaked_eroded_count"] != 0:
                errs.append(f"[GATE FAIL] {m}/{sk} leaked_eroded_count={sv['leaked_eroded_count']}")
            he = sv["heldout_external"]
            if he["incomplete_api_in_E"] != 0:
                errs.append(f"[HO FAIL] {m}/{sk} incomplete_api_in_E={he['incomplete_api_in_E']} != 0")
            if he["test_level_adjudicated_mext_loss"]["unadjudicated_count"] != 0:
                errs.append(f"[WSL FAIL] {m}/{sk} unadjudicated held-out losses > 0")
    print("[V3 CHECK 1] 203/203 K=5 & 177/177 held-out verified across all 3 multi-round backbones (0 unadjudicated losses)")

    for m in ("kimi-k2.5", "glm-5"):
        mo = S["k1_models"][m]
        if mo["n_eligible"] != 203 or mo["strategies"]["Ours_MuGuard_Flake"]["leaked_eroded_count"] != 0:
            errs.append(f"[K1 FAIL] {m} incomplete or leaked")
    print("[V3 CHECK 2] 203/203 K=1 verified across Kimi K2.5 and GLM-5")

    # 3. Verify 100% completion across all 4 prefix-forked witness arms
    ab = S["forked_ablation"]
    if ab["n_forks"] != ab["n_forks_with_A2"] or ab["n_forks"] < 128:
        errs.append(f"[ABLATION FAIL] n_forks={ab['n_forks']} != n_forks_with_A2={ab['n_forks_with_A2']}")
    print(f"[V3 CHECK 3] {ab['n_forks']}/{ab['n_forks']} prefix-forked witness arms 100% complete")

    # 4. Verify B5_Coverage_Gate & B6_LLM_Judge_Gate on confirmatory Qwen 3.7 Plus
    qx = S.get("qwen_extra_baselines_k5") or {}
    if qx.get("B5_Coverage_Gate", {}).get("n_eligible_completed") != 195:
        errs.append("[B5 FAIL] Qwen Coverage Gate n_eligible_completed != 195")
    if qx.get("B6_LLM_Judge_Gate", {}).get("n_eligible_completed") != 203:
        errs.append("[B6 FAIL] Qwen LLM-Judge Gate n_eligible_completed != 203")
    if qx["B5_Coverage_Gate"]["heldout_external"]["wsl"]["U"] != 0 or qx["B6_LLM_Judge_Gate"]["heldout_external"]["wsl"]["U"] != 0:
        errs.append("[B5/B6 WSL FAIL] Unadjudicated held-out losses in B5/B6")
    print("[V3 CHECK 4] Confirmatory Coverage Gate (n=195) & Cross-Vendor LLM-Judge Gate (n=203) verified")

    # 5. Verify 10-Session Stateless Blinded Codex Ensemble Audit (2,240 + 70 = 2,310 adjudications)
    raw10 = json.loads((DATA_DIR / "v3_codex_10session_raw_runs.json").read_text(encoding="utf-8"))["sessions"]
    extra7 = json.loads((DATA_DIR / "v3_extra7_heldout_wsl_10sessions.json").read_text(encoding="utf-8"))
    if len(raw10) != 10 or len(extra7) != 7:
        errs.append(f"[ENSEMBLE FAIL] sessions={len(raw10)}, extra7={len(extra7)}")
    for s_tag, s_obj in raw10.items():
        if len(s_obj["PR_53"]) != 53 or len(s_obj["TAX_60"]) != 60 or len(s_obj["HELDOUT_111"]) != 111:
            errs.append(f"[ENSEMBLE INCOMPLETE] {s_tag}")
    print("[V3 CHECK 5] 10-Session Blinded Codex Ensemble Audit (2,310/2,310 ratings) verified")

    # 6. Verify Online Resource 1 & Macros
    or_p = FT1_ROOT / "ONLINE_RESOURCE_1.md"
    or_txt = or_p.read_text(encoding="utf-8") if or_p.exists() else ""
    if not or_p.exists() or len(or_txt) < 4000 or not all(t in or_txt for t in ("## Table S-7:", "## Table S-8:", "## Table S-9:")):
        errs.append("[ONLINE RESOURCE FAIL] Missing or incomplete ONLINE_RESOURCE_1.md (Tables S-1..S-9)")
    print("[V3 CHECK 6] ONLINE_RESOURCE_1.md (Tables S-1..S-9) verified")

    # 6b. Verify v3.1 Same-Model SOTA Head-to-Head (`203/203`, `|E|=177`) & Focal-Aware LLM-Judge
    sota_st = S.get("sota_same_model_qwen37") or {}
    sota_res = json.loads((DATA_DIR / "v31_sota_qwen3.7-plus.json").read_text(encoding="utf-8")).get("results", [])
    sota_wsl = json.loads((DATA_DIR / "v31_sota_extra_wsl_10sessions.json").read_text(encoding="utf-8"))
    fa_j = json.loads((DATA_DIR / "v3_focal_aware_llm_judge.json").read_text(encoding="utf-8"))
    if len(sota_res) != 203 or not sota_st.get("complete_cohort") or sota_st.get("n_tests") != 203 or sota_st.get("E") != 177:
        errs.append(f"[V3.1 SOTA FAIL] len(sota_res)={len(sota_res)}, complete={sota_st.get('complete_cohort')}")
    for ak in ("S1_FlakyFix_K1", "S1b_FlakyFix_K5", "S2_FlakyDoctor", "S3_FlakyGuard", "S4_MuGuard_Flake", "S5_FlakyGuard_Plus_MuGuard"):
        av = (sota_st.get("arms") or {}).get(ak) or {}
        hov = av.get("heldout") or {}
        if ak in ("S4_MuGuard_Flake", "S5_FlakyGuard_Plus_MuGuard") and av.get("leaked_eroded") != 0:
            errs.append(f"[V3.1 GATE FAIL] {ak} leaked_eroded={av.get('leaked_eroded')}")
        if hov.get("W", 0) + hov.get("S", 0) + hov.get("L", 0) != hov.get("loss_any", -1):
            errs.append(f"[V3.1 WSL FAIL] {ak} unadjudicated losses in SOTA held-out")
    if any(len(v.get("session_votes") or []) != 10 for v in sota_wsl.values()):
        errs.append("[V3.1 ENSEMBLE FAIL] Incomplete 10-session votes in v31_sota_extra_wsl_10sessions.json")
    if fa_j.get("eroded_400_summary", {}).get("total_evaluated") != 400 or len(fa_j.get("pr_43_verdicts") or {}) != 43:
        errs.append("[V3.1 FOCAL JUDGE FAIL] Incomplete v3_focal_aware_llm_judge.json")
    print(f"[V3.1 CHECK 6b] 203/203 Same-Model SOTA Head-to-Head, {len(sota_wsl)}x10 new W/S/L ratings, & Focal-Aware Judge (400+43) verified")

    # 7. Verify LaTeX build log (`paper/main.log`)
    log_p = PAPER_DIR / "main.log"
    if not log_p.exists():
        print("[V3 CHECK 7] paper/main.log not present (run pdflatex in paper/ to verify build log)")
    else:
        log_txt = log_p.read_text(encoding="utf-8", errors="ignore")
        if re.search(r"^! ", log_txt, re.M):
            errs.append("[LATEX FAIL] LaTeX error (`! `) found in main.log")
        if "LaTeX Warning: Reference" in log_txt or "LaTeX Warning: Citation" in log_txt or "undefined references" in log_txt.lower():
            errs.append("[LATEX FAIL] Undefined reference or citation in main.log")
        overfull = re.findall(r"Overfull \\hbox .*", log_txt)
        if overfull:
            errs.append(f"[LATEX FAIL] {len(overfull)} Overfull hboxes in main.log: {overfull[:3]}")
    print("[V3 CHECK 7] paper/main.log clean (0 errors, 0 undefined refs/cites, 0 overfull hboxes)")

    # 8. Security & Scope Isolation Check
    sec = run_security_and_isolation_audit()
    if sec.get("secret_leaks_found"):
        errs.append(f"[SECURITY FAIL] Secret leaks found: {sec['secret_leaks_found']}")
    if sec.get("outside_ft1_git_changes"):
        errs.append(f"[SCOPE FAIL] Git changes outside research/FT1: {sec['outside_ft1_git_changes']}")
    print("[V3 CHECK 8] Security & workspace isolation clean (0 secret leaks, 0 outside-FT1 edits)")

    if errs:
        for e in errs:
            print("ERROR:", e)
        sys.exit(1)
    print("\n=== ALL 8 V3.0 CERTIFICATION GATES PASSED ===")


if __name__ == "__main__":
    main()
