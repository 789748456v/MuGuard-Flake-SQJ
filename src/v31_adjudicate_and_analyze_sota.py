"""
Phase D (v3.1, Codex round-2 consensus): blinded W/S/L adjudication + primary-endpoint inference for the
same-model SOTA head-to-head (`v31_adjudicate_and_analyze_sota.py`).

Locked definitions (see PREREG_v31_SOTA_HEAD2HEAD.md, "interim-informed analysis lock"):
  * Primary population (per arm): accepted patches in the held-out cohort |E|=177 that PASS the strict held-out
    W+ schedule AND have fully evaluable M_ext (runner ok, eval ok, zero UNKNOWN mutants).
  * Primary endpoint: conditional harmful held-out loss = (# of primary-population patches with M_ext retention
    < 1.0 whose 10-session majority label is GENUINE_ORACLE_WEAKENING or TEST_RESPONSIBILITY_SHIFT) / (primary
    population size). Zero denominators are undefined.
  * Reported separately: held-out W+ failures, mutation UNKNOWN, unresolved adjudication.
  * Inference: paired repository cluster bootstrap (20,000 resamples) recomputing each arm's numerator and
    denominator; two-sided null-centred studentized bootstrap test of the ratio difference (cluster-linearized SE
    per replicate); Holm-Bonferroni within each declared family (S4 vs {S1,S1b,S2,S3}; S5 vs {S1,S1b,S2,S3}), m=4.
  * Secondary (descriptive / paired binary): SoundFix, Strict Y_s, Full Y_s (Strict Y_s AND SoundFix).
Labels: legacy losses reuse the 10-session ensemble MAJORITY (joined through v3_human_audit_key.json cand_key);
genuinely new losses are adjudicated by 10 stateless, order-permuted, pinned-model Codex sessions (model
adjudication, not human ground truth) from dossiers built from the COMPLETE lost-mutant lists and full diffs.
"""
import argparse
import base64
import difflib
import json
import math
import random
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import v2_statistical_and_audit_engine as v2e
from remote_and_llm import DATA_DIR
from run_full_study_matrix import candidate_fingerprint, log
from v3_codex_10session_blinded_audit import DECISION_TREE_RUBRIC, chunk_list, majority_vote, make_schema
from v31_run_sota_head_to_head import ALL_SOTA_ARMS, SOTA_HO_PATH, SOTA_OUT_PATH
from v31_sota_integrity import get_quarantined_projects, load_eligible_heldout_map

EXTRA_WSL_OUT = DATA_DIR / "v31_sota_extra_wsl_10sessions.json"
STAT_OUT = DATA_DIR / "v31_sota_statistical_summary.json"
ADJUDICATOR_MODEL = "gpt-6-astra"
WSL_TIE_ORDER = ["GENUINE_ORACLE_WEAKENING", "TEST_RESPONSIBILITY_SHIFT", "LEGITIMATE_RELAXATION_OR_CANONICALIZATION"]
HARMFUL = {"GENUINE_ORACLE_WEAKENING", "TEST_RESPONSIBILITY_SHIFT"}
BASELINES = ["S1_FlakyFix_K1", "S1b_FlakyFix_K5", "S2_FlakyDoctor", "S3_FlakyGuard"]
SAVE_LOCK = threading.Lock()


def save_atomic(path: Path, obj: Any) -> None:
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


