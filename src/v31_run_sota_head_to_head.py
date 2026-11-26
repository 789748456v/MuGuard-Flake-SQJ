"""
Phase C & Phase D: Confirmatory Same-Model, Same-Container End-to-End SOTA Head-to-Head Runner
(`v31_run_sota_head_to_head.py`) on `qwen3.7-plus` (`N = 203` Eligible Tests, `|E| = 177` Held-Out Cohort).

Evaluates 6 end-to-end configurations under identical `qwen3.7-plus` (`temperature = 0.0`),
identical Docker repositories, and fair deterministic Python method re-indentation:
  1. `S1_FlakyFix_K1`           鈥?Fatima et al. (IEEE TSE 2024): test-code-only + 13-class prediction + out-of-benchmark few-shot (`K=1`).
  2. `S1b_FlakyFix_K5`          鈥?Budget-matched `FlakyFix` (`K=5`, sharing Round 1 identically with `S1_FlakyFix_K1`).
  3. `S2_FlakyDoctor`           鈥?Chen & Jabbarvand (ISSTA 2024): neurosymbolic localization + reactive import `Stitching` + 3-identical-error early termination (`K=5`).
  4. `S3_FlakyGuard`            鈥?Li et al. (ASE 2025 Distinguished Paper, Uber): Dynamic Call Graph `F<=5` focal production functions + structured `<thought>` & failure `Reflection` history + single-sided $W^+$ validator (`K=5`).
  5. `S4_MuGuard_Flake`         鈥?Test-side localization context + fail-closed dual-role mutation gate + localized surviving-mutant witnesses (`K=5`).
  6. `S5_FlakyGuard_Plus_MuGuard` 鈥?Full combination: shares 100% of `S3_FlakyGuard`'s prefix & `F<=5` DCG context, intercepting eroded $W^+$ patches at round `r0` and injecting surviving-mutant witnesses into `FlakyGuard`'s Reflection loop (`K=5`).

Modes:
  - `--mode eval`    : Runs all 6 arms across `203/203` eligible tests with atomic checkpointing (`data/v31_sota_qwen3.7-plus.json`).
  - `--mode heldout` : Runs post-freeze external validation (`W+_heldout` + `M_ext`) on all newly accepted candidates in `|E| = 177` (`data/v31_sota_heldout_validation.json`).
"""
import argparse
import base64
import copy
import hashlib
import json
import random
import re
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ast_guard_and_prompts import PROMPT_GUARD_CONTRACT, parse_llm_patch_response
from python_ast_guard_and_prompts import PROMPT_GUARD_CONTRACT_PY, check_py_basic_sanity
from remote_and_llm import DATA_DIR, chat_completion, run_ssh, scp_to_remote
from v31_remote_channel import (
    InfraError,
    RemoteEvalChannel,
    parse_wrapper_output,
)
from run_full_study_matrix import (
    CACHE_LOCK,
    candidate_fingerprint,
    check_java_basic_sanity,
    load_disk_llm_cache,
    log,
    prompt_cache_key,
)
from run_v2_confirmatory_campaign import (
    V2_CACHE_LOCK,
    build_v2_prompt,
    compact_eval_dict,
    load_v2_eval_cache,
)
from v2_guards_and_attribution import HARNESS_VERSION, build_matched_ablation_feedback
from v3_python_indent_ablation import parse_py_response_normalized
from v31_build_flakyguard_dcg import build_sample_covered_map
from v31_flakyfix_fewshot_bank import (
    build_flakyfix_category_prompt,
    build_flakyfix_repair_prompt,
    parse_flakyfix_category,
    verify_flakyfix_zero_leakage,
)
from v31_sota_integrity import (
    DCG_REBUILT_COMPLETED_SIDS,
    FORCE_REEVAL_SIDS,
    SAFE_EVAL_REQUIRED_PROJECTS,
    compute_mext_affected_dcg_sids,
    dcg_entry_fingerprint,
    get_quarantined_projects,
    is_checkpoint_record_eligible,
    is_sample_dcg_certified,
    load_eligible_heldout_map,
)

SOTA_OUT_PATH = DATA_DIR / "v31_sota_qwen3.7-plus.json"
SOTA_HO_PATH = DATA_DIR / "v31_sota_heldout_validation.json"
DCG_PATH = DATA_DIR / "v3_flakyguard_dcg_context.json"
V31_LLM_CACHE_PATH = DATA_DIR / "v31_llm_cache_qwen3.7-plus.json"
V31_EVAL_CACHE_PATH = DATA_DIR / "v31_eval_cache.json"

# Single persistent SSH channel to the remote evaluation server (4 concurrent remote evaluations)
REMOTE_CHANNEL = RemoteEvalChannel(remote_workers=4)
STOP_EVENT = threading.Event()


class AccountExhausted(RuntimeError):
    pass


# Integrity rules (`FORCE_REEVAL_SIDS`, `DCG_REBUILT_COMPLETED_SIDS`, `SAFE_EVAL_REQUIRED_PROJECTS`,
# and `get_quarantined_projects`) are defined centrally in `v31_sota_integrity.py`.
PROJ_LOCKS: Dict[str, threading.Lock] = defaultdict(threading.Lock)
SAVE_LOCK = threading.Lock()

ALL_SOTA_ARMS = [
    "S1_FlakyFix_K1",
    "S1b_FlakyFix_K5",
    "S2_FlakyDoctor",
    "S3_FlakyGuard",
    "S4_MuGuard_Flake",
    "S5_FlakyGuard_Plus_MuGuard",
]


