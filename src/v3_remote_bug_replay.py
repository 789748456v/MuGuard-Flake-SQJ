"""
FT-1 v3.0 Remote Real Historical Production Bug-Revert Replayer.

Given a sample, a clean-revertable historical production bug-fix commit SHA, and a dict of
strategy candidate patches (`orig`, `B0`, `B3+`, `B4`, `MuGuard`, `B5_Coverage`, `B6_Judge`, `B1`),
this script:
  1. Reverts ONLY the focal production files of `commit_sha` onto HEAD (guaranteed `git checkout --`
     and bytecode backup restoration in `finally:`).
  2. Compiles the reverted buggy production code `P_bug`.
  3. Runs `T_orig` in clean `e_0` — ONLY commits where `T_orig` cleanly FAILS (reproducing the
     historical production bug!) are certified as real regression bugs.
  4. For every certified real bug, executes each strategy's accepted test patch against `P_bug`
     and records whether the patched test still CATCHES the real bug (`FAIL` -> `BUG_CAUGHT`)
     or SILENTLY MISSES IT (`PASS` -> `BUG_MISSED_SILENT_GREEN`)!
"""
import json
import os
import re
import subprocess
import sys
import time
from typing import Any, Dict, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import remote_core as rc
import remote_python_core as rpc
from v2_guards_and_attribution import is_code_semantically_changed


def run_single_test_clean(sample: Dict[str, Any]) -> Tuple[bool, str]:
    """Run the test currently on disk under clean e_0. Returns (passed, diag)."""
    lang = sample.get("language", "java")
    if lang == "java":
        pdir = sample["proj_dir"]
        mod_rel = sample["module"]
        jdk = sample["jdk"]
        uses_j5 = sample["uses_junit5"]
        spec = f"{sample['victim_fqcn']}#{sample['victim_method']}"
        if not uses_j5 and rc.ensure_order_runner_ready(pdir, mod_rel, jdk):
            ok, out, _ = rc.run_junit4_specs(pdir, mod_rel, jdk, [spec], timeout=60)
            return ok, out[-300:]
        pl = f"-pl {mod_rel}" if mod_rel not in ("", ".") else ""
        r_code, out, _ = rc.docker_mvn(pdir, jdk, f"surefire:test {pl} -Dtest='{spec}' -DfailIfNoTests=false", timeout=90)
        ok = (r_code == 0) and ("Failures: 0, Errors: 0" in out or "BUILD SUCCESS" in out)
        return ok, out[-300:]
    else:
        pdir = sample["proj_dir"]
        od_sub = sample.get("od_subtype") or "OD-victim"
        # For OD-brittle on original test, run setter + target; for patched candidate tests, run isolated target
        specs = f"'{sample['polluter_full_name']}' '{sample['test_full_name']}'" if (od_sub == "OD-brittle" and sample.get("polluter_full_name")) else f"'{sample['test_full_name']}'"
        r_code, out, _ = rpc.run_py_docker(pdir, f"python3 -m pytest -q -p no:randomly -p no:random_order {specs}", timeout=60)
        return (r_code == 0), out[-300:]


