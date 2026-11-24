import argparse
import base64
import hashlib
import json
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ast_guard_and_prompts import (
    CATCH_ASSERTION_ERR_RE,
    CATCH_SWALLOW_RE,
    IGNORE_RE,
    build_repair_prompt,
    check_static_ast_guard,
    format_mutant_witness_feedback,
    is_non_empty_method,
    parse_llm_patch_response,
    strip_comments_and_strings,
)
from python_ast_guard_and_prompts import (
    build_py_repair_prompt,
    check_py_basic_sanity,
    check_py_static_ast_guard,
    format_py_mutant_witness_feedback,
    parse_py_llm_patch_response,
)
from remote_and_llm import DATA_DIR, chat_completion, run_ssh, scp_to_remote

# Strictly <= 3 concurrent Docker containers on remote server (3 * 2.5 = 7.5 vCPU <= 8 vCPU, 3 * 4 GB = 12 GB <= 14 GB)
REMOTE_EVAL_SEM = threading.Semaphore(3)
PROJ_LOCKS: Dict[str, threading.Lock] = defaultdict(threading.Lock)
SAVE_LOCK = threading.Lock()
CACHE_LOCK = threading.Lock()

STRATEGIES = [
    "B0_Standard_Validator",
    "B2_Prompt_Guard",
    "B3_Static_AST_Guard",
    "Ours_MuGuard_Flake",
]


def log(msg: str) -> None:
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def safe_model_tag(model: str) -> str:
    return model.replace("/", "_").replace(":", "_")


def prompt_cache_key(model: str, prompt: str) -> str:
    return hashlib.sha256((model + "::" + prompt).encode("utf-8")).hexdigest()[:24]


def load_disk_llm_cache(model: str) -> Tuple[Path, Dict[str, Dict[str, Any]]]:
    p = DATA_DIR / f"llm_cache_{safe_model_tag(model)}.json"
    if p.exists():
        try:
            return p, json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return p, {}


def save_disk_llm_cache(cache_path: Path, cache_dict: Dict[str, Dict[str, Any]]) -> None:
    tmp = cache_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cache_dict, ensure_ascii=False), encoding="utf-8")
    tmp.replace(cache_path)


def check_java_basic_sanity(orig_methods: Dict[str, str], cand_methods: Dict[str, str]) -> Tuple[bool, List[str]]:
    errs: List[str] = []
    for mname, orig_src in orig_methods.items():
        c_src = cand_methods.get(mname)
        if not c_src:
            continue
        if not is_non_empty_method(c_src):
            errs.append(f"Method `{mname}` cannot have an empty body.")
            continue
        o_clean = strip_comments_and_strings(orig_src)
        c_clean = strip_comments_and_strings(c_src)
        if IGNORE_RE.search(c_clean) and not IGNORE_RE.search(o_clean):
            errs.append(f"Method `{mname}` cannot add `@Ignore`/`@Disabled` or `assumeTrue(false)`.")
        if CATCH_SWALLOW_RE.search(c_clean) or (
            CATCH_ASSERTION_ERR_RE.search(c_clean) and not CATCH_ASSERTION_ERR_RE.search(o_clean)
        ):
            errs.append(f"Method `{mname}` cannot swallow exceptions or catch `AssertionError`/`Throwable`.")
    return (len(errs) == 0), errs


def build_unified_prompt(
    strategy: str,
    sample: Dict[str, Any],
    round_idx: int,
    prev_candidate: Optional[Dict[str, Any]] = None,
    feedback_msg: str = "",
) -> str:
    if sample.get("language") == "python":
        return build_py_repair_prompt(strategy, sample, round_idx, prev_candidate, feedback_msg)
    return build_repair_prompt(strategy, sample, round_idx, prev_candidate, feedback_msg)


def parse_unified_response(raw_text: str, sample: Dict[str, Any]) -> Dict[str, Any]:
    if sample.get("language") == "python":
        return parse_py_llm_patch_response(raw_text)
    return parse_llm_patch_response(raw_text, sample["test_type"])


def format_unified_witness(
    sample: Dict[str, Any],
    survived_mutants: List[Dict[str, Any]],
    mode: str = "full",
) -> str:
    if sample.get("language") == "python":
        return format_py_mutant_witness_feedback(survived_mutants, mode=mode)
    return format_mutant_witness_feedback(survived_mutants, mode=mode)