def save_json_atomic(path: Path, obj: Any) -> None:
    tmp = path.with_suffix(f".{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
    for _ in range(6):
        try:
            tmp.replace(path)
            return
        except PermissionError:
            time.sleep(0.15)
    tmp.replace(path)


def call_llm_v31(
    model: str,
    prompt: str,
    purpose: str,
    ro_llm_cache: Dict[str, Any],
    rw_llm_cache: Dict[str, Any],
    fast_classify: bool = False,
) -> Tuple[Dict[str, Any], bool]:
    """Read-through `ro_llm_cache` (`llm_cache_qwen3.7-plus.json`, untouched) + write-to `v31_llm_cache`."""
    k = prompt_cache_key(model, prompt)
    with CACHE_LOCK:
        if k in rw_llm_cache and (rw_llm_cache[k].get("content") or "").strip():
            return rw_llm_cache[k], True
        if k in ro_llm_cache and (ro_llm_cache[k].get("content") or "").strip():
            return ro_llm_cache[k], True

    extra_body = {"enable_thinking": True, "thinking_budget": 768} if fast_classify else None
    max_toks = 1536 if fast_classify else 8192

    last_exc: Optional[Exception] = None
    for attempt in range(5):
        if fast_classify:
            cur_extra: Optional[Dict[str, Any]] = {"enable_thinking": True, "thinking_budget": 768}
        elif attempt == 0:
            cur_extra = None
        elif attempt == 1:
            cur_extra = {"enable_thinking": True, "thinking_budget": 3072}
        else:
            cur_extra = {"enable_thinking": False}
        try:
            resp = chat_completion(
                provider=None,
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=max_toks,
                purpose=purpose,
                timeout=180,
                extra_body=cur_extra,
            )
            if not (resp.get("content") or "").strip():
                raise RuntimeError("Empty LLM content")
            slim = {
                "content": resp["content"],
                "latency_sec": resp["latency_sec"],
                "tokens": (resp.get("usage") or {}).get("total_tokens", 0),
            }
            with CACHE_LOCK:
                rw_llm_cache[k] = slim
                if len(rw_llm_cache) % 8 == 0:
                    save_json_atomic(V31_LLM_CACHE_PATH, rw_llm_cache)
            return slim, False
        except Exception as e:
            last_exc = e
            log(f"  [LLM retry {attempt+1}/5] {purpose}: {str(e)[:140]}")
            if any(q in str(e) for q in ("Arrearage", "http_402", "good standing", "Insufficient Balance")):
                STOP_EVENT.set()
                raise AccountExhausted(f"API account exhausted; stopping all workers: {str(e)[:200]}")
            time.sleep(3 * (attempt + 1))
    raise last_exc or RuntimeError(f"LLM call failed: {purpose}")


def parse_fair_response(raw_text: str, sample: Dict[str, Any]) -> Dict[str, Any]:
    if sample.get("language") == "python":
        return parse_py_response_normalized(raw_text)
    return parse_llm_patch_response(raw_text, sample["test_type"])


def extract_thought_summary(raw_text: str) -> str:
    m = re.search(r"<thought>(.*?)</thought>", raw_text or "", re.DOTALL | re.IGNORECASE)
    if m:
        s = " ".join(m.group(1).strip().split())
        return s[:450]
    # Fallback: text before first code marker
    for mk in ("//<fix start>", "//<victim start>", "#<victim start>"):
        idx = (raw_text or "").find(mk)
        if idx > 0:
            s = " ".join(raw_text[:idx].strip().split())
            if s:
                return s[:350]
    return "(No explicit thought summary emitted)"


def remote_eval_fast(
    sample: Dict[str, Any],
    cand: Dict[str, Any],
    bench_sha: str,
    ro_eval_cache: Dict[str, Dict[str, Any]],
    rw_eval_cache: Dict[str, Dict[str, Any]],
    mode: str = "in_loop_gate",
) -> Tuple[Dict[str, Any], bool]:
    """Evaluate via the single persistent SSH channel. Infrastructure failures retry the SAME candidate,
    never consume a repair round, and are never cached. Records evaluated in implicated repositories
    (restart-contaminated working trees) only reuse cache entries produced by the integrity-safe wrapper."""
    if STOP_EVENT.is_set():
        raise AccountExhausted("global stop requested")
    sid = sample["sample_id"]
    proj_low = sample["project_name"].lower()
    c_fp = candidate_fingerprint(cand)
    ckey = f"{HARNESS_VERSION}::{bench_sha[:12]}::{mode}::{sid}::{c_fp}"
    forced = (
        (sid in FORCE_REEVAL_SIDS)
        or (proj_low in SAFE_EVAL_REQUIRED_PROJECTS and mode == "post_freeze_heldout")
        or (proj_low == "openwebbeans")
    )

    with V2_CACHE_LOCK:
        hit = rw_eval_cache.get(ckey)
        if hit is not None and (not forced or str(hit.get("_provenance", "")).startswith("v31_safe_eval")):
            return dict(hit), True
        if not forced and ckey in ro_eval_cache:
            return dict(ro_eval_cache[ckey]), True

    b64_cand = base64.b64encode(json.dumps(cand, ensure_ascii=False).encode("utf-8")).decode("ascii")
    last_exc: Optional[Exception] = None
    for attempt in range(10):
        try:
            msg = REMOTE_CHANNEL.evaluate_raw(sample["proj_dir"], mode, sid, b64_cand)
            ev = parse_wrapper_output(msg["stdout"], sid)
            ev["_provenance"] = "v31_safe_eval:" + REMOTE_CHANNEL.deployed_sha.get("remote_eval_v2.py", "")[:16]
            if ev.get("_pre_restored_files"):
                log(f"  [INTEGRITY] {sid}: wrapper restored leftover tracked edits before eval: {ev['_pre_restored_files']}")
            with V2_CACHE_LOCK:
                rw_eval_cache[ckey] = dict(ev)
                if len(rw_eval_cache) % 8 == 0:
                    save_json_atomic(V31_EVAL_CACHE_PATH, rw_eval_cache)
            return ev, False
        except InfraError as e:
            last_exc = e
            log(f"  [INFRA retry {attempt+1}/10] {sid}: {str(e)[:200]}")
            time.sleep(min(90, 10 * (attempt + 1)) + random.uniform(0, 3))
    raise InfraError(f"infrastructure retries exhausted on {sid}: {last_exc}")


def extract_cand_methods(sample: Dict[str, Any], cand: Dict[str, Any]) -> Tuple[Dict[str, str], Dict[str, str]]:
    lang = sample["language"]
    ttype = sample["test_type"]
    orig_methods: Dict[str, str] = {"victim": sample["orig_test_code"]}
    if (ttype == "OD" or lang == "python") and sample.get("orig_polluter_code"):
        orig_methods["polluter"] = sample["orig_polluter_code"]
    if lang == "java" and ttype == "ID":
        cand_methods = {"victim": cand.get("test_code") or ""}
    else:
        cand_methods = {"victim": cand.get("victim_code") or ""}
        if cand.get("polluter_code"):
            cand_methods["polluter"] = cand["polluter_code"]
    return orig_methods, cand_methods


def check_basic_sanity_any(sample: Dict[str, Any], cand: Dict[str, Any]) -> Tuple[bool, List[str]]:
    orig_m, cand_m = extract_cand_methods(sample, cand)
    if sample["language"] == "python":
        return check_py_basic_sanity(orig_m, cand_m)
    return check_java_basic_sanity(orig_m, cand_m)


def finalize_arm_record(strategy: str, rounds_trace: List[Dict[str, Any]], accepted_round: Optional[int], final_cand: Optional[Dict[str, Any]], final_eval: Optional[Dict[str, Any]], extra_meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    accepted = accepted_round is not None and final_eval is not None
    gate_state = rounds_trace[-1]["decision"] if rounds_trace else "UNRESOLVED"
    w_plus_ok = bool(accepted and final_eval.get("w_plus_passed"))
    eroded = bool(accepted and final_eval.get("eroded"))
    gate_safe = bool(accepted and final_eval.get("gate_safe"))
    sound_fix = bool(accepted and w_plus_ok and gate_safe and not eroded)
    rec = {
        "strategy": strategy,
        "harness_version": "3.1.0-sota-head2head",
        "gate_state": gate_state,
        "accepted": accepted,
        "accepted_round": accepted_round,
        "w_plus_passed": w_plus_ok,
        "gate_safe": gate_safe,
        "eroded": eroded,
        "sound_fix": sound_fix,
        "target_retention": final_eval.get("target_retention") if final_eval else None,
        "partner_retention": final_eval.get("partner_retention") if final_eval else None,
        "joint_retention": final_eval.get("joint_retention") if final_eval else None,
        "scope_kind": final_eval.get("scope_kind") if final_eval else None,
        "final_candidate": final_cand,
        "final_eval": final_eval,
        "rounds": rounds_trace,
    }
    if extra_meta:
        rec.update(extra_meta)
    return rec


# =============================================================================
# 1. `S1_FlakyFix_K1` & `S1b_FlakyFix_K5` (IEEE TSE 2024)
# =============================================================================
def run_flakyfix_arms(
    sample: Dict[str, Any],
    bench_sha: str,
    model: str,
    ro_llm_cache: Dict[str, Any],
    rw_llm_cache: Dict[str, Any],
    ro_eval_cache: Dict[str, Any],
    rw_eval_cache: Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    sid = sample["sample_id"]
    # Step 1: Predict 1 of 13 categories from TEST CODE ONLY
    cat_prompt = build_flakyfix_category_prompt(sample)
    cat_resp, _ = call_llm_v31(model, cat_prompt, f"v31_flakyfix_cat_{sid}", ro_llm_cache, rw_llm_cache, fast_classify=True)
    pred_cat = parse_flakyfix_category(cat_resp["content"])

    rounds_k5: List[Dict[str, Any]] = []
    prev_cand: Optional[Dict[str, Any]] = None
    feedback_msg = ""
    acc_round_k5: Optional[int] = None
    final_cand_k5: Optional[Dict[str, Any]] = None
    final_eval_k5: Optional[Dict[str, Any]] = None

    for r_idx in range(1, 6):
        prompt = build_flakyfix_repair_prompt(sample, pred_cat, r_idx, prev_cand, feedback_msg)
        prompt_sha = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]
        llm_resp, llm_cached = call_llm_v31(model, prompt, f"v31_flakyfix_{sid}_r{r_idx}", ro_llm_cache, rw_llm_cache)
        cand = parse_fair_response(llm_resp["content"], sample)
        # FlakyFix operates strictly on the target flaky test only (never edits partner test)
        if sample["language"] == "python" or sample["test_type"] == "OD":
            cand["polluter_code"] = None
        cand_fp = candidate_fingerprint(cand)

        san_ok, san_errs = check_basic_sanity_any(sample, cand)
        if not san_ok:
            feedback_msg = "SYNTAX/SANITY REJECTION:\n- " + "\n- ".join(san_errs)
            prev_cand = cand
            rounds_k5.append({
                "round": r_idx, "prompt_sha": prompt_sha, "candidate_fp": cand_fp,
                "llm_cached": llm_cached, "eval_cached": False, "llm_sec": llm_resp["latency_sec"],
                "ast_rejected": True, "ast_violations": san_errs, "candidate": cand, "eval": None,
                "decision": "SANITY_REJECT_RETRY" if r_idx < 5 else "SANITY_REJECT_FINAL",
            })
            continue

        ev_full, eval_cached = remote_eval_fast(sample, cand, bench_sha, ro_eval_cache, rw_eval_cache, "in_loop_gate")
        ev_comp = compact_eval_dict(ev_full)

        if not ev_full["compiled"]:
            feedback_msg = f"COMPILATION / SYNTAX FAILED:\n```\n{ev_full['compile_error']}\n```"
            prev_cand = cand
            rounds_k5.append({
                "round": r_idx, "prompt_sha": prompt_sha, "candidate_fp": cand_fp,
                "llm_cached": llm_cached, "eval_cached": eval_cached, "llm_sec": llm_resp["latency_sec"],
                "ast_rejected": False, "candidate": cand, "eval": ev_comp,
                "decision": "COMPILE_RETRY" if r_idx < 5 else "ABSTAIN_COMPILE",
            })
            continue

        if not ev_full["w_plus_passed"]:
            feedback_msg = f"STABILITY VALIDATION (W+) FAILED:\n```\n{ev_full['w_plus_diag']}\n```"
            prev_cand = cand
            rounds_k5.append({
                "round": r_idx, "prompt_sha": prompt_sha, "candidate_fp": cand_fp,
                "llm_cached": llm_cached, "eval_cached": eval_cached, "llm_sec": llm_resp["latency_sec"],
                "ast_rejected": False, "candidate": cand, "eval": ev_comp,
                "decision": "UNSTABLE_RETRY" if r_idx < 5 else "ABSTAIN_UNSTABLE",
            })
            continue

        # Passed W+! Single-sided validator accepts immediately.
        acc_round_k5 = r_idx
        final_cand_k5 = cand
        final_eval_k5 = ev_comp
        rounds_k5.append({
            "round": r_idx, "prompt_sha": prompt_sha, "candidate_fp": cand_fp,
            "llm_cached": llm_cached, "eval_cached": eval_cached, "llm_sec": llm_resp["latency_sec"],
            "ast_rejected": False, "candidate": cand, "eval": ev_comp, "decision": "ACCEPTED",
        })
        break

    # Derive S1_FlakyFix_K1 directly from Round 1!
    r1 = copy.deepcopy(rounds_k5[0])
    if r1["decision"] == "ACCEPTED":
        s1_k1 = finalize_arm_record("S1_FlakyFix_K1", [r1], 1, r1["candidate"], r1["eval"], {"predicted_category": pred_cat})
    else:
        r1["decision"] = r1["decision"].replace("_RETRY", "_FINAL")
        s1_k1 = finalize_arm_record("S1_FlakyFix_K1", [r1], None, None, None, {"predicted_category": pred_cat})

    s1b_k5 = finalize_arm_record("S1b_FlakyFix_K5", rounds_k5, acc_round_k5, final_cand_k5, final_eval_k5, {"predicted_category": pred_cat})
    return s1_k1, s1b_k5


# =============================================================================
# 2. `S2_FlakyDoctor` (ISSTA 2024: Stitching + 3-Identical-Error Early Stop)
# =============================================================================
def resolve_stitching_conflicting_imports(cand: Dict[str, Any], compile_err: str) -> Optional[Dict[str, Any]]:
    """
    FlakyDoctor 搂3.3 Stitching: When newly injected Java imports collide with existing
    single-type or static imports (`reference to X is ambiguous`, `already defined in`),
    strip the conflicting added import(s) (or fallback to stripping newly added wildcard/static imports).
    """
    imps = cand.get("imports") or []
    if not imps:
        return None
    err_low = (compile_err or "").lower()
    if not any(k in err_low for k in ("is ambiguous", "already defined in", "conflicts with")):
        return None

    # Extract ambiguous symbol names if present
    syms = set(re.findall(r"reference to ([A-Za-z0-9_]+) is ambiguous", compile_err or ""))
    syms.update(re.findall(r"([A-Za-z0-9_]+) is already defined in", compile_err or ""))

    filtered = []
    for imp in imps:
        drop = False
        for sym in syms:
            if imp.endswith(f".{sym};") or (".*;" in imp and "static " in imp):
                drop = True
                break
        if not syms and ".*;" in imp:
            drop = True
        if not drop:
            filtered.append(imp)
    if filtered == imps:
        filtered = []  # Drop all newly added imports if wildcard collision persisted
    new_cand = copy.deepcopy(cand)
    new_cand["imports"] = filtered
    return new_cand


def normalize_error_sig_for_early_stop(kind: str, msg: str) -> str:
    """Substantive normalized diagnostic signature (FlakyDoctor termination criterion 1): compares the actual
    compiler/test messages (file basename + message, line numbers/paths/addresses stripped), never a generic
    heading such as `[ERROR] COMPILATION ERROR`."""
    generic = re.compile(r"(COMPILATION ERROR|BUILD FAILURE|Failed to execute goal|Re-run Maven|To see the full stack|For more information|Help 1|^\[ERROR\]\s*$|-> \[Help)", re.I)
    subst: List[str] = []
    for ln in (msg or "").splitlines():
        s = ln.strip()
        if not s or generic.search(s):
            continue
        s = re.sub(r"^\[ERROR\]\s*", "", s)
        s = re.sub(r"(/[^\s:]+/)([^/\s:]+\.(?:java|py))", r"\2", s)
        s = re.sub(r"\[\d+,\d+\]|:\d+(:\d+)?", "", s)
        s = re.sub(r"0x[0-9a-fA-F]+", "0xADDR", s)
        s = re.sub(r"\s+", " ", s)
        if s:
            subst.append(s[:160])
        if len(subst) >= 4:
            break
    return f"{kind}::" + " || ".join(subst) if subst else f"{kind}::<no-substantive-diagnostic>"


def run_flakydoctor_arm(
    sample: Dict[str, Any],
    bench_sha: str,
    model: str,
    ro_llm_cache: Dict[str, Any],
    rw_llm_cache: Dict[str, Any],
    ro_eval_cache: Dict[str, Any],
    rw_eval_cache: Dict[str, Any],
) -> Dict[str, Any]:
    sid = sample["sample_id"]
    rounds_trace: List[Dict[str, Any]] = []
    prev_cand: Optional[Dict[str, Any]] = None
    feedback_msg = ""
    acc_round: Optional[int] = None
    final_cand: Optional[Dict[str, Any]] = None
    final_eval: Optional[Dict[str, Any]] = None
    err_sigs: List[str] = []
    stitching_count = 0
    early_stopped_3x = False

    for r_idx in range(1, 6):
        # Uses exact B0_Standard_Validator neurosymbolic localization prompt -> hits existing cache when identical!
        prompt = build_v2_prompt("B0_Standard_Validator", sample, r_idx, prev_cand, feedback_msg)
        prompt_sha = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]
        llm_resp, llm_cached = call_llm_v31(model, prompt, f"v2_B0_Standard_Validator_{sid}_r{r_idx}", ro_llm_cache, rw_llm_cache)
        cand = parse_fair_response(llm_resp["content"], sample)
        cand_fp = candidate_fingerprint(cand)

        san_ok, san_errs = check_basic_sanity_any(sample, cand)
        if not san_ok:
            feedback_msg = "SYNTAX/SANITY REJECTION:\n- " + "\n- ".join(san_errs)
            prev_cand = cand
            esig = normalize_error_sig_for_early_stop("SANITY", san_errs[0] if san_errs else "")
            err_sigs.append(esig)
            stop3 = len(err_sigs) >= 3 and err_sigs[-1] == err_sigs[-2] == err_sigs[-3]
            rounds_trace.append({
                "round": r_idx, "prompt_sha": prompt_sha, "candidate_fp": cand_fp,
                "llm_cached": llm_cached, "eval_cached": False, "llm_sec": llm_resp["latency_sec"],
                "ast_rejected": True, "ast_violations": san_errs, "candidate": cand, "eval": None,
                "decision": "EARLY_STOP_SAME_ERROR_3X" if stop3 else ("SANITY_REJECT_RETRY" if r_idx < 5 else "SANITY_REJECT_FINAL"),
            })
            if stop3:
                early_stopped_3x = True
                break
            continue

        ev_full, eval_cached = remote_eval_fast(sample, cand, bench_sha, ro_eval_cache, rw_eval_cache, "in_loop_gate")
        stitched_this_round = False
        if not ev_full["compiled"] and sample["language"] == "java":
            stitched_cand = resolve_stitching_conflicting_imports(cand, ev_full.get("compile_error", ""))
            if stitched_cand is not None:
                ev_st, ec_st = remote_eval_fast(sample, stitched_cand, bench_sha, ro_eval_cache, rw_eval_cache, "in_loop_gate")
                stitching_count += 1
                stitched_this_round = True
                cand = stitched_cand
                cand_fp = candidate_fingerprint(cand)
                ev_full = ev_st
                eval_cached = eval_cached and ec_st

        ev_comp = compact_eval_dict(ev_full)

        if not ev_full["compiled"]:
            feedback_msg = f"COMPILATION / SYNTAX FAILED:\n```\n{ev_full['compile_error']}\n```\nFix all errors."
            prev_cand = cand
            esig = normalize_error_sig_for_early_stop("COMPILE", ev_full.get("compile_error", ""))
            err_sigs.append(esig)
            stop3 = len(err_sigs) >= 3 and err_sigs[-1] == err_sigs[-2] == err_sigs[-3]
            rounds_trace.append({
                "round": r_idx, "prompt_sha": prompt_sha, "candidate_fp": cand_fp,
                "llm_cached": llm_cached, "eval_cached": eval_cached, "llm_sec": llm_resp["latency_sec"],
                "stitching_applied": stitched_this_round, "ast_rejected": False, "candidate": cand, "eval": ev_comp,
                "decision": "EARLY_STOP_SAME_ERROR_3X" if stop3 else ("COMPILE_RETRY" if r_idx < 5 else "ABSTAIN_COMPILE"),
            })
            if stop3:
                early_stopped_3x = True
                break
            continue

        if not ev_full["w_plus_passed"]:
            feedback_msg = (
                f"STABILITY VALIDATION (W+) FAILED:\n```\n{ev_full['w_plus_diag']}\n```\n"
                "Your patch compiled, but failed NonDex / order execution."
            )
            prev_cand = cand
            esig = normalize_error_sig_for_early_stop("UNSTABLE", ev_full.get("w_plus_diag", ""))
            err_sigs.append(esig)
            stop3 = len(err_sigs) >= 3 and err_sigs[-1] == err_sigs[-2] == err_sigs[-3]
            rounds_trace.append({
                "round": r_idx, "prompt_sha": prompt_sha, "candidate_fp": cand_fp,
                "llm_cached": llm_cached, "eval_cached": eval_cached, "llm_sec": llm_resp["latency_sec"],
                "stitching_applied": stitched_this_round, "ast_rejected": False, "candidate": cand, "eval": ev_comp,
                "decision": "EARLY_STOP_SAME_ERROR_3X" if stop3 else ("UNSTABLE_RETRY" if r_idx < 5 else "ABSTAIN_UNSTABLE"),
            })
            if stop3:
                early_stopped_3x = True
                break
            continue

        acc_round = r_idx
        final_cand = cand
        final_eval = ev_comp
        rounds_trace.append({
            "round": r_idx, "prompt_sha": prompt_sha, "candidate_fp": cand_fp,
            "llm_cached": llm_cached, "eval_cached": eval_cached, "llm_sec": llm_resp["latency_sec"],
            "stitching_applied": stitched_this_round, "ast_rejected": False, "candidate": cand, "eval": ev_comp,
            "decision": "ACCEPTED",
        })
        break

    return finalize_arm_record(
        "S2_FlakyDoctor",
        rounds_trace,
        acc_round,
        final_cand,
        final_eval,
        {"stitching_triggered_count": stitching_count, "early_stopped_same_error_3x": early_stopped_3x},
    )


# =============================================================================
# 3. `S4_MuGuard_Flake` (Test-Side Context + Fair Re-Indenter + Fail-Closed Gate)
# =============================================================================
def run_muguard_testside_arm(
    sample: Dict[str, Any],
    bench_sha: str,
    model: str,
    ro_llm_cache: Dict[str, Any],
    rw_llm_cache: Dict[str, Any],
    ro_eval_cache: Dict[str, Any],
    rw_eval_cache: Dict[str, Any],
) -> Dict[str, Any]:
    sid = sample["sample_id"]
    lang = sample["language"]
    rounds_trace: List[Dict[str, Any]] = []
    prev_cand: Optional[Dict[str, Any]] = None
    feedback_msg = ""
    acc_round: Optional[int] = None
    final_cand: Optional[Dict[str, Any]] = None
    final_eval: Optional[Dict[str, Any]] = None
    erosion_retries = 0

    for r_idx in range(1, 6):
        prompt = build_v2_prompt("Ours_MuGuard_Flake", sample, r_idx, prev_cand, feedback_msg)
        prompt_sha = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]
        llm_resp, llm_cached = call_llm_v31(model, prompt, f"v2_Ours_MuGuard_Flake_{sid}_r{r_idx}", ro_llm_cache, rw_llm_cache)
        cand = parse_fair_response(llm_resp["content"], sample)
        cand_fp = candidate_fingerprint(cand)

        san_ok, san_errs = check_basic_sanity_any(sample, cand)
        if not san_ok:
            feedback_msg = "SYNTAX/SANITY REJECTION:\n- " + "\n- ".join(san_errs)
            prev_cand = cand
            rounds_trace.append({
                "round": r_idx, "prompt_sha": prompt_sha, "candidate_fp": cand_fp,
                "llm_cached": llm_cached, "eval_cached": False, "llm_sec": llm_resp["latency_sec"],
                "ast_rejected": True, "ast_violations": san_errs, "candidate": cand, "eval": None,
                "decision": "SANITY_REJECT_RETRY" if r_idx < 5 else "SANITY_REJECT_FINAL",
            })
            continue

        ev_full, eval_cached = remote_eval_fast(sample, cand, bench_sha, ro_eval_cache, rw_eval_cache, "in_loop_gate")
        ev_comp = compact_eval_dict(ev_full)

        if not ev_full["compiled"]:
            feedback_msg = f"COMPILATION / SYNTAX FAILED:\n```\n{ev_full['compile_error']}\n```\nFix all errors."
            prev_cand = cand
            rounds_trace.append({
                "round": r_idx, "prompt_sha": prompt_sha, "candidate_fp": cand_fp,
                "llm_cached": llm_cached, "eval_cached": eval_cached, "llm_sec": llm_resp["latency_sec"],
                "ast_rejected": False, "candidate": cand, "eval": ev_comp,
                "decision": "COMPILE_RETRY" if r_idx < 5 else "ABSTAIN_COMPILE",
            })
            continue

        if not ev_full["w_plus_passed"]:
            feedback_msg = (
                f"STABILITY VALIDATION (W+) FAILED:\n```\n{ev_full['w_plus_diag']}\n```\n"
                "Your patch compiled, but failed NonDex / order execution."
            )
            prev_cand = cand
            rounds_trace.append({
                "round": r_idx, "prompt_sha": prompt_sha, "candidate_fp": cand_fp,
                "llm_cached": llm_cached, "eval_cached": eval_cached, "llm_sec": llm_resp["latency_sec"],
                "ast_rejected": False, "candidate": cand, "eval": ev_comp,
                "decision": "UNSTABLE_RETRY" if r_idx < 5 else "ABSTAIN_UNSTABLE",
            })
            continue

        override = ev_full.get("gate_status_override")
        if override == "ABSTAIN_PARTNER_UNCOVERED":
            if r_idx < 5:
                feedback_msg = (
                    "PARTNER SCOPE UNVERIFIABLE: The partner test has zero baseline focal mutation obligations, "
                    "so modifying the partner test or shared fixture cannot be verified safe. "
                    "Leave the partner test completely unchanged and isolate/reset state strictly at the start of the target test!"
                )
                prev_cand = cand
                rounds_trace.append({
                    "round": r_idx, "prompt_sha": prompt_sha, "candidate_fp": cand_fp,
                    "llm_cached": llm_cached, "eval_cached": eval_cached, "llm_sec": llm_resp["latency_sec"],
                    "ast_rejected": False, "candidate": cand, "eval": ev_comp, "decision": "PARTNER_UNCOVERED_RETRY",
                })
                continue
            rounds_trace.append({
                "round": r_idx, "prompt_sha": prompt_sha, "candidate_fp": cand_fp,
                "llm_cached": llm_cached, "eval_cached": eval_cached, "llm_sec": llm_resp["latency_sec"],
                "ast_rejected": False, "candidate": cand, "eval": ev_comp, "decision": "ABSTAIN_PARTNER_UNCOVERED",
            })
            break

        if not ev_full.get("pit_ran") or override == "PARTNER_MUT_EVAL_ERROR" or ev_full.get("joint_retention") is None:
            if r_idx < 5:
                feedback_msg = (
                    f"FOCAL MUTATION PROBE ERROR:\n```\n{ev_full.get('w_plus_diag', '')}\n```\n"
                    "Ensure your test compiles and executes cleanly in standalone isolation."
                )
                prev_cand = cand
                rounds_trace.append({
                    "round": r_idx, "prompt_sha": prompt_sha, "candidate_fp": cand_fp,
                    "llm_cached": llm_cached, "eval_cached": eval_cached, "llm_sec": llm_resp["latency_sec"],
                    "ast_rejected": False, "candidate": cand, "eval": ev_comp, "decision": "EVAL_ERROR_RETRY",
                })
                continue
            rounds_trace.append({
                "round": r_idx, "prompt_sha": prompt_sha, "candidate_fp": cand_fp,
                "llm_cached": llm_cached, "eval_cached": eval_cached, "llm_sec": llm_resp["latency_sec"],
                "ast_rejected": False, "candidate": cand, "eval": ev_comp, "decision": "ABSTAIN_EVAL_ERROR",
            })
            break

        if ev_full["gate_safe"]:
            acc_round = r_idx
            final_cand = cand
            final_eval = ev_comp
            rounds_trace.append({
                "round": r_idx, "prompt_sha": prompt_sha, "candidate_fp": cand_fp,
                "llm_cached": llm_cached, "eval_cached": eval_cached, "llm_sec": llm_resp["latency_sec"],
                "ast_rejected": False, "candidate": cand, "eval": ev_comp, "decision": "SAFE_PASS",
            })
            break

        if r_idx < 5:
            erosion_retries += 1
            surv = ev_full.get("survived_mutants", [])
            surv_ids = {m["id"] for m in surv}
            non_surv = [m for m in sample["m_det"] if m["id"] not in surv_ids]
            fb_obj = build_matched_ablation_feedback(
                language=lang,
                total_obligations=ev_full["joint_m_det_total"],
                survived_mutants=surv,
                non_survived_pool=non_surv,
                mode="A3_TrueWitness",
            )
            feedback_msg = fb_obj["text"]
            prev_cand = cand
            rounds_trace.append({
                "round": r_idx, "prompt_sha": prompt_sha, "candidate_fp": cand_fp,
                "llm_cached": llm_cached, "eval_cached": eval_cached, "llm_sec": llm_resp["latency_sec"],
                "ast_rejected": False, "candidate": cand, "eval": ev_comp, "decision": "EROSION_RETRY",
            })
            continue
        rounds_trace.append({
            "round": r_idx, "prompt_sha": prompt_sha, "candidate_fp": cand_fp,
            "llm_cached": llm_cached, "eval_cached": eval_cached, "llm_sec": llm_resp["latency_sec"],
            "ast_rejected": False, "candidate": cand, "eval": ev_comp, "decision": "ABSTAIN_EROSION",
        })
        break

    return finalize_arm_record("S4_MuGuard_Flake", rounds_trace, acc_round, final_cand, final_eval, {"erosion_retries": erosion_retries})