# =============================================================================
# 1. Held-out schema adapter (actual remote_eval_v2.py post_freeze_heldout output)
# =============================================================================
def lost_lists(hev: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for k in ("lost_value_flow_mutants", "lost_call_mutants", "lost_op_mutants", "lost_inst_mutants"):
        out.extend(hev.get(k) or [])
    return out


def heldout_status(hev: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Classify one held-out evaluation. `evaluable` requires runner ok, eval ok, and zero UNKNOWN mutants."""
    if not hev:
        return {"state": "MISSING"}
    w_strict = bool(hev.get("w_plus_heldout_passed_strict", hev.get("w_plus_heldout_passed")))
    w_stress = bool(hev.get("w_plus_heldout_passed_target_stress", w_strict))
    evaluable = bool(hev.get("m_ext_runner_ok")) and bool(hev.get("m_ext_eval_ok")) and int(hev.get("m_ext_all_unknown") or 0) == 0
    ret = hev.get("m_ext_all_retention")
    total = int(hev.get("m_ext_all_total") or 0)
    killed = int(hev.get("m_ext_all_killed") or 0)
    lost = lost_lists(hev)
    lost_complete = (len(lost) == max(0, total - killed)) if evaluable else None
    return {
        "state": "EVALUABLE" if (evaluable and ret is not None) else "UNKNOWN",
        "w_strict": w_strict,
        "w_stress": w_stress,
        "retention": float(ret) if ret is not None else None,
        "loss": bool(evaluable and ret is not None and float(ret) < 1.0),
        "lost_list_complete": lost_complete,
        "total": total,
        "killed": killed,
    }


# =============================================================================
# 2. Labels: legacy 10-session majorities + extra7 + new SOTA adjudications
# =============================================================================
def load_majority_labels() -> Dict[str, Dict[str, Any]]:
    key = json.loads((DATA_DIR / "v3_human_audit_key.json").read_text(encoding="utf-8"))["ho_all"]
    raw = json.loads((DATA_DIR / "v3_codex_10session_raw_runs.json").read_text(encoding="utf-8"))["sessions"]
    labels: Dict[str, Dict[str, Any]] = {}
    for bid, meta in key.items():
        votes = [raw[s]["HELDOUT_111"][bid]["label_wsl"] for s in sorted(raw) if bid in raw[s].get("HELDOUT_111", {})]
        if len(votes) != 10:
            continue
        maj, cnt, ent = majority_vote(votes, WSL_TIE_ORDER)
        labels[meta["cand_key"]] = {"label": maj, "votes": cnt, "entropy": ent, "source": "v3_ensemble_majority", "stage1": meta.get("stage1_label")}
    p7 = DATA_DIR / "v3_extra7_heldout_wsl_10sessions.json"
    if p7.exists():
        for ck, v in json.loads(p7.read_text(encoding="utf-8")).items():
            labels[ck] = {"label": v["majority_label"], "votes": v.get("majority_count_of_10"), "source": "v3_extra7_majority"}
    if EXTRA_WSL_OUT.exists():
        for ck, v in json.loads(EXTRA_WSL_OUT.read_text(encoding="utf-8")).items():
            if len(v.get("session_votes") or []) == 10:
                labels[ck] = {"label": v["majority_label"], "votes": v["majority_count_of_10"], "source": "v31_sota_majority"}
    return labels


def mext_pool_by_id(sample: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    pool: Dict[str, Dict[str, Any]] = {}
    for k in ("m_ext_op_value_flow", "m_ext_op_call", "m_ext_operator", "m_ext_instance"):
        for m in sample.get(k) or []:
            pool[m["id"]] = m
    return pool


def full_unified(a: str, b: str, label: str) -> str:
    return "\n".join(difflib.unified_diff((a or "").splitlines(), (b or "").splitlines(),
                                          fromfile=f"orig/{label}", tofile=f"patched/{label}", lineterm="", n=3))


def build_blinded_dossier(bid: str, sample: Dict[str, Any], cand: Dict[str, Any], hev: Dict[str, Any]) -> str:
    lang = sample["language"]
    ext = "java" if lang == "java" else "py"
    cand_t = cand.get("test_code") or cand.get("victim_code") or ""
    parts = [
        f"## {bid}",
        f"- **Language / Test Type**: `{lang}` / `{sample.get('od_subtype') or sample['test_type']}`",
        f"- **Target Test**: `{sample['test_full_name']}`",
        f"- **Held-Out Retention**: `{hev.get('m_ext_all_killed')}/{hev.get('m_ext_all_total')}`",
        f"**Target Test Unified Diff**:\n```diff\n{full_unified(sample.get('orig_test_code', ''), cand_t, f'target_test.{ext}') or '(no target diff)'}\n```",
    ]
    cand_p = cand.get("polluter_code") or ""
    if sample.get("orig_polluter_code") and cand_p and cand_p.strip() != sample["orig_polluter_code"].strip():
        parts.append(f"**Partner Test Unified Diff**:\n```diff\n{full_unified(sample['orig_polluter_code'], cand_p, f'partner_test.{ext}')}\n```")
    if cand.get("imports"):
        parts.append("**Added Imports**:\n```\n" + "\n".join(cand["imports"]) + "\n```")
    if cand.get("pom"):
        parts.append(f"**Added Build Dependency**:\n```xml\n{cand['pom']}\n```")
    pool = mext_pool_by_id(sample)
    mlines = []
    for idx, lm in enumerate(lost_lists(hev), 1):
        m = pool.get(lm.get("id"), {})
        mlines.append(
            f"- Lost Held-Out Mutant #{idx} [{lm.get('status', 'SURVIVED')}]: `{m.get('mutatedClass', '')}.{m.get('mutatedMethod', '')}:L{lm.get('line', m.get('lineNumber'))}` "
            f"(`{lm.get('mutator', '')}`: {lm.get('desc', m.get('description', ''))})\n  ```{lang}\n{m.get('source_slice', '(source slice unavailable)')}\n  ```"
        )
    parts.append("**Lost Unseen Held-Out Production Mutants (`M_ext`, complete list)**:\n" + "\n".join(mlines))
    return "\n\n".join(parts)


def adjudicate_new_losses(items: Dict[str, Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]], workers: int = 4) -> None:
    existing: Dict[str, Any] = json.loads(EXTRA_WSL_OUT.read_text(encoding="utf-8")) if EXTRA_WSL_OUT.exists() else {}
    todo = [ck for ck in sorted(items) if len((existing.get(ck) or {}).get("session_votes") or []) < 10]
    if not todo:
        return
    codex_bin = shutil.which("codex")
    if not codex_bin:
        raise RuntimeError("codex CLI not found")
    next_idx = 1 + max([int(v["blind_id"].split("-")[-1]) for v in existing.values() if v.get("blind_id")] or [0])
    bid_of: Dict[str, str] = {}
    blocks: Dict[str, str] = {}
    for i, ck in enumerate(todo):
        bid = f"HO-SOTA-{next_idx + i:03d}"
        bid_of[ck] = bid
        smp, cand, hev = items[ck]
        blocks[bid] = build_blinded_dossier(bid, smp, cand, hev)
    work = Path(tempfile.gettempdir()) / "ft1_v31_sota_blind_wsl"
    (work / "cwd").mkdir(parents=True, exist_ok=True)
    schema = work / "schema.json"
    schema.write_text(json.dumps(make_schema("HELDOUT_111"), indent=2), encoding="utf-8")
    jobs = []
    for s in range(1, 11):
        ids = list(blocks)
        random.Random(20261200 + s).shuffle(ids)
        for b, chunk in enumerate(chunk_list(ids, 12)):
            jobs.append((s, b, chunk))
    votes: Dict[str, Dict[int, Dict[str, Any]]] = defaultdict(dict)

    def _run(job):
        s, b, ids = job
        prompt = (
            f"You are an independent, blinded software-testing auditor evaluating {len(ids)} stabilized flaky-test patches (`{', '.join(ids)}`) "
            "that lost one or more unseen held-out production mutants (`M_ext`). Do NOT run shell commands; all information is below.\n"
            "Classify EVERY item into `label_wsl` strictly following Step 1 -> Step 2 -> Step 3:\n"
            f"{DECISION_TREE_RUBRIC}\n\nReturn an entry for EVERY ID: {', '.join(ids)}. Keep each rationale <= 30 words.\n\n"
            "================ BLINDED PATCH DOSSIERS ================\n\n" + "\n\n---\n\n".join(blocks[i] for i in ids)
        )
        out_f = work / f"s{s:02d}_b{b}.json"
        for att in range(1, 4):
            if out_f.exists():
                out_f.unlink()
            p = subprocess.run([codex_bin, "exec", "--ephemeral", "--skip-git-repo-check", "-C", str(work / "cwd"), "-s", "read-only",
                                "-m", ADJUDICATOR_MODEL, "-c", 'model_reasoning_effort="medium"', "--output-schema", str(schema), "-o", str(out_f), "-"],
                               input=prompt, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=900)
            if p.returncode == 0 and out_f.exists():
                got = {it["blind_id"]: it for it in json.loads(out_f.read_text(encoding="utf-8")).get("items", []) if it.get("blind_id") in ids}
                if set(got) == set(ids):
                    return s, got
            time.sleep(5 * att)
        raise RuntimeError(f"W/S/L batch failed s={s} b={b}")

    log(f"[PHASE D] Adjudicating {len(todo)} new M_ext-loss patches x 10 sessions ({len(jobs)} batches, model={ADJUDICATOR_MODEL})")
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for fut in as_completed([ex.submit(_run, j) for j in jobs]):
            s, got = fut.result()
            with SAVE_LOCK:
                for bid, it in got.items():
                    votes[bid][s] = it
    for ck in todo:
        bid = bid_of[ck]
        sv = [votes[bid][s]["label_wsl"] for s in range(1, 11)]
        maj, cnt, ent = majority_vote(sv, WSL_TIE_ORDER)
        existing[ck] = {"blind_id": bid, "majority_label": maj, "majority_count_of_10": cnt, "normalized_vote_entropy": ent,
                        "vote_counts": dict(Counter(sv)), "session_votes": sv, "adjudicator_model": ADJUDICATOR_MODEL,
                        "dossier_sha256": __import__("hashlib").sha256(blocks[bid].encode("utf-8")).hexdigest()}
    save_atomic(EXTRA_WSL_OUT, existing)


# =============================================================================
# 3. Primary-endpoint inference: paired repo bootstrap of a ratio difference
# =============================================================================
def ratio_diff_inference(per_repo: Dict[str, Tuple[int, int, int, int]], n_boot: int = 20000, seed: int = 20261126) -> Dict[str, Any]:
    """per_repo[r] = (num_A, den_A, num_B, den_B). Returns Δ=R_A-R_B, percentile CI, studentized null-centred p."""
    repos = sorted(per_repo)
    arr = np.array([per_repo[r] for r in repos], dtype=float)
    C = len(repos)

    def stat(a: np.ndarray) -> Tuple[Optional[float], Optional[float]]:
        nA, dA, nB, dB = a[:, 0].sum(), a[:, 1].sum(), a[:, 2].sum(), a[:, 3].sum()
        if dA <= 0 or dB <= 0:
            return None, None
        RA, RB = nA / dA, nB / dB
        z = (a[:, 0] - RA * a[:, 1]) / dA - (a[:, 2] - RB * a[:, 3]) / dB
        k = a.shape[0]
        se = math.sqrt(k / max(1, k - 1) * float((z ** 2).sum())) if k > 1 else 0.0
        return RA - RB, se

    d0, se0 = stat(arr)
    if d0 is None:
        return {"defined": False}
    rng = np.random.default_rng(seed)
    diffs, tstats, degenerate = [], [], 0
    for _ in range(n_boot):
        a = arr[rng.integers(0, C, C)]
        d, se = stat(a)
        if d is None:
            degenerate += 1
            continue
        diffs.append(d)
        if se and se > 1e-12:
            tstats.append((d - d0) / se)
    diffs = np.array(diffs)
    t0 = d0 / se0 if se0 and se0 > 1e-12 else (0.0 if abs(d0) < 1e-12 else float("inf"))
    p = float(np.mean(np.abs(np.array(tstats)) >= abs(t0))) if tstats else float("nan")
    nA, dA, nB, dB = arr.sum(axis=0)
    return {
        "defined": True,
        "rate_A_pct": round(100 * nA / dA, 2), "num_A": int(nA), "den_A": int(dA),
        "rate_B_pct": round(100 * nB / dB, 2), "num_B": int(nB), "den_B": int(dB),
        "diff_pp": round(100 * d0, 2),
        "ci95_pp": [round(100 * float(np.percentile(diffs, 2.5)), 2), round(100 * float(np.percentile(diffs, 97.5)), 2)],
        "se_pp": round(100 * se0, 2),
        "p_studentized_bootstrap": round(max(p, 1.0 / (len(tstats) + 1)), 4) if tstats else None,
        "n_repos": C, "degenerate_replicates": degenerate, "n_boot": n_boot,
    }


def validate_ratio_test(n_sims: int = 300, n_boot: int = 2000) -> Dict[str, Any]:
    """Type-I error check under unequal acceptance but EQUAL conditional harmful-loss rates."""
    rng = np.random.default_rng(7)
    rej = 0
    for s in range(n_sims):
        per = {}
        for r in range(40):
            nt = int(rng.integers(1, 9))
            dA = int(rng.binomial(nt, 0.45)); dB = int(rng.binomial(nt, 0.85))
            rate = float(rng.uniform(0.03, 0.15))
            per[f"r{r}"] = (int(rng.binomial(dA, rate)), dA, int(rng.binomial(dB, rate)), dB)
        res = ratio_diff_inference(per, n_boot=n_boot, seed=1000 + s)
        if res.get("defined") and res.get("p_studentized_bootstrap") is not None and res["p_studentized_bootstrap"] < 0.05:
            rej += 1
    return {"n_sims": n_sims, "n_boot": n_boot, "empirical_type_I_error_at_0.05": round(rej / n_sims, 3)}


def apply_family_holm_m4(fam: Dict[str, Dict[str, Any]], baselines: List[str] = BASELINES) -> None:
    """Apply Holm-Bonferroni across the full declared m=4 baseline family (`R3-6`).
    Undefined comparisons contribute p=1.0 placeholders so defined comparisons are still adjusted
    against all 4 positions (e.g., one defined p=0.02 + three undefined -> 0.08), while undefined
    comparisons retain `p_holm_m4 = None`."""
    pvals: Dict[str, float] = {}
    defined_flags: Dict[str, bool] = {}
    for base in baselines:
        entry = fam.get(base) or {}
        is_def = bool(entry.get("defined") and entry.get("p_studentized_bootstrap") is not None)
        defined_flags[base] = is_def
        pvals[base] = float(entry["p_studentized_bootstrap"]) if is_def else 1.0
    assert len(pvals) == len(baselines) == 4
    holm = v2e.holm_bonferroni(pvals)
    for base in baselines:
        fam[base]["p_holm_m4"] = holm.get(base) if defined_flags[base] else None


# =============================================================================
# 4. Main
# =============================================================================
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--codex-workers", type=int, default=4)
    ap.add_argument("--validate-test", action="store_true")
    ap.add_argument("--allow-incomplete", action="store_true", help="Analyse a partial cohort (interim diagnostics only)")
    args = ap.parse_args()

    if args.validate_test:
        print(json.dumps(validate_ratio_test(), indent=2))
        return

    bm = {s["sample_id"]: s for s in json.loads((DATA_DIR / "v2_frozen_benchmark_light.json").read_text(encoding="utf-8"))["samples"]}
    res = json.loads(SOTA_OUT_PATH.read_text(encoding="utf-8"))["results"]
    sids = [r["sample_id"] for r in res]
    assert len(sids) == len(set(sids)), "duplicate sample records"
    quarantined = get_quarantined_projects(DATA_DIR)
    if not args.allow_incomplete:
        assert len(res) == 203, f"incomplete SOTA cohort: {len(res)}/203"
        q_hits = [r["sample_id"] for r in res if r.get("project_name", "").lower() in quarantined]
        assert not q_hits, f"quarantined samples remain unresolved before Phase D analysis: {q_hits}"
    by_sid = {r["sample_id"]: r for r in res}
    e_sids = [s for s in sids if bm[s].get("m_ext_total_count", 0) > 0]

    labels = load_majority_labels()
    ho_map = load_eligible_heldout_map(DATA_DIR, SOTA_HO_PATH, existing_wsl_labels=labels)

    # Collect per-arm, per-test held-out classification; find losses needing new adjudication
    cls: Dict[str, Dict[str, Dict[str, Any]]] = {a: {} for a in ALL_SOTA_ARMS}
    need_adj: Dict[str, Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]] = {}
    incomplete_loss_evidence: List[str] = []
    for sid in e_sids:
        for arm in ALL_SOTA_ARMS:
            st = by_sid[sid]["strategies"][arm]
            if not (st.get("accepted") and st.get("final_candidate")):
                cls[arm][sid] = {"accepted": False}
                continue
            ck = f"{sid}::{candidate_fingerprint(st['final_candidate'])}"
            hs = heldout_status(ho_map.get(ck))
            hs.update({"accepted": True, "ck": ck})
            cls[arm][sid] = hs
            if hs["state"] == "MISSING":
                raise RuntimeError(f"missing held-out evaluation for {arm} {ck}; run --mode heldout first")
            if hs["state"] == "EVALUABLE" and hs["w_strict"] and hs["loss"] and ck not in labels:
                if not hs["lost_list_complete"]:
                    incomplete_loss_evidence.append(ck)
                else:
                    need_adj[ck] = (bm[sid], st["final_candidate"], ho_map[ck])
    if incomplete_loss_evidence:
        raise RuntimeError(f"{len(incomplete_loss_evidence)} new losses have truncated lost-mutant evidence; re-run their held-out "
                           f"evaluation with the uncapped evaluator first: {incomplete_loss_evidence[:5]}")
    if need_adj:
        adjudicate_new_losses(need_adj, workers=args.codex_workers)
        labels = load_majority_labels()

    # Per-arm descriptive summaries
    arms_summary: Dict[str, Any] = {}
    per_repo_counts: Dict[str, Dict[str, List[int]]] = {a: defaultdict(lambda: [0, 0]) for a in ALL_SOTA_ARMS}
    sf_vec: Dict[str, Dict[str, int]] = {a: {} for a in ALL_SOTA_ARMS}
    ys_vec: Dict[str, Dict[str, int]] = {a: {} for a in ALL_SOTA_ARMS}
    ysf_vec: Dict[str, Dict[str, int]] = {a: {} for a in ALL_SOTA_ARMS}
    for arm in ALL_SOTA_ARMS:
        w = er = sf = 0
        for sid in sids:
            st = by_sid[sid]["strategies"][arm]
            w += int(bool(st.get("w_plus_passed")))
            er += int(bool(st.get("eroded")))
            sf += int(bool(st.get("sound_fix")))
            sf_vec[arm][sid] = int(bool(st.get("sound_fix")))
        acc = ho_wfail = unk = prim = loss = harm = 0
        wsl = Counter()
        ys = ysf = 0
        for sid in e_sids:
            c = cls[arm][sid]
            repo = bm[sid]["project_name"]
            y = yf = 0
            if c.get("accepted"):
                acc += 1
                if c["state"] != "EVALUABLE":
                    unk += 1
                elif not c["w_strict"]:
                    ho_wfail += 1
                else:
                    prim += 1
                    per_repo_counts[arm][repo][1] += 1
                    if c["loss"]:
                        loss += 1
                        lab = labels[c["ck"]]["label"]
                        wsl[lab] += 1
                        if lab in HARMFUL:
                            harm += 1
                            per_repo_counts[arm][repo][0] += 1
                    else:
                        y = 1
                        yf = sf_vec[arm][sid]
            per_repo_counts[arm].setdefault(repo, [0, 0])
            ys += y
            ysf += yf
            ys_vec[arm][sid] = y
            ysf_vec[arm][sid] = yf
        arms_summary[arm] = {
            "n": len(sids), "w_plus": w, "leaked_eroded": er, "leak_pct_of_w_plus": round(100 * er / w, 2) if w else None,
            "sound_fix": sf, "sound_fix_pct": round(100 * sf / len(sids), 2),
            "heldout": {
                "E": len(e_sids), "accepted": acc, "unknown_mutation": unk, "heldout_w_plus_fail": ho_wfail,
                "primary_population": prim, "loss_any": loss, "harmful_WS": harm,
                "W": wsl.get("GENUINE_ORACLE_WEAKENING", 0), "S": wsl.get("TEST_RESPONSIBILITY_SHIFT", 0),
                "L": wsl.get("LEGITIMATE_RELAXATION_OR_CANONICALIZATION", 0),
                "conditional_harmful_pct": round(100 * harm / prim, 2) if prim else None,
                "strict_ys": ys, "strict_ys_pct": round(100 * ys / len(e_sids), 2),
                "full_ys": ysf, "full_ys_pct": round(100 * ysf / len(e_sids), 2),
            },
        }

    # Primary inference (two families, Holm m=4 each)
    primary: Dict[str, Any] = {}
    secondary: Dict[str, Any] = {}
    for ours in ("S4_MuGuard_Flake", "S5_FlakyGuard_Plus_MuGuard"):
        fam = {}
        sec = {}
        for base in BASELINES:
            repos = sorted(set(per_repo_counts[ours]) | set(per_repo_counts[base]))
            per = {r: (per_repo_counts[ours].get(r, [0, 0])[0], per_repo_counts[ours].get(r, [0, 0])[1],
                       per_repo_counts[base].get(r, [0, 0])[0], per_repo_counts[base].get(r, [0, 0])[1]) for r in repos}
            fam[base] = ratio_diff_inference(per)
            sec[base] = {
                "sound_fix": v2e.paired_project_cluster_inference(
                    [{"project": bm[s]["project_name"], "a": sf_vec[ours][s], "b": sf_vec[base][s]} for s in sids], "a", "b"),
                "strict_ys": v2e.paired_project_cluster_inference(
                    [{"project": bm[s]["project_name"], "a": ys_vec[ours][s], "b": ys_vec[base][s]} for s in e_sids], "a", "b"),
                "full_ys": v2e.paired_project_cluster_inference(
                    [{"project": bm[s]["project_name"], "a": ysf_vec[ours][s], "b": ysf_vec[base][s]} for s in e_sids], "a", "b"),
            }
        apply_family_holm_m4(fam, BASELINES)
        primary[ours] = fam
        secondary[ours] = sec

    reuse = sum(1 for a in ALL_SOTA_ARMS for s in e_sids if cls[a][s].get("accepted") and cls[a][s].get("ck") in labels)
    out = {
        "protocol_version": "3.1.1-sota-codex-consensus-analysis",
        "analysis_lock": "interim-informed (written after 82/203 outcomes; no held-out outcomes observed at lock time)",
        "primary_endpoint": "conditional harmful (W+S) held-out loss among accepted, strict-held-out-W+-passing, fully evaluable patches",
        "n_tests": len(sids), "E": len(e_sids), "complete_cohort": len(sids) == 203,
        "new_losses_adjudicated": len(need_adj), "accepted_records_with_reused_labels": reuse,
        "arms": arms_summary, "primary_inference": primary, "secondary_inference": secondary,
    }
    save_atomic(STAT_OUT, out)
    for arm, a in arms_summary.items():
        h = a["heldout"]
        print(f"{arm:28s} SF={a['sound_fix_pct']:5.1f}% leak={a['leaked_eroded']:2d} | prim={h['primary_population']:3d} harmful={h['harmful_WS']:2d} "
              f"({h['conditional_harmful_pct']}%) W/S/L={h['W']}/{h['S']}/{h['L']} unk={h['unknown_mutation']} hoWfail={h['heldout_w_plus_fail']} "
              f"StrictYs={h['strict_ys_pct']}% FullYs={h['full_ys_pct']}%")


if __name__ == "__main__":
    main()
