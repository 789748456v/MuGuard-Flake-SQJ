"""
FT-1 v3.0 Fair Python Indentation Normalizer Ablation (`v3_python_indent_ablation.py`).

Root cause discovered in v2.3:
  In `python_ast_guard_and_prompts.py`, `parse_py_llm_patch_response` called `.strip()`
  on the extracted block between `# <victim_fix start>` and `# <victim_fix end>`. When an
  LLM emitted a class test method indented by 4 spaces (especially decorated methods like
  `@mock.patch(...)` in `centreon-sdk-python`, `PySyncDroid`, `api-python`), `.strip()`
  stripped leading spaces ONLY from line 0 while leaving subsequent lines indented by 4-8
  spaces, triggering `IndentationError` inside `check_py_basic_sanity` (`SANITY_REJECT_FINAL`),
  which accounted for 43%-50% of all MuGuard-Flake terminal abstentions!

Fair Ablation Protocol:
  Applies `normalize_py_method_block()` IDENTICALLY to ALL strategies
  (`B0_Standard_Validator`, `B2_Prompt_Guard`, `B3_Static_AST_Guard`,
   `B3_Plus_Structural_Guard`, `B4_Scalar_Gate_Loop`, `Ours_MuGuard_Flake`) on every
  Python eligible test where ANY strategy previously suffered a `SANITY_REJECT` round,
  then runs post-freeze held-out validation (`W+_heldout` + `M_ext`) on newly accepted
  candidates to measure the exact unbiased lift in operational SoundFix% AND Fixed-Cohort Strict Y_s!
"""
import argparse
import ast
import json
import sys
import textwrap
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import python_ast_guard_and_prompts as py_guard
import run_full_study_matrix as rfsm
import run_v2_confirmatory_campaign as rv2
from remote_and_llm import DATA_DIR, scp_to_remote

OUT_PATH = DATA_DIR / "v3_python_indent_ablation.json"
LOCK = threading.Lock()


def normalize_py_method_block(raw_text: Optional[str]) -> Optional[str]:
    """
    Deterministically recover valid top-level Python indentation when `.strip()` stripped
    leading spaces from line 0 (e.g., `@decorator` or `def test_x(self):`) while preserving
    indentation on lines 1..N. Verified strictly via `ast.parse`.
    """
    if not raw_text:
        return raw_text
    # 1. Try standard textwrap.dedent on raw_text first
    cleaned = raw_text.strip("\r\n")
    for cand in (
        textwrap.dedent(cleaned),
        cleaned,
    ):
        try:
            tree = ast.parse(cand)
            if any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) for n in tree.body):
                return cand
        except SyntaxError:
            pass

    # 2. Repair the exact `.strip()` artifact: line 0 has indent 0, lines 1..k have >= 4 spaces!
    lines = cleaned.splitlines()
    if len(lines) >= 2:
        non_empty_rest = [ln for ln in lines[1:] if ln.strip()]
        if non_empty_rest:
            min_rest_indent = min(len(ln) - len(ln.lstrip(" ")) for ln in non_empty_rest)
            # Case A: line 0 is `@decorator` (indent 0) and line 1 is `    def ...` (indent 4) -> prepend min_rest_indent to line 0, then dedent!
            if min_rest_indent > 0 and (len(lines[0]) - len(lines[0].lstrip(" "))) == 0:
                re_indented = (" " * min_rest_indent + lines[0]) + "\n" + "\n".join(lines[1:])
                ded = textwrap.dedent(re_indented)
                try:
                    tree = ast.parse(ded)
                    if any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) for n in tree.body):
                        return ded
                except SyntaxError:
                    pass
                # Case B: line 0 is `def ...:` (indent 0), body is indented by 8 spaces instead of 4 -> dedent lines[1:] by (min_rest_indent - 4)
                if min_rest_indent >= 4 and lines[0].lstrip().startswith(("def ", "async def ")):
                    shift = max(0, min_rest_indent - 4)
                    fixed = lines[0] + "\n" + "\n".join(
                        ln[shift:] if (ln.strip() and len(ln) - len(ln.lstrip(" ")) >= shift) else ln
                        for ln in lines[1:]
                    )
                    try:
                        tree = ast.parse(fixed)
                        if any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) for n in tree.body):
                            return fixed
                    except SyntaxError:
                        pass
    return raw_text


def parse_py_response_normalized(raw_text: str) -> Dict[str, Any]:
    parsed = py_guard.parse_py_llm_patch_response(raw_text)
    if parsed.get("victim_code"):
        parsed["victim_code"] = normalize_py_method_block(parsed["victim_code"])
    if parsed.get("polluter_code"):
        parsed["polluter_code"] = normalize_py_method_block(parsed["polluter_code"])
    return parsed


def load_json_or(path: Path, default: Any) -> Any:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return default