# =============================================================================
# 4. `S3_FlakyGuard` & `S5_FlakyGuard_Plus_MuGuard` (ASE 2025 + Strict Prefix Sharing!)
# =============================================================================
def build_flakyguard_prompt(
    sample: Dict[str, Any],
    dcg_entry: Dict[str, Any],
    reflection_history: List[Dict[str, Any]],
    with_witness_contract: bool = False,
) -> str:
    """
    Faithful FlakyGuard (Li et al., ASE 2025 搂III-B & 搂III-C) Prompt:
      - Dynamic failure diagnostic + test & fixture context (+ partner test for OD)
      - F <= 5 dynamically executed focal production functions from pruned Dynamic Call Graph
      - Accumulated Reflection History of prior failed strategies (`<thought>` + execution/witness feedback)
      - Structured `<thought>` (Root Cause Category, Explanation, Fixing Plan) before patch markers.
      Note: On pre-fork rounds (`with_witness_contract=False`), `S3` and `S5` share 100% identical prompts!
    """
    lang = sample["language"]
    ttype = sample["test_type"]
    fence = "java" if lang == "java" else "python"

    # Format F<=5 DCG focal production functions
    focal_blocks = []
    for idx, fn in enumerate((dcg_entry.get("selected_functions") or [])[:5], 1):
        focal_blocks.append(
            f"[Focal Function #{idx}] `{fn['signature']}` (`{fn['rel_file']}:L{fn['start_line']}-L{fn['end_line']}`)\n"
            f"```{fence}\n{fn['code']}\n```"
        )
    dcg_section = (
        "=== DYNAMIC CALL GRAPH FOCAL PRODUCTION CONTEXT (F <= 5 Executed Functions) ===\n"
        + "\n\n".join(focal_blocks)
        + "\n"
    )

    if lang == "java":
        if ttype == "ID":
            test_ctx = (
                f"Target Flaky Test (`ID` / NonDex): `{sample['test_full_name']}`\n"
                f"Test Class Imports & Fixtures:\n```java\n{sample['context_header']}\n```\n"
                f"Original Flaky Test Method:\n```java\n{sample['orig_test_code']}\n```\n"
                f"Failure Stack / Diagnostic:\n```\n{sample['flaky_error_summary']}\n```\n"
            )
            out_fmt = (
                "First emit a concise `<thought>` block, then emit the repaired code using the exact markers below:\n"
                "<thought>\n"
                "Root Cause Category: <e.g., Unordered Collection/JSON Iteration | Shared Mutable State | ...>\n"
                "Explanation: <1-2 sentences tying the failure stack to the focal production functions>\n"
                "Fixing Plan: <concise plan preserving intended test semantics>\n"
                "</thought>\n"
                "//<fix start>\n...complete Java test method...\n//<fix end>\n"
                "//<import start>\n//<import end>\n"
                "<!-- <pom.xml start> -->\n<!-- <pom.xml end> -->"
            )
        else:
            test_ctx = (
                f"Victim Flaky Test (`OD`): `{sample['test_full_name']}`\n"
                f"Polluter Test: `{sample['polluter_full_name']}`\n"
                f"Shared Class Context (Imports, Fields, @Before/@After):\n```java\n{sample['context_header']}\n```\n"
                f"Original Victim Test Method:\n```java\n{sample['orig_test_code']}\n```\n"
                f"Original Polluter Test Method:\n```java\n{sample['orig_polluter_code']}\n```\n"
                f"Order-Execution (`polluter -> victim`) Failure Diagnostic:\n```\n{sample['flaky_error_summary']}\n```\n"
            )
            out_fmt = (
                "First emit a concise `<thought>` block, then emit the repaired code using the exact markers below:\n"
                "<thought>\n"
                "Root Cause Category: <e.g., Shared Static/Singleton State Pollution | Missing Fixture Reset | ...>\n"
                "Explanation: <1-2 sentences tying the polluter->victim interaction to the focal production functions>\n"
                "Fixing Plan: <concise plan isolating state cleanly without breaking assertions>\n"
                "</thought>\n"
                "//<victim start>\n...complete Java victim method...\n//<victim end>\n"
                "//<polluter start>\n...complete Java polluter method (repeat unchanged if only editing victim)...\n//<polluter end>\n"
                "//<import start>\n//<import end>\n"
                "<!-- <pom.xml start> -->\n<!-- <pom.xml end> -->"
            )
    else:
        od_sub = sample.get("od_subtype", "OD-victim")
        p_lbl = "State-Setter Test" if od_sub == "OD-brittle" else "Polluter Test"
        test_ctx = (
            f"Target Flaky Test (`{od_sub}`): `{sample['test_full_name']}`\n"
            f"{p_lbl}: `{sample['polluter_full_name']}`\n"
            f"Module Imports & Class/Fixture Context:\n```python\n{sample['context_header']}\n```\n"
            f"Original Target Test Function:\n```python\n{sample['orig_test_code']}\n```\n"
            f"Original {p_lbl} Function:\n```python\n{sample['orig_polluter_code']}\n```\n"
            f"Failure Diagnostic:\n```\n{sample['flaky_error_summary']}\n```\n"
        )
        out_fmt = (
            "First emit a concise `<thought>` block, then emit the repaired Python code using the exact markers below:\n"
            "<thought>\n"
            "Root Cause Category: <e.g., Shared Module/Class State Pollution | Missing Prerequisite Setup | ...>\n"
            "Explanation: <1-2 sentences tying the failure to the focal production functions>\n"
            "Fixing Plan: <concise plan fixing order-dependence while keeping intended checks>\n"
            "</thought>\n"
            "#<victim start>\n...complete Python target test function...\n#<victim end>\n"
            f"#<polluter start>\n...complete Python {p_lbl.lower()} function (repeat unchanged if only editing target)...\n#<polluter end>\n"
            "#<import start>\n#<import end>"
        )

    refl_block = ""
    if reflection_history:
        items = []
        for h in reflection_history:
            items.append(
                f"--- Attempt #{h['round']} ---\n"
                f"Previous Thought & Plan: {h['thought']}\n"
                f"Previous Candidate Patch:\n```{fence}\n{h['cand_preview']}\n```\n"
                f"Reflection / Validation Failure:\n{h['feedback']}\n"
            )
        refl_block = (
            "\n=== FAILED STRATEGIES & REFLECTION HISTORY (DO NOT REPEAT FAILED PLANS) ===\n"
            + "\n".join(items)
            + "\nReflect carefully on why the previous attempt(s) failed and synthesize a corrected fix.\n"
        )

    contract_extra = ""
    if with_witness_contract:
        contract_extra = "\n" + (PROMPT_GUARD_CONTRACT_PY if lang == "python" else PROMPT_GUARD_CONTRACT) + "\n"

    return (
        f"You are FlakyGuard, an autonomous flaky-test repair agent analyzing both test code and focal production functions.\n"
        "Repair the flaky test so it compiles and passes all stability schedules while preserving the test's intended verification semantics (do not delete or hollow out assertions).\n\n"
        + test_ctx
        + "\n"
        + dcg_section
        + contract_extra
        + refl_block
        + "\n"
        + out_fmt
    )