def run_candidate_on_buggy_prod(sample: Dict[str, Any], candidate: Dict[str, Any]) -> Dict[str, Any]:
    """Apply candidate test patch over ALREADY-reverted buggy production code, compile test, and run."""
    lang = sample.get("language", "java")
    if lang == "java":
        pdir = sample["proj_dir"]
        mod_rel = sample["module"]
        jdk = sample["jdk"]
        ttype = sample["test_type"]
        mod_dir = pdir if mod_rel in ("", ".") else os.path.join(pdir, mod_rel)
        vic_file = sample["victim_file_path"]
        pol_file = sample.get("polluter_file_path") or vic_file
        same_file = (os.path.abspath(vic_file) == os.path.abspath(pol_file))
        orig_vic = open(vic_file, encoding="utf-8", errors="replace").read()
        orig_pol = orig_vic if same_file else open(pol_file, encoding="utf-8", errors="replace").read()
        vbak = rc.backup_class_files(mod_dir, sample["victim_fqcn"])
        pbak = rc.backup_class_files(mod_dir, sample["polluter_fqcn"]) if (sample.get("polluter_fqcn") and sample["polluter_fqcn"] != sample["victim_fqcn"]) else {}
        try:
            if ttype == "ID":
                tcode = candidate.get("test_code") or candidate.get("victim_code") or ""
                ns = rc.replace_java_method(orig_vic, sample["victim_method"], tcode)
                ns = rc.inject_java_imports(ns, candidate.get("imports") or [])
                open(vic_file, "w", encoding="utf-8").write(ns)
            else:
                vcode = candidate.get("victim_code") or candidate.get("test_code") or ""
                pcode = candidate.get("polluter_code") or ""
                p_chg = is_code_semantically_changed("java", sample.get("orig_polluter_code", ""), pcode)
                if same_file:
                    ns = orig_vic
                    if vcode:
                        ns = rc.replace_java_method(ns, sample["victim_method"], vcode)
                    if p_chg and sample["polluter_method"] != sample["victim_method"]:
                        ns = rc.replace_java_method(ns, sample["polluter_method"], pcode)
                    ns = rc.inject_java_imports(ns, candidate.get("imports") or [])
                    open(vic_file, "w", encoding="utf-8").write(ns)
                else:
                    if vcode:
                        ns = rc.replace_java_method(orig_vic, sample["victim_method"], vcode)
                        ns = rc.inject_java_imports(ns, candidate.get("imports") or [])
                        open(vic_file, "w", encoding="utf-8").write(ns)
                    if p_chg:
                        open(pol_file, "w", encoding="utf-8").write(rc.replace_java_method(orig_pol, sample["polluter_method"], pcode))

            pl = f"-pl {mod_rel}" if mod_rel not in ("", ".") else ""
            c_rc, c_out, _ = rc.docker_mvn(pdir, jdk, f"test-compile {pl}", timeout=120)
            if c_rc != 0:
                return {"compiled": False, "outcome": "COMPILE_FAIL", "diag": c_out[-250:]}
            passed, diag = run_single_test_clean(sample)
            return {
                "compiled": True,
                "passed_on_bug": passed,
                "outcome": "BUG_MISSED_SILENT_GREEN" if passed else "BUG_CAUGHT",
                "diag": diag,
            }
        finally:
            open(vic_file, "w", encoding="utf-8").write(orig_vic)
            if not same_file:
                open(pol_file, "w", encoding="utf-8").write(orig_pol)
            rc.restore_class_files(mod_dir, sample["victim_fqcn"], vbak)
            if pbak and sample.get("polluter_fqcn"):
                rc.restore_class_files(mod_dir, sample["polluter_fqcn"], pbak)
    else:
        pdir = sample["proj_dir"]
        vic_file = os.path.join(pdir, sample["victim_file_rel"])
        pol_rel = sample.get("polluter_file_rel") or sample["victim_file_rel"]
        pol_file = os.path.join(pdir, pol_rel)
        same_file = (os.path.abspath(vic_file) == os.path.abspath(pol_file))
        orig_vic = open(vic_file, encoding="utf-8", errors="replace").read()
        orig_pol = orig_vic if same_file else open(pol_file, encoding="utf-8", errors="replace").read()
        try:
            vcode = candidate.get("victim_code") or candidate.get("test_code") or ""
            pcode = candidate.get("polluter_code") or ""
            p_chg = is_code_semantically_changed("python", sample.get("orig_polluter_code", ""), pcode)
            if same_file:
                ns = orig_vic
                if vcode:
                    ns = rpc.replace_py_func(ns, sample.get("victim_class"), sample["victim_method"], vcode)
                if p_chg and sample.get("polluter_method"):
                    ns = rpc.replace_py_func(ns, sample.get("polluter_class"), sample["polluter_method"], pcode)
                ns = rpc.inject_py_imports(ns, candidate.get("imports") or [])
                open(vic_file, "w", encoding="utf-8").write(ns)
            else:
                if vcode:
                    ns = rpc.replace_py_func(orig_vic, sample.get("victim_class"), sample["victim_method"], vcode)
                    ns = rpc.inject_py_imports(ns, candidate.get("imports") or [])
                    open(vic_file, "w", encoding="utf-8").write(ns)
                if p_chg and sample.get("polluter_method"):
                    open(pol_file, "w", encoding="utf-8").write(rpc.replace_py_func(orig_pol, sample.get("polluter_class"), sample["polluter_method"], pcode))

            # Run patched test standalone (isolated!)
            r_code, out, _ = rpc.run_py_docker(pdir, f"python3 -m pytest -q -p no:randomly -p no:random_order '{sample['test_full_name']}'", timeout=60)
            passed = (r_code == 0)
            return {
                "compiled": True,
                "passed_on_bug": passed,
                "outcome": "BUG_MISSED_SILENT_GREEN" if passed else "BUG_CAUGHT",
                "diag": out[-250:],
            }
        finally:
            open(vic_file, "w", encoding="utf-8").write(orig_vic)
            if not same_file:
                open(pol_file, "w", encoding="utf-8").write(orig_pol)