def save_json_atomic(path: Path, obj: Any) -> None:
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--models", nargs="+", default=["qwen3.7-plus", "deepseek-flash", "deepseek-v4-pro"])
    args = ap.parse_args()

    # Patch rv2.parse_v2_response in-memory to apply the fair Python normalizer across ALL strategies!
    orig_parse_v2 = rv2.parse_v2_response

    def _fair_parse_v2(raw_text: str, sample: Dict[str, Any]) -> Dict[str, Any]:
        if sample.get("language") == "python":
            return parse_py_response_normalized(raw_text)
        return orig_parse_v2(raw_text, sample)

    rv2.parse_v2_response = _fair_parse_v2

    src_dir = Path(__file__).resolve().parent
    for fn in ("v2_guards_and_attribution.py", "v2_pytest_attribution_runner.py", "remote_v2_freeze_benchmark_and_mext.py", "remote_eval_v2.py"):
        scp_to_remote(src_dir / fn, f"scripts/{fn}")

    bm = json.loads((DATA_DIR / "v2_frozen_benchmark_light.json").read_text(encoding="utf-8"))
    bench_sha = bm["exec_benchmark_sha256"]
    s_by_id = {s["sample_id"]: s for s in bm["samples"]}

    eval_cache_path, eval_cache = rv2.load_v2_eval_cache()
    ho_v2 = json.loads((DATA_DIR / "v2_heldout_validation.json").read_text(encoding="utf-8"))["evaluations"]

    store = load_json_or(OUT_PATH, {"protocol_version": "3.0.0-py-indent-fair-ablation", "by_model": {}, "heldout_evals": {}})
    by_model = store.setdefault("by_model", {})
    ho_extra = store.setdefault("heldout_evals", {})

    for model in args.models:
        v2_res = json.loads((DATA_DIR / f"v2_results_{rfsm.safe_model_tag(model)}.json").read_text(encoding="utf-8"))["results"]
        # Find ALL Python eligible samples where ANY strategy had a SANITY_REJECT round!
        affected_sids = []
        for r in v2_res:
            if not r.get("v2_eligible") or r.get("language") != "python":
                continue
            hit = False
            for st in r["strategies"].values():
                for rd in st.get("rounds", []):
                    if "SANITY_REJECT" in str(rd.get("decision", "")):
                        hit = True
                        break
                if hit:
                    break
            if hit:
                affected_sids.append(r["sample_id"])

        m_map = by_model.setdefault(model, {})
        todo = [sid for sid in affected_sids if sid not in m_map]
        print(f"[PY INDENT ABLATION {model}] affected_py_tests={len(affected_sids)}, todo={len(todo)}", flush=True)
        if not todo:
            continue

        cache_path, disk_cache = rfsm.load_disk_llm_cache(model)

        def _do_one(sid: str) -> Tuple[str, Dict[str, Any]]:
            smp = s_by_id[sid]
            s_res = {}
            for st in rv2.V2_STRATEGIES_K5:
                s_res[st] = rv2.run_v2_strategy_loop(
                    strategy=st,
                    sample=smp,
                    bench_sha=bench_sha,
                    model=model,
                    max_rounds=5,
                    disk_cache=disk_cache,
                    cache_path=cache_path,
                    eval_cache=eval_cache,
                    eval_cache_path=eval_cache_path,
                )
            b0 = s_res["B0_Standard_Validator"]
            mg = s_res["Ours_MuGuard_Flake"]
            print(f"  [PY-NORM {model} {sid}] B0:sf={int(b0['sound_fix'])},er={int(b0['eroded'])} | MuGuard:{mg['gate_state']}(sf={int(mg['sound_fix'])})", flush=True)
            return sid, {"sample_id": sid, "project_name": smp["project_name"], "strategies": s_res}

        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = {ex.submit(_do_one, sid): sid for sid in todo}
            for fut in as_completed(futs):
                sid = futs[fut]
                try:
                    _, rec = fut.result()
                    with LOCK:
                        m_map[sid] = rec
                        save_json_atomic(OUT_PATH, store)
                except Exception as e:
                    print(f"  [ERR PY-NORM {model} {sid}]: {e}", flush=True)

    # Run post-freeze held-out validation for every newly accepted candidate
    to_ho = []
    seen = set()
    for m, m_map in by_model.items():
        for sid, rec in m_map.items():
            smp = s_by_id[sid]
            if smp.get("m_ext_total_count", 0) <= 0:
                continue
            for st in rec["strategies"].values():
                if not st.get("accepted"):
                    continue
                acc_r = st.get("accepted_round")
                rd = next((x for x in st["rounds"] if x["round"] == acc_r), st["rounds"][-1])
                ck = f"{sid}::{rd['candidate_fp']}"
                if ck not in ho_v2 and ck not in ho_extra and ck not in seen:
                    seen.add(ck)
                    to_ho.append((ck, smp, rd["candidate"]))

    print(f"[PY INDENT HO] Evaluating {len(to_ho)} newly accepted held-out candidates...", flush=True)
    if to_ho:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = {ex.submit(rv2.remote_evaluate_v2, smp, cand, bench_sha, eval_cache, eval_cache_path, "post_freeze_heldout"): ck for ck, smp, cand in to_ho}
            for fut in as_completed(futs):
                ck = futs[fut]
                try:
                    ev, _ = fut.result()
                    with LOCK:
                        ho_extra[ck] = ev
                        save_json_atomic(OUT_PATH, store)
                except Exception as e:
                    print(f"  [ERR PY-NORM HO {ck}]: {e}", flush=True)

    rv2.save_v2_eval_cache(eval_cache_path, eval_cache)
    print("[OK] Python Indentation Fair Ablation complete!")


if __name__ == "__main__":
    main()