def format_cand_preview(sample: Dict[str, Any], cand: Dict[str, Any]) -> str:
    if sample["language"] == "java" and sample["test_type"] == "ID":
        return (cand.get("test_code") or "")[:1800]
    v = (cand.get("victim_code") or "")[:1400]
    p = (cand.get("polluter_code") or "")[:800]
    return f"Target/Victim:\n{v}" + (f"\n\nPartner:\n{p}" if p else "")


def run_flakyguard_and_muguard_combo(
    sample: Dict[str, Any],
    dcg_entry: Dict[str, Any],
    bench_sha: str,
    model: str,
    ro_llm_cache: Dict[str, Any],
    rw_llm_cache: Dict[str, Any],
    ro_eval_cache: Dict[str, Any],
    rw_eval_cache: Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Runs `S3_FlakyGuard` and `S5_FlakyGuard_Plus_MuGuard` with STRICT PREFIX SHARING:
      - If `S3` never passes W+: `S5` is 100% identical (`ABSTAIN_*`).
      - If `S3` passes W+ at `r0` and `gate_safe == True`: `S5` accepts the exact same patch at `r0` (`SAFE_PASS`).
      - If `S3` passes W+ at `r0` and `gate_safe == False` (eroded or uncovered partner):
        `S3` accepts at `r0` (exposing FlakyGuard's in-loop erosion), while `S5` forks from `r0`
        by injecting surviving-mutant witnesses into FlakyGuard's Reflection History for rounds `r0+1 .. 5`!
    """
    sid = sample["sample_id"]
    lang = sample["language"]

    s3_rounds: List[Dict[str, Any]] = []
    refl_hist: List[Dict[str, Any]] = []
    s3_acc_round: Optional[int] = None
    s3_final_cand: Optional[Dict[str, Any]] = None
    s3_final_eval: Optional[Dict[str, Any]] = None
    fork_ev_full: Optional[Dict[str, Any]] = None
    fork_cand: Optional[Dict[str, Any]] = None
    fork_thought: str = ""

    for r_idx in range(1, 6):
        prompt = build_flakyguard_prompt(sample, dcg_entry, refl_hist, with_witness_contract=False)
        prompt_sha = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]
        llm_resp, llm_cached = call_llm_v31(model, prompt, f"v31_flakyguard_{sid}_r{r_idx}", ro_llm_cache, rw_llm_cache)
        thought_str = extract_thought_summary(llm_resp["content"])
        cand = parse_fair_response(llm_resp["content"], sample)
        cand_fp = candidate_fingerprint(cand)

        san_ok, san_errs = check_basic_sanity_any(sample, cand)
        if not san_ok:
            fb = "SYNTAX/SANITY REJECTION:\n- " + "\n- ".join(san_errs)
            refl_hist.append({"round": r_idx, "thought": thought_str, "cand_preview": format_cand_preview(sample, cand), "feedback": fb})
            s3_rounds.append({
                "round": r_idx, "prompt_sha": prompt_sha, "candidate_fp": cand_fp, "thought": thought_str,
                "llm_cached": llm_cached, "eval_cached": False, "llm_sec": llm_resp["latency_sec"],
                "ast_rejected": True, "ast_violations": san_errs, "candidate": cand, "eval": None,
                "decision": "SANITY_REJECT_RETRY" if r_idx < 5 else "SANITY_REJECT_FINAL",
            })
            continue

        ev_full, eval_cached = remote_eval_fast(sample, cand, bench_sha, ro_eval_cache, rw_eval_cache, "in_loop_gate")
        ev_comp = compact_eval_dict(ev_full)

        if not ev_full["compiled"]:
            fb = f"COMPILATION / SYNTAX FAILED:\n```\n{ev_full['compile_error']}\n```"
            refl_hist.append({"round": r_idx, "thought": thought_str, "cand_preview": format_cand_preview(sample, cand), "feedback": fb})
            s3_rounds.append({
                "round": r_idx, "prompt_sha": prompt_sha, "candidate_fp": cand_fp, "thought": thought_str,
                "llm_cached": llm_cached, "eval_cached": eval_cached, "llm_sec": llm_resp["latency_sec"],
                "ast_rejected": False, "candidate": cand, "eval": ev_comp,
                "decision": "COMPILE_RETRY" if r_idx < 5 else "ABSTAIN_COMPILE",
            })
            continue

        if not ev_full["w_plus_passed"]:
            fb = f"STABILITY VALIDATION (W+) FAILED:\n```\n{ev_full['w_plus_diag']}\n```"
            refl_hist.append({"round": r_idx, "thought": thought_str, "cand_preview": format_cand_preview(sample, cand), "feedback": fb})
            s3_rounds.append({
                "round": r_idx, "prompt_sha": prompt_sha, "candidate_fp": cand_fp, "thought": thought_str,
                "llm_cached": llm_cached, "eval_cached": eval_cached, "llm_sec": llm_resp["latency_sec"],
                "ast_rejected": False, "candidate": cand, "eval": ev_comp,
                "decision": "UNSTABLE_RETRY" if r_idx < 5 else "ABSTAIN_UNSTABLE",
            })
            continue

        # Candidate compiled AND passed W+ -> FlakyGuard (S3) accepts immediately!
        s3_acc_round = r_idx
        s3_final_cand = cand
        s3_final_eval = ev_comp
        fork_ev_full = ev_full
        fork_cand = cand
        fork_thought = thought_str
        s3_rounds.append({
            "round": r_idx, "prompt_sha": prompt_sha, "candidate_fp": cand_fp, "thought": thought_str,
            "llm_cached": llm_cached, "eval_cached": eval_cached, "llm_sec": llm_resp["latency_sec"],
            "ast_rejected": False, "candidate": cand, "eval": ev_comp, "decision": "ACCEPTED",
        })
        break

    s3_rec = finalize_arm_record("S3_FlakyGuard", s3_rounds, s3_acc_round, s3_final_cand, s3_final_eval)

    # Strict Prefix Sharing for S5_FlakyGuard_Plus_MuGuard!
    if s3_acc_round is None or fork_ev_full is None or fork_cand is None:
        # Case A: S3 never passed W+ -> S5 trajectory is 100% identical!
        s5_rounds = copy.deepcopy(s3_rounds)
        s5_rec = finalize_arm_record("S5_FlakyGuard_Plus_MuGuard", s5_rounds, None, None, None, {"prefix_shared_with_S3": True, "forked_from_S3": False})
        return s3_rec, s5_rec

    if fork_ev_full.get("gate_safe"):
        # Case B: S3 passed W+ at r0 AND preserved 100% joint obligations -> S5 accepts identically at r0!
        s5_rounds = copy.deepcopy(s3_rounds)
        s5_rounds[-1]["decision"] = "SAFE_PASS"
        s5_rec = finalize_arm_record("S5_FlakyGuard_Plus_MuGuard", s5_rounds, s3_acc_round, s3_final_cand, s3_final_eval, {"prefix_shared_with_S3": True, "forked_from_S3": False})
        return s3_rec, s5_rec

    # Case C: S3 passed W+ at r0 (`s3_acc_round`), BUT failed MuGuard-Flake's conservation gate!
    s5_rounds = copy.deepcopy(s3_rounds)
    r0 = s3_acc_round
    override0 = fork_ev_full.get("gate_status_override")

    def _make_gate_rejection(ev_f: Dict[str, Any], round_num: int) -> Tuple[str, str]:
        ov = ev_f.get("gate_status_override")
        if ov == "ABSTAIN_PARTNER_UNCOVERED":
            return (
                "PARTNER_UNCOVERED_RETRY" if round_num < 5 else "ABSTAIN_PARTNER_UNCOVERED",
                "PARTNER SCOPE UNVERIFIABLE: The partner test has zero baseline focal mutation obligations. "
                "Leave the partner test completely unchanged and isolate/reset state strictly inside the target test!",
            )
        if not ev_f.get("pit_ran") or ov == "PARTNER_MUT_EVAL_ERROR" or ev_f.get("joint_retention") is None:
            return (
                "EVAL_ERROR_RETRY" if round_num < 5 else "ABSTAIN_EVAL_ERROR",
                f"FOCAL MUTATION PROBE ERROR:\n```\n{ev_f.get('w_plus_diag', '')}\n```",
            )
        surv = ev_f.get("survived_mutants", [])
        surv_ids = {m["id"] for m in surv}
        non_surv = [m for m in sample["m_det"] if m["id"] not in surv_ids]
        fb_obj = build_matched_ablation_feedback(
            language=lang,
            total_obligations=ev_f["joint_m_det_total"],
            survived_mutants=surv,
            non_survived_pool=non_surv,
            mode="A3_TrueWitness",
        )
        return ("EROSION_RETRY" if round_num < 5 else "ABSTAIN_EROSION", fb_obj["text"])

    dec0, witness_fb0 = _make_gate_rejection(fork_ev_full, r0)
    s5_rounds[-1]["decision"] = dec0
    if r0 >= 5:
        s5_rec = finalize_arm_record("S5_FlakyGuard_Plus_MuGuard", s5_rounds, None, None, None, {"prefix_shared_with_S3": True, "forked_from_S3": True, "fork_round": r0})
        return s3_rec, s5_rec

    # Fork from r0: append witness counterexample to Reflection History and continue r = r0+1 .. 5!
    s5_refl = copy.deepcopy(refl_hist)
    s5_refl.append({
        "round": r0,
        "thought": fork_thought,
        "cand_preview": format_cand_preview(sample, fork_cand),
        "feedback": witness_fb0,
    })
    s5_acc_round: Optional[int] = None
    s5_final_cand: Optional[Dict[str, Any]] = None
    s5_final_eval: Optional[Dict[str, Any]] = None

    for r_idx in range(r0 + 1, 6):
        prompt = build_flakyguard_prompt(sample, dcg_entry, s5_refl, with_witness_contract=True)
        prompt_sha = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]
        llm_resp, llm_cached = call_llm_v31(model, prompt, f"v31_fg_muguard_fork_{sid}_r{r_idx}", ro_llm_cache, rw_llm_cache)
        thought_str = extract_thought_summary(llm_resp["content"])
        cand = parse_fair_response(llm_resp["content"], sample)
        cand_fp = candidate_fingerprint(cand)

        san_ok, san_errs = check_basic_sanity_any(sample, cand)
        if not san_ok:
            fb = "SYNTAX/SANITY REJECTION:\n- " + "\n- ".join(san_errs)
            s5_refl.append({"round": r_idx, "thought": thought_str, "cand_preview": format_cand_preview(sample, cand), "feedback": fb})
            s5_rounds.append({
                "round": r_idx, "prompt_sha": prompt_sha, "candidate_fp": cand_fp, "thought": thought_str,
                "llm_cached": llm_cached, "eval_cached": False, "llm_sec": llm_resp["latency_sec"],
                "ast_rejected": True, "ast_violations": san_errs, "candidate": cand, "eval": None,
                "decision": "SANITY_REJECT_RETRY" if r_idx < 5 else "SANITY_REJECT_FINAL",
            })
            continue

        ev_full, eval_cached = remote_eval_fast(sample, cand, bench_sha, ro_eval_cache, rw_eval_cache, "in_loop_gate")
        ev_comp = compact_eval_dict(ev_full)

        if not ev_full["compiled"]:
            fb = f"COMPILATION / SYNTAX FAILED:\n```\n{ev_full['compile_error']}\n```"
            s5_refl.append({"round": r_idx, "thought": thought_str, "cand_preview": format_cand_preview(sample, cand), "feedback": fb})
            s5_rounds.append({
                "round": r_idx, "prompt_sha": prompt_sha, "candidate_fp": cand_fp, "thought": thought_str,
                "llm_cached": llm_cached, "eval_cached": eval_cached, "llm_sec": llm_resp["latency_sec"],
                "ast_rejected": False, "candidate": cand, "eval": ev_comp,
                "decision": "COMPILE_RETRY" if r_idx < 5 else "ABSTAIN_COMPILE",
            })
            continue

        if not ev_full["w_plus_passed"]:
            fb = f"STABILITY VALIDATION (W+) FAILED:\n```\n{ev_full['w_plus_diag']}\n```"
            s5_refl.append({"round": r_idx, "thought": thought_str, "cand_preview": format_cand_preview(sample, cand), "feedback": fb})
            s5_rounds.append({
                "round": r_idx, "prompt_sha": prompt_sha, "candidate_fp": cand_fp, "thought": thought_str,
                "llm_cached": llm_cached, "eval_cached": eval_cached, "llm_sec": llm_resp["latency_sec"],
                "ast_rejected": False, "candidate": cand, "eval": ev_comp,
                "decision": "UNSTABLE_RETRY" if r_idx < 5 else "ABSTAIN_UNSTABLE",
            })
            continue

        if ev_full.get("gate_safe"):
            s5_acc_round = r_idx
            s5_final_cand = cand
            s5_final_eval = ev_comp
            s5_rounds.append({
                "round": r_idx, "prompt_sha": prompt_sha, "candidate_fp": cand_fp, "thought": thought_str,
                "llm_cached": llm_cached, "eval_cached": eval_cached, "llm_sec": llm_resp["latency_sec"],
                "ast_rejected": False, "candidate": cand, "eval": ev_comp, "decision": "SAFE_PASS",
            })
            break

        dec_r, w_fb_r = _make_gate_rejection(ev_full, r_idx)
        s5_refl.append({"round": r_idx, "thought": thought_str, "cand_preview": format_cand_preview(sample, cand), "feedback": w_fb_r})
        s5_rounds.append({
            "round": r_idx, "prompt_sha": prompt_sha, "candidate_fp": cand_fp, "thought": thought_str,
            "llm_cached": llm_cached, "eval_cached": eval_cached, "llm_sec": llm_resp["latency_sec"],
            "ast_rejected": False, "candidate": cand, "eval": ev_comp, "decision": dec_r,
        })
        if r_idx >= 5:
            break

    s5_rec = finalize_arm_record(
        "S5_FlakyGuard_Plus_MuGuard",
        s5_rounds,
        s5_acc_round,
        s5_final_cand,
        s5_final_eval,
        {"prefix_shared_with_S3": True, "forked_from_S3": True, "fork_round": r0},
    )
    return s3_rec, s5_rec


def run_eval_mode(model: str, workers: int) -> None:
    bm = json.loads((DATA_DIR / "v2_frozen_benchmark_light.json").read_text(encoding="utf-8"))
    all_samples = bm["samples"]
    verify_flakyfix_zero_leakage(all_samples)
    log("[ZERO-LEAKAGE CHECK] Verified 0 repo/method/code overlaps across all 52 FlakyFix few-shot examples and 208 samples!")

def deploy_and_verify_remote_evaluator() -> None:
    """Establish the single persistent SSH channel, atomically syncing all 10 scripts + verifying SHA-256 in 1 handshake."""
    REMOTE_CHANNEL._ensure()
    log(
        f"[DEPLOY & CHANNEL VERIFIED] remote_eval_v2={REMOTE_CHANNEL.deployed_sha.get('remote_eval_v2.py','')[:12]} "
        f"safe_eval={REMOTE_CHANNEL.deployed_sha.get('ft1_safe_eval.sh','')[:12]} "
        f"verifier={REMOTE_CHANNEL.deployed_sha.get('ft1_verify_and_restore_repo.py','')[:12]} "
        f"remote_core={REMOTE_CHANNEL.deployed_sha.get('remote_core.py','')[:12]}"
    )


def run_eval_mode(model: str, workers: int) -> None:
    bm = json.loads((DATA_DIR / "v2_frozen_benchmark_light.json").read_text(encoding="utf-8"))
    all_samples = bm["samples"]
    verify_flakyfix_zero_leakage(all_samples)
    log("[ZERO-LEAKAGE CHECK] Verified 0 repo/method/code overlaps across all 52 FlakyFix few-shot examples and 208 samples!")

    assert DCG_PATH.exists(), f"Missing {DCG_PATH}; run Phase B first!"
    dcg_store = json.loads(DCG_PATH.read_text(encoding="utf-8"))
    dcg_samples = dcg_store.get("samples") or {}
    raw_dcg_path = DATA_DIR / "v31_raw_dcg_graphs_cache.json"
    raw_dcg_store = json.loads(raw_dcg_path.read_text(encoding="utf-8")) if raw_dcg_path.exists() else {}
    bc_samples = json.loads((DATA_DIR / "v3_baseline_coverage.json").read_text(encoding="utf-8"))["samples"]
    elig = [s for s in all_samples if s.get("v2_eligible")]
    assert len(elig) == 203, f"Expected 203 eligible tests, got {len(elig)}"
    s_by_id = {s["sample_id"]: s for s in elig}
    affected_dcg_sids = compute_mext_affected_dcg_sids(elig, bc_samples, build_sample_covered_map)
    quarantined = get_quarantined_projects(DATA_DIR)

    deploy_and_verify_remote_evaluator()

    bench_sha = bm["exec_benchmark_sha256"]
    _, ro_llm_cache = load_disk_llm_cache(model)
    rw_llm_cache = json.loads(V31_LLM_CACHE_PATH.read_text(encoding="utf-8")) if V31_LLM_CACHE_PATH.exists() else {}
    _, ro_eval_cache = load_v2_eval_cache()
    rw_eval_cache = json.loads(V31_EVAL_CACHE_PATH.read_text(encoding="utf-8")) if V31_EVAL_CACHE_PATH.exists() else {}

    completed_map: Dict[str, Any] = {}
    purged_reasons: Dict[str, str] = {}
    if SOTA_OUT_PATH.exists():
        old = json.loads(SOTA_OUT_PATH.read_text(encoding="utf-8"))
        for r in old.get("results", []):
            sid = r.get("sample_id", "")
            smp = s_by_id.get(sid)
            if not smp or not set(ALL_SOTA_ARMS).issubset(set((r.get("strategies") or {}).keys())):
                continue
            exp_s3_r1_sha = (
                hashlib.sha256(
                    build_flakyguard_prompt(smp, dcg_samples[sid], [], with_witness_contract=False).encode("utf-8")
                ).hexdigest()[:16]
                if sid in dcg_samples
                else ""
            )
            ok_rec, reason = is_checkpoint_record_eligible(
                rec=r,
                sample=smp,
                dcg_store=dcg_store,
                raw_dcg_store=raw_dcg_store,
                bc_samples=bc_samples,
                affected_sids=affected_dcg_sids,
                build_cov_fn=build_sample_covered_map,
                expected_s3_r1_prompt_sha=exp_s3_r1_sha,
                quarantined_projects=quarantined,
            )
            if ok_rec:
                if not r.get("dcg_context_sha256"):
                    r["dcg_context_sha256"] = dcg_entry_fingerprint(dcg_samples[sid])
                r["dcg_mext_free"] = True
                completed_map[sid] = r
            else:
                purged_reasons[sid] = reason
    if purged_reasons:
        log(f"[INTEGRITY] Purged {len(purged_reasons)} uncertified/stale checkpoint records for replay: {purged_reasons}")

    ordered_ids = [s["sample_id"] for s in elig]
    todo: List[Dict[str, Any]] = []
    unscheduled_dcg: Dict[str, str] = {}
    quarantined_skipped: List[str] = []
    for s in elig:
        sid = s["sample_id"]
        if sid in completed_map:
            continue
        if s["project_name"].lower() in quarantined:
            quarantined_skipped.append(sid)
            continue
        dcg_ok, dcg_reason = is_sample_dcg_certified(
            s, dcg_store, raw_dcg_store, bc_samples, affected_dcg_sids, build_sample_covered_map
        )
        if not dcg_ok:
            unscheduled_dcg[sid] = dcg_reason
            continue
        todo.append(s)

    if unscheduled_dcg:
        log(f"[INTEGRITY] Holding {len(unscheduled_dcg)} samples unscheduled pending M_ext-free DCG rebuild: {unscheduled_dcg}")
    log(
        f"[PHASE C SOTA {model}] {len(completed_map)}/203 certified; running {len(todo)} eligible "
        f"(unscheduled_dcg={len(unscheduled_dcg)}, quarantined={quarantined_skipped}) with {workers} workers..."
    )

    def _save_progress() -> None:
        ordered = [completed_map[sid] for sid in ordered_ids if sid in completed_map]
        save_json_atomic(
            SOTA_OUT_PATH,
            {
                "protocol_version": "3.1.0-same-model-sota-head2head",
                "model": model,
                "exec_benchmark_sha256": bench_sha,
                "total_eligible": 203,
                "completed_count": len(ordered),
                "arms": ALL_SOTA_ARMS,
                "results": ordered,
            },
        )

    def _process_one(smp: Dict[str, Any]) -> Dict[str, Any]:
        sid = smp["sample_id"]
        if STOP_EVENT.is_set():
            raise AccountExhausted("global stop requested")
        dcg_ok, dcg_reason = is_sample_dcg_certified(
            smp, dcg_store, raw_dcg_store, bc_samples, affected_dcg_sids, build_sample_covered_map
        )
        if not dcg_ok:
            raise RuntimeError(f"Refusing to run {sid} with uncertified DCG context: {dcg_reason}")
        cur_dcg_fp = dcg_entry_fingerprint(dcg_samples[sid])
        s1_k1, s1b_k5 = run_flakyfix_arms(smp, bench_sha, model, ro_llm_cache, rw_llm_cache, ro_eval_cache, rw_eval_cache)
        s2_fd = run_flakydoctor_arm(smp, bench_sha, model, ro_llm_cache, rw_llm_cache, ro_eval_cache, rw_eval_cache)
        s4_mg = run_muguard_testside_arm(smp, bench_sha, model, ro_llm_cache, rw_llm_cache, ro_eval_cache, rw_eval_cache)
        s3_fg, s5_fg_mg = run_flakyguard_and_muguard_combo(
            smp, dcg_samples[sid], bench_sha, model, ro_llm_cache, rw_llm_cache, ro_eval_cache, rw_eval_cache
        )
        log(
            f"  [DONE {sid}] "
            f"FF1:sf={int(s1_k1['sound_fix'])}(er={int(s1_k1['eroded'])}) | "
            f"FF5:sf={int(s1b_k5['sound_fix'])}(er={int(s1b_k5['eroded'])}) | "
            f"FD:sf={int(s2_fd['sound_fix'])}(er={int(s2_fd['eroded'])}) | "
            f"FG:sf={int(s3_fg['sound_fix'])}(er={int(s3_fg['eroded'])}) | "
            f"MG:sf={int(s4_mg['sound_fix'])} | "
            f"FG+MG:sf={int(s5_fg_mg['sound_fix'])}"
        )
        return {
            "sample_id": sid,
            "project_name": smp["project_name"],
            "language": smp["language"],
            "test_type": smp["test_type"],
            "od_subtype": smp.get("od_subtype", smp["test_type"]),
            "v2_eligible": True,
            "exec_benchmark_sha256": bench_sha,
            "integrity_certified": True,
            "evaluator_sha256": REMOTE_CHANNEL.deployed_sha.get("remote_eval_v2.py", ""),
            "safe_eval_wrapper_sha256": REMOTE_CHANNEL.deployed_sha.get("ft1_safe_eval.sh", ""),
            "baseline_verifier_sha256": REMOTE_CHANNEL.deployed_sha.get("ft1_verify_and_restore_repo.py", ""),
            "dcg_mext_free": bool(dcg_ok),
            "dcg_context_sha256": cur_dcg_fp,
            "strategies": {
                "S1_FlakyFix_K1": s1_k1,
                "S1b_FlakyFix_K5": s1b_k5,
                "S2_FlakyDoctor": s2_fd,
                "S3_FlakyGuard": s3_fg,
                "S4_MuGuard_Flake": s4_mg,
                "S5_FlakyGuard_Plus_MuGuard": s5_fg_mg,
            },
        }

    if todo:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(_process_one, smp): smp["sample_id"] for smp in todo}
            done_cnt = 0
            for fut in as_completed(futs):
                sid = futs[fut]
                try:
                    rec = fut.result()
                except Exception as e:
                    log(f"  [ERR {sid}] {type(e).__name__}: {str(e)[:300]}")
                    if STOP_EVENT.is_set():
                        log("=== [FAIL-FAST] API account exhausted: cancelling all pending samples ===")
                        for f2 in futs:
                            f2.cancel()
                    continue
                with SAVE_LOCK:
                    completed_map[sid] = rec
                    done_cnt += 1
                    _save_progress()
                    if done_cnt % 10 == 0 or done_cnt == len(todo):
                        log(f"=== [PROGRESS] {len(completed_map)}/203 SOTA samples saved ===")

    with V2_CACHE_LOCK:
        save_json_atomic(V31_EVAL_CACHE_PATH, rw_eval_cache)
    with CACHE_LOCK:
        save_json_atomic(V31_LLM_CACHE_PATH, rw_llm_cache)
    _save_progress()

    # Quick operational summary printout
    assert len(completed_map) == 203, f"Incomplete SOTA run: {len(completed_map)}/203"
    print("\n=== PHASE C OPERATIONAL SUMMARY (N = 203, Qwen 3.7 Plus) ===", flush=True)
    for arm in ALL_SOTA_ARMS:
        w_cnt = sum(1 for r in completed_map.values() if r["strategies"][arm]["w_plus_passed"])
        er_cnt = sum(1 for r in completed_map.values() if r["strategies"][arm]["eroded"])
        sf_cnt = sum(1 for r in completed_map.values() if r["strategies"][arm]["sound_fix"])
        lk_pct = (100.0 * er_cnt / w_cnt) if w_cnt else 0.0
        print(
            f"  {arm:28s}: Pass@W+={w_cnt:3d}/203 ({w_cnt/203*100:5.2f}%) | "
            f"Leaked={er_cnt:2d} ({lk_pct:5.2f}%) | SoundFix={sf_cnt:3d}/203 ({sf_cnt/203*100:5.2f}%)",
            flush=True,
        )


def run_heldout_mode(workers: int) -> None:
    bm = json.loads((DATA_DIR / "v2_frozen_benchmark_light.json").read_text(encoding="utf-8"))
    bench_sha = bm["exec_benchmark_sha256"]
    s_by_id = {s["sample_id"]: s for s in bm["samples"]}

    assert SOTA_OUT_PATH.exists(), f"Missing {SOTA_OUT_PATH}"
    sota_results = json.loads(SOTA_OUT_PATH.read_text(encoding="utf-8"))["results"]
    assert len(sota_results) == 203, f"Expected 203 results in {SOTA_OUT_PATH}"

    # Shared eligibility-filtered heldout loader (`R3-5`): excludes implicated repos without `v31_safe_eval:` provenance
    from v31_adjudicate_and_analyze_sota import load_majority_labels
    existing_labels = load_majority_labels()
    known_ho = load_eligible_heldout_map(DATA_DIR, SOTA_HO_PATH, existing_wsl_labels=existing_labels)

    _, ro_eval_cache = load_v2_eval_cache()
    rw_eval_cache = json.loads(V31_EVAL_CACHE_PATH.read_text(encoding="utf-8")) if V31_EVAL_CACHE_PATH.exists() else {}

    needed_jobs: List[Tuple[str, str, Dict[str, Any], Dict[str, Any]]] = []
    seen_ck = set()
    all_req_cks = set()

    for r in sota_results:
        sid = r["sample_id"]
        smp = s_by_id[sid]
        if smp.get("m_ext_total_count", 0) <= 0:
            continue
        for arm in ALL_SOTA_ARMS:
            st = r["strategies"][arm]
            if not st.get("accepted") or not st.get("final_candidate"):
                continue
            cand = st["final_candidate"]
            cfp = candidate_fingerprint(cand)
            ck = f"{sid}::{cfp}"
            all_req_cks.add(ck)
            if ck not in known_ho and ck not in seen_ck:
                seen_ck.add(ck)
                needed_jobs.append((ck, sid, smp, cand))

    log(f"[PHASE D HELDOUT] Total unique accepted (sid::fp) in |E|=177: {len(all_req_cks)}; cached={len(all_req_cks)-len(needed_jobs)}; new_to_run={len(needed_jobs)}")

    out_evals = {ck: known_ho[ck] for ck in all_req_cks if ck in known_ho}

    def _save_ho() -> None:
        save_json_atomic(
            SOTA_HO_PATH,
            {
                "protocol_version": "3.1.0-sota-post-freeze-heldout",
                "exec_benchmark_sha256": bench_sha,
                "total_unique_accepted_in_E": len(all_req_cks),
                "completed_count": len(out_evals),
                "evaluations": out_evals,
            },
        )

    if needed_jobs:
        deploy_and_verify_remote_evaluator()

        def _eval_ho(job: Tuple[str, str, Dict[str, Any], Dict[str, Any]]) -> Tuple[str, Dict[str, Any]]:
            ck, sid, smp, cand = job
            ev, _ = remote_eval_fast(smp, cand, bench_sha, ro_eval_cache, rw_eval_cache, "post_freeze_heldout")
            ev_slim = dict(ev)
            log(
                f"  [HO DONE {ck}] w_ho={ev_slim.get('w_plus_heldout_passed_strict', ev_slim.get('w_plus_heldout_passed'))} "
                f"m_ext_ret={ev_slim.get('m_ext_all_retention')} prov={str(ev_slim.get('_provenance',''))[:20]}"
            )
            return ck, ev_slim

        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(_eval_ho, jb): jb[0] for jb in needed_jobs}
            done_n = 0
            for fut in as_completed(futs):
                ck, ev_slim = fut.result()
                with SAVE_LOCK:
                    out_evals[ck] = ev_slim
                    done_n += 1
                    _save_ho()
                    if done_n % 10 == 0 or done_n == len(needed_jobs):
                        log(f"=== [HO PROGRESS] {done_n}/{len(needed_jobs)} new held-out evaluations saved ===")

    with V2_CACHE_LOCK:
        save_json_atomic(V31_EVAL_CACHE_PATH, rw_eval_cache)
    _save_ho()
    log(f"=== [PHASE D HELDOUT COMPLETE] {len(out_evals)}/{len(all_req_cks)} held-out evaluations ready! ===")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["eval", "heldout", "both"], default="both")
    ap.add_argument("--model", default="qwen3.7-plus")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    if args.mode in ("eval", "both"):
        run_eval_mode(args.model, args.workers)
    if args.mode in ("heldout", "both"):
        run_heldout_mode(args.workers)


if __name__ == "__main__":
    main()