def replay_commit(payload: Dict[str, Any]) -> Dict[str, Any]:
    sample = payload["sample"]
    git_root = payload["git_root"]
    commit = payload["commit"]
    candidates: Dict[str, Dict[str, Any]] = payload["candidates"]
    sha = commit["commit_sha"]
    touched = commit["touched_focal_files"]
    lang = sample.get("language", "java")

    t0 = time.time()
    p_diff = subprocess.run(
        ["git", "-C", git_root, "diff", sha, f"{sha}^", "--"] + touched,
        capture_output=True, text=True, errors="replace", timeout=20,
    )
    if p_diff.returncode != 0 or not p_diff.stdout.strip():
        return {"certified_real_bug": False, "reason": "diff_extract_failed"}

    # Apply production-only revert
    p_app = subprocess.run(
        ["git", "-C", git_root, "apply", "-"],
        input=p_diff.stdout, capture_output=True, text=True, errors="replace", timeout=20,
    )
    if p_app.returncode != 0:
        return {"certified_real_bug": False, "reason": f"apply_failed: {p_app.stderr[:120]}"}

    try:
        # Compile reverted buggy production code (Java only)
        if lang == "java":
            pl = f"-pl {sample['module']}" if sample["module"] not in ("", ".") else ""
            c_rc, c_out, _ = rc.docker_mvn(sample["proj_dir"], sample["jdk"], f"test-compile {pl}", timeout=120)
            if c_rc != 0:
                return {"certified_real_bug": False, "reason": "reverted_prod_compile_fail"}

        # Check whether T_orig catches this reverted production bug!
        orig_passed, orig_diag = run_single_test_clean(sample)
        if orig_passed:
            return {"certified_real_bug": False, "reason": "orig_test_still_passed_on_reverted_commit"}

        # CERTIFIED REAL HISTORICAL PRODUCTION BUG! Now test every candidate patch against P_bug!
        cand_outcomes: Dict[str, Any] = {}
        for label, cand in candidates.items():
            cand_outcomes[label] = run_candidate_on_buggy_prod(sample, cand)

        return {
            "certified_real_bug": True,
            "sample_id": sample["sample_id"],
            "language": lang,
            "commit_sha": sha,
            "commit_date": commit["date"],
            "commit_subject": commit["subject"],
            "touched_focal_files": touched,
            "orig_fail_diag": orig_diag,
            "candidate_outcomes": cand_outcomes,
            "wall_sec": round(time.time() - t0, 2),
        }
    finally:
        # Guaranteed clean restore of production files + recompile clean classes!
        subprocess.run(["git", "-C", git_root, "checkout", "--"] + touched, capture_output=True, timeout=20)
        if lang == "java":
            pl = f"-pl {sample['module']}" if sample["module"] not in ("", ".") else ""
            rc.docker_mvn(sample["proj_dir"], sample["jdk"], f"test-compile {pl}", timeout=120)


def run_batch(spec_path: str, state_path: str, workers: int = 2) -> None:
    """Single-SSH remote batch runner for real historical bug replay (per-project locked, checkpointed)."""
    import threading
    from collections import defaultdict
    from concurrent.futures import ThreadPoolExecutor, as_completed

    spec = json.load(open(spec_path, encoding="utf-8"))
    state: Dict[str, Any] = {"evaluated": {}}
    if os.path.exists(state_path):
        try:
            state = json.load(open(state_path, encoding="utf-8"))
        except Exception:
            pass

    lock = threading.Lock()
    proj_locks: Dict[str, threading.Lock] = defaultdict(threading.Lock)

    def _save() -> None:
        tmp = state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
        os.replace(tmp, state_path)

    jobs = [j for j in spec["jobs"] if j["jk"] not in state["evaluated"]]
    print(f"[BATCH BUG REPLAY] {len(state['evaluated'])} done; running {len(jobs)} candidate historical commits...", flush=True)

    def _one(j: Dict[str, Any]) -> None:
        jk = j["jk"]
        proj = j["sample"]["project_name"]
        with proj_locks[proj]:
            res = replay_commit(j)
        with lock:
            state["evaluated"][jk] = res
            if len(state["evaluated"]) % 5 == 0 or res.get("certified_real_bug"):
                _save()
        if res.get("certified_real_bug"):
            missed = [k for k, v in (res.get("candidate_outcomes") or {}).items() if v.get("outcome") == "BUG_MISSED_SILENT_GREEN"]
            print(f"  [CERTIFIED REAL BUG!] {jk} ('{j['commit']['subject'][:55]}') -> SILENT_MISS={len(missed)}", flush=True)
        else:
            print(f"  [skip] {jk}: {res.get('reason')}", flush=True)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(_one, j) for j in jobs]
        for f in as_completed(futs):
            try:
                f.result()
            except Exception as e:
                print(f"  [ERR BUG]: {e}", flush=True)
    _save()
    cert = sum(1 for v in state["evaluated"].values() if v.get("certified_real_bug"))
    print(f"[BATCH BUG REPLAY DONE] evaluated={len(state['evaluated'])} certified_real_bugs={cert}", flush=True)


def main() -> None:
    if len(sys.argv) >= 4 and sys.argv[1] == "batch":
        run_batch(sys.argv[2], sys.argv[3], workers=int(sys.argv[4]) if len(sys.argv) > 4 else 2)
        return
    payload = json.loads(sys.stdin.read())
    res = replay_commit(payload)
    print("FT1_V3_BUG_REPLAY=" + json.dumps(res, ensure_ascii=False))


if __name__ == "__main__":
    main()