def call_llm_cached(
    model: str,
    prompt: str,
    purpose: str,
    disk_cache: Dict[str, Dict[str, Any]],
    cache_path: Path,
) -> Tuple[Dict[str, Any], bool]:
    k = prompt_cache_key(model, prompt)
    with CACHE_LOCK:
        if k in disk_cache and (disk_cache[k].get("content") or "").strip():
            return disk_cache[k], True

    last_exc: Optional[Exception] = None
    for attempt in range(5):
        try:
            eff_max_toks = 16384 if model.lower().startswith("deepseek") else 8192
            if attempt >= 2 and model.lower() == "deepseek-flash":
                eff_max_toks = 24576
            msgs = [{"role": "user", "content": prompt}]
            if attempt == 1 and model.lower().startswith("deepseek"):
                msgs.insert(
                    0,
                    {
                        "role": "system",
                        "content": "Keep internal chain-of-thought concise (<5000 tokens) so the complete patch code block is always emitted in full.",
                    },
                )
            elif attempt >= 2 and model.lower().startswith("deepseek"):
                msgs.insert(
                    0,
                    {
                        "role": "system",
                        "content": "Use minimal chain-of-thought (<2000 tokens) and immediately output the complete required patch markers and code.",
                    },
                )
            resp = chat_completion(
                provider=None,
                model=model,
                messages=msgs,
                temperature=0.0,
                max_tokens=eff_max_toks,
                purpose=purpose,
                timeout=180,
            )
            if not (resp.get("content") or "").strip():
                raise RuntimeError("Empty LLM content (reasoning token cap or truncated stream)")
            slim = {
                "content": resp["content"],
                "latency_sec": resp["latency_sec"],
                "tokens": resp["usage"].get("total_tokens", 0),
            }
            with CACHE_LOCK:
                disk_cache[k] = slim
                save_disk_llm_cache(cache_path, disk_cache)
            return slim, False
        except Exception as e:
            last_exc = e
            err_s = str(e)
            if any(q in err_s for q in ("http_402", "Insufficient Balance", "Arrearage", "good standing")):
                raise
            log(f"  [LLM retry {attempt+1}/5] {model} on {purpose}: {err_s[:140]}")
            time.sleep(3 * (attempt + 1))
    raise last_exc or RuntimeError(f"LLM call failed: {purpose}")


def candidate_fingerprint(cand: Dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(cand, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:18]


def remote_evaluate_unified(sample: Dict[str, Any], cand: Dict[str, Any]) -> Dict[str, Any]:
    b64_cand = base64.b64encode(json.dumps(cand, ensure_ascii=False).encode("utf-8")).decode("ascii")
    sid = sample["sample_id"]
    lang = sample.get("language", "java")
    bench_remote = (
        "full_runs/python_full_benchmark_exec.json"
        if lang == "python"
        else "full_runs/java_full_benchmark.json"
    )
    p_key = f"{lang}:{sample['sha']}:{sample['project_name']}"
    cmd = f"python3 scripts/remote_eval_unified.py '{lang}' '{bench_remote}' '{sid}' '{b64_cand}' 1"

    last_exc: Optional[Exception] = None
    for attempt in range(3):
        try:
            with PROJ_LOCKS[p_key]:
                with REMOTE_EVAL_SEM:
                    rc_code, out, err = run_ssh(cmd, timeout=300)
            s_tag, e_tag = "===FT1_EVAL_JSON_START===", "===FT1_EVAL_JSON_END==="
            if s_tag in out and e_tag in out:
                return json.loads(out.split(s_tag, 1)[1].split(e_tag, 1)[0].strip())
            last_exc = RuntimeError(f"Parse error on {sid} (rc={rc_code}): {out[-350:]} {err[-250:]}")
        except Exception as e:
            last_exc = e
        time.sleep(3 * (attempt + 1))
    raise last_exc or RuntimeError(f"Remote eval failed on {sid}")


def run_strategy_loop(
    strategy: str,
    sample: Dict[str, Any],
    model: str,
    max_rounds: int,
    disk_cache: Dict[str, Dict[str, Any]],
    cache_path: Path,
    eval_cache: Dict[str, Dict[str, Any]],
    witness_mode: str = "full",
) -> Dict[str, Any]:
    lang = sample.get("language", "java")
    ttype = sample["test_type"]
    orig_methods: Dict[str, str] = {"victim": sample["orig_test_code"]}
    if (ttype == "OD" or lang == "python") and sample.get("orig_polluter_code"):
        orig_methods["polluter"] = sample["orig_polluter_code"]

    rounds_trace: List[Dict[str, Any]] = []
    prev_cand: Optional[Dict[str, Any]] = None
    feedback_msg = ""

    accepted_round: Optional[int] = None
    final_eval: Optional[Dict[str, Any]] = None
    final_cand: Optional[Dict[str, Any]] = None
    gate_state = "UNRESOLVED"
    erosion_retries = 0
    ast_rejections = 0

    for r_idx in range(1, max_rounds + 1):
        base_strat = "Ours_MuGuard_Flake" if strategy.startswith("Ablation_") else strategy
        prompt = build_unified_prompt(
            strategy=base_strat,
            sample=sample,
            round_idx=r_idx,
            prev_candidate=prev_cand,
            feedback_msg=feedback_msg,
        )
        llm_resp, llm_cached = call_llm_cached(
            model=model,
            prompt=prompt,
            purpose=f"full_{strategy}_{sample['sample_id']}_r{r_idx}",
            disk_cache=disk_cache,
            cache_path=cache_path,
        )
        cand = parse_unified_response(llm_resp["content"], sample)
        if lang == "java" and ttype == "ID":
            cand_methods = {"victim": cand.get("test_code") or ""}
        else:
            cand_methods = {"victim": cand.get("victim_code") or ""}
            if cand.get("polluter_code"):
                cand_methods["polluter"] = cand["polluter_code"]

        # 1. Pre-execution AST / Sanity Gate
        if base_strat == "B3_Static_AST_Guard":
            ast_ok, ast_errs = (
                check_py_static_ast_guard(orig_methods, cand_methods)
                if lang == "python"
                else check_static_ast_guard(orig_methods, cand_methods)
            )
            if not ast_ok:
                ast_rejections += 1
                feedback_msg = (
                    "STATIC AST GUARD REJECTED YOUR PATCH:\n- "
                    + "\n- ".join(ast_errs)
                    + "\nFix the flakiness while preserving all assertions and avoiding weak checks."
                )
                prev_cand = cand
                rounds_trace.append({
                    "round": r_idx,
                    "llm_cached": llm_cached,
                    "llm_sec": llm_resp["latency_sec"],
                    "ast_rejected": True,
                    "ast_violations": ast_errs,
                    "candidate": cand,
                    "eval": None,
                    "decision": "AST_REJECT_RETRY" if r_idx < max_rounds else "AST_REJECT_FINAL",
                })
                continue
        else:
            san_ok, san_errs = (
                check_py_basic_sanity(orig_methods, cand_methods)
                if lang == "python"
                else check_java_basic_sanity(orig_methods, cand_methods)
            )
            if not san_ok:
                feedback_msg = "SYNTAX/SANITY REJECTION:\n- " + "\n- ".join(san_errs)
                prev_cand = cand
                rounds_trace.append({
                    "round": r_idx,
                    "llm_cached": llm_cached,
                    "llm_sec": llm_resp["latency_sec"],
                    "ast_rejected": True,
                    "ast_violations": san_errs,
                    "candidate": cand,
                    "eval": None,
                    "decision": "SANITY_REJECT_RETRY" if r_idx < max_rounds else "SANITY_REJECT_FINAL",
                })
                continue

        # 2. Remote execution evaluation (compile + W+ + focal M_det)
        c_fp = candidate_fingerprint(cand)
        if c_fp in eval_cache:
            ev = dict(eval_cache[c_fp])
            eval_cached = True
        else:
            ev = remote_evaluate_unified(sample, cand)
            eval_cache[c_fp] = dict(ev)
            eval_cached = False

        ev_compact = dict(ev)
        ev_compact["survived_mutant_ids"] = [m["id"] for m in ev.get("survived_mutants", []) if "id" in m]
        ev_compact["survived_mutant_summaries"] = [
            f"{m['mutatedClass'].split('.')[-1]}.{m['mutatedMethod']}:L{m['lineNumber']} ({m['description']})"
            for m in ev.get("survived_mutants", [])
        ]
        ev_compact.pop("survived_mutants", None)

        if not ev["compiled"]:
            feedback_msg = f"COMPILATION / SYNTAX FAILED:\n```\n{ev['compile_error']}\n```\nFix all errors."
            prev_cand = cand
            rounds_trace.append({
                "round": r_idx,
                "llm_cached": llm_cached,
                "eval_cached": eval_cached,
                "llm_sec": llm_resp["latency_sec"],
                "ast_rejected": False,
                "candidate": cand,
                "eval": ev_compact,
                "decision": "COMPILE_RETRY" if r_idx < max_rounds else "ABSTAIN_COMPILE",
            })
            continue

        if not ev["w_plus_passed"]:
            feedback_msg = (
                f"STABILITY VALIDATION (W+) FAILED:\n```\n{ev['w_plus_diag']}\n```\n"
                "Your patch compiled, but failed NonDex / order execution."
            )
            prev_cand = cand
            rounds_trace.append({
                "round": r_idx,
                "llm_cached": llm_cached,
                "eval_cached": eval_cached,
                "llm_sec": llm_resp["latency_sec"],
                "ast_rejected": False,
                "candidate": cand,
                "eval": ev_compact,
                "decision": "UNSTABLE_RETRY" if r_idx < max_rounds else "ABSTAIN_UNSTABLE",
            })
            continue

        # Candidate compiled AND passed W+!
        if base_strat != "Ours_MuGuard_Flake":
            accepted_round = r_idx
            final_eval = ev_compact
            final_cand = cand
            gate_state = "ACCEPTED_BY_BASELINE_GATE"
            rounds_trace.append({
                "round": r_idx,
                "llm_cached": llm_cached,
                "eval_cached": eval_cached,
                "llm_sec": llm_resp["latency_sec"],
                "ast_rejected": False,
                "candidate": cand,
                "eval": ev_compact,
                "decision": "ACCEPTED",
            })
            break
        else:
            if not ev.get("pit_ran") or ev.get("retention_m_det") is None:
                feedback_msg = (
                    f"FOCAL MUTATION PROBE FAILED TO COMPILE/RUN:\n```\n{ev.get('w_plus_diag', '')}\n```\n"
                    "Ensure your test method syntax and imports compile cleanly in isolation."
                )
                prev_cand = cand
                rounds_trace.append({
                    "round": r_idx,
                    "llm_cached": llm_cached,
                    "eval_cached": eval_cached,
                    "llm_sec": llm_resp["latency_sec"],
                    "ast_rejected": False,
                    "candidate": cand,
                    "eval": ev_compact,
                    "decision": "COMPILE_RETRY" if r_idx < max_rounds else "ABSTAIN_COMPILE",
                })
                continue
            if not ev["eroded"]:
                accepted_round = r_idx
                final_eval = ev_compact
                final_cand = cand
                gate_state = "SAFE_PASS"
                rounds_trace.append({
                    "round": r_idx,
                    "llm_cached": llm_cached,
                    "eval_cached": eval_cached,
                    "llm_sec": llm_resp["latency_sec"],
                    "ast_rejected": False,
                    "candidate": cand,
                    "eval": ev_compact,
                    "decision": "SAFE_PASS",
                })
                break
            else:
                if r_idx < max_rounds and witness_mode != "abstain_only":
                    erosion_retries += 1
                    surv = ev.get("survived_mutants", [])
                    if witness_mode == "placebo":
                        surv_ids = {m["id"] for m in surv}
                        non_surv = [m for m in sample["m_det"] if m["id"] not in surv_ids]
                        placebo_muts = non_surv[: max(1, min(len(surv), 2))] or surv[:1]
                        feedback_msg = format_unified_witness(sample, placebo_muts, mode="placebo")
                    elif witness_mode == "binary":
                        feedback_msg = format_unified_witness(sample, surv, mode="binary")
                    else:
                        feedback_msg = format_unified_witness(sample, surv, mode="full")
                    prev_cand = cand
                    rounds_trace.append({
                        "round": r_idx,
                        "llm_cached": llm_cached,
                        "eval_cached": eval_cached,
                        "llm_sec": llm_resp["latency_sec"],
                        "ast_rejected": False,
                        "candidate": cand,
                        "eval": ev_compact,
                        "decision": "EROSION_RETRY",
                    })
                    continue
                else:
                    gate_state = "ABSTAIN_EROSION"
                    rounds_trace.append({
                        "round": r_idx,
                        "llm_cached": llm_cached,
                        "eval_cached": eval_cached,
                        "llm_sec": llm_resp["latency_sec"],
                        "ast_rejected": False,
                        "candidate": cand,
                        "eval": ev_compact,
                        "decision": "ABSTAIN_EROSION",
                    })
                    break

    if accepted_round is None and gate_state == "UNRESOLVED":
        gate_state = "ABSTAIN_UNSTABLE" if strategy in ("Ours_MuGuard_Flake",) or strategy.startswith("Ablation_") else "FAILED_W_PLUS"

    accepted = accepted_round is not None and final_eval is not None
    w_plus_ok = bool(accepted and final_eval.get("w_plus_passed"))
    pit_ok = bool(accepted and final_eval.get("pit_ran") and final_eval.get("retention_m_det") is not None)
    eroded = bool(accepted and final_eval.get("eroded"))
    sound_fix = bool(accepted and w_plus_ok and pit_ok and not eroded)

    return {
        "strategy": strategy,
        "gate_state": gate_state,
        "accepted": accepted,
        "accepted_round": accepted_round,
        "w_plus_passed": w_plus_ok,
        "eroded": eroded,
        "sound_fix": sound_fix,
        "retention_m_det": final_eval.get("retention_m_det") if final_eval else None,
        "retention_m_gold": final_eval.get("retention_m_gold") if final_eval else None,
        "erosion_retries": erosion_retries,
        "ast_rejections": ast_rejections,
        "final_candidate": final_cand,
        "final_eval": final_eval,
        "rounds": rounds_trace,
    }


def process_one_sample(
    sample: Dict[str, Any],
    model: str,
    max_rounds: int,
    disk_cache: Dict[str, Dict[str, Any]],
    cache_path: Path,
) -> Dict[str, Any]:
    sid = sample["sample_id"]
    t0 = time.time()
    eval_cache: Dict[str, Dict[str, Any]] = {}
    strat_results: Dict[str, Any] = {}
    for strat in STRATEGIES:
        strat_results[strat] = run_strategy_loop(
            strategy=strat,
            sample=sample,
            model=model,
            max_rounds=max_rounds,
            disk_cache=disk_cache,
            cache_path=cache_path,
            eval_cache=eval_cache,
            witness_mode="full",
        )
    b0 = strat_results["B0_Standard_Validator"]
    b3 = strat_results["B3_Static_AST_Guard"]
    ours = strat_results["Ours_MuGuard_Flake"]
    log(
        f"[{model} DONE {sid}] ({time.time()-t0:.1f}s) | "
        f"B0:W+={int(b0['w_plus_passed'])},ret={b0['retention_m_det']} | "
        f"B3:W+={int(b3['w_plus_passed'])},ret={b3['retention_m_det']} | "
        f"Ours:{ours['gate_state']}(r={ours['accepted_round']},ret={ours['retention_m_det']},w_ret={ours['erosion_retries']})"
    )
    return {
        "sample_id": sid,
        "language": sample.get("language", "java"),
        "test_type": sample["test_type"],
        "od_subtype": sample.get("od_subtype", sample["test_type"]),
        "project_name": sample["project_name"],
        "test_full_name": sample["test_full_name"],
        "m_det_count": sample["m_det_count"],
        "m_gold_count": len(sample.get("m_gold_ids", [])),
        "pit_median_sec": sample["pit_median_sec"],
        "strategies": strat_results,
    }


def save_full_results(out_path: Path, model: str, max_rounds: int, completed_map: Dict[str, Any], ordered_ids: List[str]) -> None:
    ordered_list = [completed_map[sid] for sid in ordered_ids if sid in completed_map]
    payload = {
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "model": model,
        "max_rounds": max_rounds,
        "completed_count": len(ordered_list),
        "total_target": len(ordered_ids),
        "results": ordered_list,
    }
    tmp = out_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(out_path)


def select_stratified_subset(samples: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    if limit <= 0 or limit >= len(samples):
        return samples
    # Stratified across Java ID, Java OD-victim, Python OD-victim, Python OD-brittle, round-robin across projects
    buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for s in samples:
        bkey = f"{s.get('language','java')}:{s.get('od_subtype', s['test_type'])}"
        buckets[bkey].append(s)
    selected: List[Dict[str, Any]] = []
    # Interleave by project within each bucket
    for bkey, arr in sorted(buckets.items()):
        quota = max(1, round(limit * len(arr) / len(samples)))
        by_p: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for s in arr:
            by_p[s["project_name"]].append(s)
        picked = []
        while len(picked) < quota and any(by_p.values()):
            for p in sorted(list(by_p.keys())):
                if by_p[p] and len(picked) < quota:
                    picked.append(by_p[p].pop(0))
                if not by_p[p]:
                    del by_p[p]
        selected.extend(picked)
    return selected[:limit]


def run_prewarm_mode(samples: List[Dict[str, Any]], models: List[str], workers: int) -> None:
    """Zero-remote-CPU Round-1 LLM pre-warming across models while remote benchmark builds."""
    for model in models:
        cache_path, disk_cache = load_disk_llm_cache(model)
        tasks: List[Tuple[str, str]] = []
        for s in samples:
            for strat in ("B0_Standard_Validator", "B2_Prompt_Guard"):
                pr = build_unified_prompt(strat, s, round_idx=1)
                if prompt_cache_key(model, pr) not in disk_cache:
                    tasks.append((f"prewarm_{strat}_{s['sample_id']}_r1", pr))
        log(f"[PREWARM {model}] {len(tasks)} uncached Round-1 prompts (already cached: {len(disk_cache)})...")
        if not tasks:
            continue
        done_n = 0
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [
                ex.submit(call_llm_cached, model, pr, purp, disk_cache, cache_path)
                for purp, pr in tasks
            ]
            for f in as_completed(futs):
                try:
                    f.result()
                    done_n += 1
                    if done_n % 20 == 0 or done_n == len(tasks):
                        log(f"  [{model} prewarm] {done_n}/{len(tasks)} done")
                except Exception as e:
                    log(f"  [{model} prewarm error]: {e}")
        save_disk_llm_cache(cache_path, disk_cache)


def run_eval_mode(samples: List[Dict[str, Any]], model: str, max_rounds: int, workers: int) -> None:
    src_dir = Path(__file__).resolve().parent
    for fn in ("remote_core.py", "remote_python_core.py", "remote_eval_unified.py"):
        scp_to_remote(src_dir / fn, f"scripts/{fn}")

    cache_path, disk_cache = load_disk_llm_cache(model)
    ordered_ids = [s["sample_id"] for s in samples]
    out_path = DATA_DIR / f"full_results_{safe_model_tag(model)}.json"

    completed_map: Dict[str, Any] = {}
    # Seed from pilot_results if available
    pilot_p = DATA_DIR / f"pilot_results_{safe_model_tag(model)}.json"
    if pilot_p.exists():
        for r in json.loads(pilot_p.read_text(encoding="utf-8")).get("results", []):
            r.setdefault("language", "java")
            r.setdefault("od_subtype", "OD-victim" if r["test_type"] == "OD" else "ID")
            completed_map[r["sample_id"]] = r
    if out_path.exists():
        for r in json.loads(out_path.read_text(encoding="utf-8")).get("results", []):
            completed_map[r["sample_id"]] = r

    todo = [s for s in samples if s["sample_id"] not in completed_map]
    log(f"[EVAL {model}] {len(completed_map)}/{len(samples)} already completed; running {len(todo)} remaining...")
    save_full_results(out_path, model, max_rounds, completed_map, ordered_ids)

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        fut_map = {
            ex.submit(process_one_sample, smp, model, max_rounds, disk_cache, cache_path): smp["sample_id"]
            for smp in todo
        }
        for fut in as_completed(fut_map):
            sid = fut_map[fut]
            try:
                res = fut.result()
            except Exception as e:
                log(f"[ERROR {model} on {sid}]: {e}")
                continue
            with SAVE_LOCK:
                completed_map[sid] = res
                save_full_results(out_path, model, max_rounds, completed_map, ordered_ids)
    save_disk_llm_cache(cache_path, disk_cache)
    log(f"=== EVAL COMPLETE ({model}) in {(time.time()-t0)/60:.1f} min -> {out_path} ===")


def run_ablation_mode(samples_by_id: Dict[str, Dict[str, Any]], models: List[str], max_rounds: int, workers: int) -> None:
    """
    SQJ N14-style 4-way Diagnostic Control Ablation on all (model, sample) instances
    where MuGuard-Flake triggered EROSION_RETRY (`erosion_retries >= 1`):
      - A0_Gate_Only_Abstain (abstain_only, K=1)
      - A1_Binary_Score_Drop (binary score notification, no location/slice)
      - A2_Placebo_Mutant_Witness (N14 mismatched mutant from M_det that was NOT survived)
      - A3_Full_Differential_Witness (reuses Ours_MuGuard_Flake exact trajectory)
    """
    src_dir = Path(__file__).resolve().parent
    for fn in ("remote_core.py", "remote_python_core.py", "remote_eval_unified.py"):
        scp_to_remote(src_dir / fn, f"scripts/{fn}")

    ablation_out = DATA_DIR / "ablation_n14_results.json"
    existing_ab: Dict[str, Any] = {}
    if ablation_out.exists():
        for item in json.loads(ablation_out.read_text(encoding="utf-8")).get("cases", []):
            existing_ab[f"{item['model']}::{item['sample_id']}"] = item

    target_pairs: List[Tuple[str, Dict[str, Any], Dict[str, Any]]] = []
    for model in models:
        res_p = DATA_DIR / f"full_results_{safe_model_tag(model)}.json"
        if not res_p.exists():
            continue
        for r in json.loads(res_p.read_text(encoding="utf-8")).get("results", []):
            ours = r["strategies"]["Ours_MuGuard_Flake"]
            if ours.get("erosion_retries", 0) >= 1 and r["sample_id"] in samples_by_id:
                key = f"{model}::{r['sample_id']}"
                if key not in existing_ab:
                    target_pairs.append((model, samples_by_id[r["sample_id"]], ours))

    log(f"[N14 ABLATION] Running 4-way witness diagnostic control on {len(target_pairs)} new erosion-triggered instances (existing: {len(existing_ab)})...")
    model_caches: Dict[str, Tuple[Path, Dict[str, Dict[str, Any]]]] = {
        m: load_disk_llm_cache(m) for m in {mp for mp, _, _ in target_pairs}
    }

    def run_one_ablation(model: str, smp: Dict[str, Any], ours_full: Dict[str, Any]) -> Dict[str, Any]:
        cache_path, disk_cache = model_caches[model]
        eval_cache: Dict[str, Dict[str, Any]] = {}
        a0 = run_strategy_loop("Ablation_A0_GateOnly", smp, model, max_rounds, disk_cache, cache_path, eval_cache, witness_mode="abstain_only")
        a1 = run_strategy_loop("Ablation_A1_BinaryScore", smp, model, max_rounds, disk_cache, cache_path, eval_cache, witness_mode="binary")
        a2 = run_strategy_loop("Ablation_A2_PlaceboWitness", smp, model, max_rounds, disk_cache, cache_path, eval_cache, witness_mode="placebo")
        log(
            f"  [ABLATION DONE {model}::{smp['sample_id']}] "
            f"A0={a0['gate_state']} | A1={a1['gate_state']}(r={a1['accepted_round']}) | "
            f"A2={a2['gate_state']}(r={a2['accepted_round']}) | A3(Ours)={ours_full['gate_state']}(r={ours_full['accepted_round']})"
        )
        return {
            "model": model,
            "sample_id": smp["sample_id"],
            "language": smp.get("language", "java"),
            "od_subtype": smp.get("od_subtype", smp["test_type"]),
            "project_name": smp["project_name"],
            "A0_GateOnly": a0,
            "A1_BinaryScore": a1,
            "A2_PlaceboWitness": a2,
            "A3_FullWitness": ours_full,
        }

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(run_one_ablation, m, s, o) for m, s, o in target_pairs]
        for fut in as_completed(futs):
            try:
                rec = fut.result()
            except Exception as e:
                log(f"[ABLATION ERROR]: {e}")
                continue
            with SAVE_LOCK:
                existing_ab[f"{rec['model']}::{rec['sample_id']}"] = rec
                ablation_out.write_text(
                    json.dumps({"cases": list(existing_ab.values())}, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
    for _, (cp, dc) in model_caches.items():
        save_disk_llm_cache(cp, dc)
    log(f"=== N14 ABLATION COMPLETE: {len(existing_ab)} total cases -> {ablation_out} ===")


def run_round1_cached_mode(samples: List[Dict[str, Any]], models: List[str], workers: int) -> None:
    """
    Evaluate all Round-1 (`K=1`) cross-family samples whose B0 and B2 Round-1 prompts are
    already cached on disk (`llm_cache_<model>.json`), executing zero new LLM API calls and
    saving real Docker testbed verdicts to `data/round1_crossfamily_<model>.json`.
    """
    src_dir = Path(__file__).resolve().parent
    for fn in ("remote_core.py", "remote_python_core.py", "remote_eval_unified.py"):
        scp_to_remote(src_dir / fn, f"scripts/{fn}")

    for model in models:
        cache_path, disk_cache = load_disk_llm_cache(model)
        eligible = []
        for s in samples:
            k_b0 = prompt_cache_key(model, build_unified_prompt("B0_Standard_Validator", s, round_idx=1))
            k_b2 = prompt_cache_key(model, build_unified_prompt("B2_Prompt_Guard", s, round_idx=1))
            if k_b0 in disk_cache and k_b2 in disk_cache:
                eligible.append(s)
        if not eligible:
            log(f"[R1 CACHED {model}] 0 eligible cached samples; skipping.")
            continue

        ordered_ids = [s["sample_id"] for s in eligible]
        out_path = DATA_DIR / f"round1_crossfamily_{safe_model_tag(model)}.json"
        completed_map: Dict[str, Any] = {}
        if out_path.exists():
            for r in json.loads(out_path.read_text(encoding="utf-8")).get("results", []):
                completed_map[r["sample_id"]] = r

        todo = [s for s in eligible if s["sample_id"] not in completed_map]
        log(f"[R1 CACHED {model}] {len(completed_map)}/{len(eligible)} done; evaluating {len(todo)} remaining on Docker (K=1)...")
        save_full_results(out_path, model, 1, completed_map, ordered_ids)

        t0 = time.time()
        with ThreadPoolExecutor(max_workers=workers) as ex:
            fut_map = {
                ex.submit(process_one_sample, smp, model, 1, disk_cache, cache_path): smp["sample_id"]
                for smp in todo
            }
            for fut in as_completed(fut_map):
                sid = fut_map[fut]
                try:
                    res = fut.result()
                except Exception as e:
                    log(f"[R1 ERROR {model} on {sid}]: {e}")
                    continue
                with SAVE_LOCK:
                    completed_map[sid] = res
                    save_full_results(out_path, model, 1, completed_map, ordered_ids)
        log(f"=== R1 CACHED EVAL COMPLETE ({model}: {len(completed_map)}/{len(eligible)}) in {(time.time()-t0)/60:.1f} min -> {out_path} ===")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["prewarm", "eval", "ablation", "round1_cached"], required=True)
    parser.add_argument("--bench", default=str(DATA_DIR / "full_benchmark.json"))
    parser.add_argument("--model", default="deepseek-flash")
    parser.add_argument("--models", default="deepseek-flash,kimi-k2.5,qwen3.7-plus")
    parser.add_argument("--max-rounds", type=int, default=5)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    bench_data = json.loads(Path(args.bench).read_text(encoding="utf-8"))
    samples = select_stratified_subset(bench_data["samples"], args.limit)

    if args.mode == "prewarm":
        m_list = [m.strip() for m in args.models.split(",") if m.strip()]
        run_prewarm_mode(samples, m_list, args.workers)
    elif args.mode == "eval":
        run_eval_mode(samples, args.model, args.max_rounds, args.workers)
    elif args.mode == "ablation":
        m_list = [m.strip() for m in args.models.split(",") if m.strip()]
        s_map = {s["sample_id"]: s for s in bench_data["samples"]}
        run_ablation_mode(s_map, m_list, args.max_rounds, args.workers)
    elif args.mode == "round1_cached":
        m_list = [m.strip() for m in args.models.split(",") if m.strip()]
        run_round1_cached_mode(samples, m_list, args.workers)


if __name__ == "__main__":
    main()
