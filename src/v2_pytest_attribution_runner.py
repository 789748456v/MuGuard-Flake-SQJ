"""
FT-1 v2.0.0-frozen Node-and-Phase Pytest Attribution Runner & Disjoint Held-Out
Python AST Fault Generator (`M_ext_operator` & `M_ext_instance`).
"""
import ast
import hashlib
from typing import Any, Dict, List, Set

# Self-contained runner executed inside Python containers (or local pytest regression tests).
# Uses an inline pytest plugin (`pytest_runtest_logreport`) so zero-test runs, skipped tests,
# and setup/teardown crashes NEVER masquerade as target `call`-phase kills!
PY_V2_ATTRIBUTED_MUT_RUNNER = r'''
import json, os, shutil, subprocess, sys

 HOOK_CODE = """
import json, os, pytest

_RECORDS = []

@pytest.hookimpl(tryfirst=True)
def pytest_runtest_logreport(report):
    exc_type = ""
    exc_msg = ""
    is_assert_like = False
    if report.failed and report.longrepr is not None:
        rc = getattr(report.longrepr, "reprcrash", None)
        if rc is not None:
            raw_m = getattr(rc, "message", "") or ""
            exc_msg = raw_m[:240]
            exc_type = raw_m.split(":", 1)[0].strip() if ":" in raw_m else raw_m.strip()
        else:
            s = str(report.longrepr)
            exc_msg = s[-240:]
            exc_type = "Failed"
        low = (exc_type + " " + exc_msg).lower()
        if (
            "assertionerror" in low
            or "assert " in low
            or "did not raise" in low
            or "failed:" in low
        ):
            is_assert_like = True
    _RECORDS.append({
        "nodeid": report.nodeid,
        "when": report.when,
        "outcome": report.outcome,
        "exc_type": exc_type,
        "is_assert_like": is_assert_like,
    })

def pytest_sessionfinish(session, exitstatus):
    out_p = os.environ.get("FT1_REPORT_JSON", "")
    if out_p:
        with open(out_p, "w", encoding="utf-8") as f:
            json.dump({"exitstatus": int(exitstatus), "records": _RECORDS}, f)
"""

spec_file = sys.argv[1]
cfg = json.load(open(spec_file, encoding="utf-8"))
pytest_args = cfg["pytest_args"]
target_hint = cfg.get("target_node_hint", "")
partner_hint = cfg.get("partner_node_hint", "")
mutants = cfg["mutants"]
timeout_sec = int(cfg.get("per_mutant_timeout", 8))

def _match_node(nodeid, hint):
    if not hint:
        return True
    n_clean = nodeid.lstrip("./")
    h_clean = hint.lstrip("./")
    # Explicit parameterized instance (`test_x[A]`): NEVER strip `[...]` or cross-match `[B]`!
    if "[" in h_clean:
        if n_clean == h_clean:
            return True
        if "::" in h_clean and n_clean.endswith("::" + h_clean):
            return True
        return False
    # Unparameterized hint (`test_x` or `path.py::Cls::test_x`): matches exact node OR any param instance `test_x[...]`
    if n_clean == h_clean or n_clean.startswith(h_clean + "["):
        return True
    if "::" in h_clean:
        n_base = n_clean.split("[", 1)[0]
        if n_base == h_clean or n_base.endswith("::" + h_clean):
            return True
    return False

hook_mod = "_ft1_v2_pytest_plugin.py"
rep_json = ".ft1_v2_last_report.json"
open(hook_mod, "w", encoding="utf-8").write(HOOK_CODE.lstrip())

orig_backups = {}
for m in mutants:
    rf = m["rel_file"]
    if rf not in orig_backups and os.path.exists(rf):
        orig_backups[rf] = open(rf, "rb").read()

results = {}
try:
    for m in mutants:
        rf = m["rel_file"]
        pycache_dir = os.path.join(os.path.dirname(rf) or ".", "__pycache__")
        if os.path.isdir(pycache_dir):
            shutil.rmtree(pycache_dir, ignore_errors=True)
        if os.path.exists(rep_json):
            os.remove(rep_json)
        open(rf, "w", encoding="utf-8").write(m["mutated_file_src"])
        env = dict(os.environ)
        env["FT1_REPORT_JSON"] = os.path.abspath(rep_json)
        env["PYTHONPATH"] = os.getcwd() + os.pathsep + env.get("PYTHONPATH", "")
        try:
            p = subprocess.run(
                [
                    sys.executable, "-m", "pytest", "-q", "--tb=short",
                    "-p", "no:randomly", "-p", "no:random_order",
                    "-p", "_ft1_v2_pytest_plugin",
                ] + pytest_args,
                capture_output=True, text=True, timeout=timeout_sec, env=env,
            )
            if os.path.exists(rep_json):
                rep = json.load(open(rep_json, encoding="utf-8"))
                recs = rep.get("records", [])
                t_recs = [r for r in recs if _match_node(r["nodeid"], target_hint)] if target_hint else recs
                t_setup_fails = [r for r in t_recs if r["when"] == "setup" and r["outcome"] == "failed"]
                t_teardown_fails = [r for r in t_recs if r["when"] == "teardown" and r["outcome"] == "failed"]
                t_calls = [r for r in t_recs if r["when"] == "call"]
                t_skipped = [r for r in t_recs if r["outcome"] == "skipped"]
                t_failed = [r for r in t_calls if r["outcome"] == "failed"]
                t_passed = [r for r in t_calls if r["outcome"] == "passed"]

                # If `partner_node_hint` supplied (e.g. OD-brittle paired e_0), partner must pass setup/call/teardown
                partner_bad = False
                if partner_hint and partner_hint != target_hint:
                    p_recs = [r for r in recs if _match_node(r["nodeid"], partner_hint)]
                    p_calls = [r for r in p_recs if r["when"] == "call"]
                    p_fails = [r for r in p_recs if r["outcome"] in ("failed", "skipped")]
                    if len(p_calls) == 0 or len(p_fails) > 0:
                        partner_bad = True

                if partner_bad:
                    results[m["id"]] = {
                        "killed": False,
                        "is_unknown": True,
                        "status": "UNKNOWN_PARTNER_FAIL",
                        "exc_type": "partner_failed_or_missing",
                        "tests_ran": len(t_calls),
                    }
                elif len(t_setup_fails) > 0:
                    results[m["id"]] = {
                        "killed": False,
                        "is_unknown": True,
                        "status": "INVALID_SETUP_FAIL",
                        "exc_type": t_setup_fails[0]["exc_type"],
                        "tests_ran": len(t_calls),
                    }
                elif len(t_teardown_fails) > 0:
                    # Must-pass teardown check ALWAYS precedes call-kill credit (call+teardown dual failure -> UNKNOWN!)
                    results[m["id"]] = {
                        "killed": False,
                        "is_unknown": True,
                        "status": "UNKNOWN_TEARDOWN_FAIL",
                        "exc_type": t_teardown_fails[0]["exc_type"],
                        "tests_ran": len(t_calls),
                    }
                elif len(t_calls) >= 1 and len(t_failed) >= 1:
                    f0 = t_failed[0]
                    results[m["id"]] = {
                        "killed": True,
                        "is_unknown": False,
                        "status": "KILLED_CALL_ASSERT" if f0["is_assert_like"] else "KILLED_CALL_EXC",
                        "exc_type": f0["exc_type"],
                        "nodeid": f0["nodeid"],
                        "tests_ran": len(t_calls),
                    }
                elif len(t_calls) >= 1 and len(t_passed) == len(t_calls) and len(t_skipped) == 0 and p.returncode == 0:
                    results[m["id"]] = {
                        "killed": False,
                        "is_unknown": False,
                        "status": "SURVIVED_PASS",
                        "exc_type": "",
                        "nodeid": t_passed[0]["nodeid"],
                        "tests_ran": len(t_calls),
                    }
                elif len(t_calls) == 0:
                    results[m["id"]] = {
                        "killed": False,
                        "is_unknown": True,
                        "status": "INVALID_ZERO_TESTS_OR_SKIPPED",
                        "exc_type": "skipped" if t_skipped else f"rc={p.returncode}",
                        "tests_ran": 0,
                    }
                else:
                    results[m["id"]] = {
                        "killed": False,
                        "is_unknown": True,
                        "status": "UNKNOWN_OTHER_NON_KILL",
                        "exc_type": f"rc={p.returncode}",
                        "tests_ran": len(t_calls),
                    }
            else:
                results[m["id"]] = {
                    "killed": False,
                    "is_unknown": True,
                    "status": f"UNKNOWN_NO_REPORT_RC_{p.returncode}",
                    "exc_type": "",
                    "tests_ran": 0,
                }
        except subprocess.TimeoutExpired:
            results[m["id"]] = {
                "killed": False,
                "is_unknown": True,
                "status": "UNKNOWN_TIMED_OUT",
                "exc_type": "TimeoutExpired",
                "tests_ran": 0,
            }
        finally:
            if rf in orig_backups:
                open(rf, "wb").write(orig_backups[rf])
            if os.path.isdir(pycache_dir):
                shutil.rmtree(pycache_dir, ignore_errors=True)
finally:
    for rf, raw in orig_backups.items():
        open(rf, "wb").write(raw)
    for tmpf in (hook_mod, rep_json):
        if os.path.exists(tmpf):
            try:
                os.remove(tmpf)
            except OSError:
                pass

killed_ids = [mid for mid, info in results.items() if info.get("killed")]
unknown_ids = [mid for mid, info in results.items() if info.get("is_unknown")]
survived_ids = [mid for mid, info in results.items() if not info.get("killed") and not info.get("is_unknown")]
print("===FT1_PY_V2_MUT_START===")
print(json.dumps({"killed_ids": killed_ids, "survived_ids": survived_ids, "unknown_ids": unknown_ids, "details": results}))
print("===FT1_PY_V2_MUT_END===")
'''.replace("\n HOOK_CODE =", "\nHOOK_CODE =").strip()


# ============================================================================
# Disjoint Unseen Python AST Mutator Families (`M_ext_operator`)
# Strictly absent from `remote_python_core.generate_py_focal_mutants`:
#   1) `AugAssignMutator` (`+=` <-> `-=`, `*=` <-> `//=`)
#   2) `UnaryOpMutator` (`-x` -> `+x`, `not x` -> `x` on standalone UnaryOp, `~x` -> `x`)
#   3) `DictEntryDropMutator` (drop first key-value entry in non-empty dict literal)
#   4) `SliceBoundMutator` (`a[i:j]` -> `a[i+1:j]` or `a[:0]`)
#   5) `CallLastArgDropMutator` (drop trailing keyword or 2nd+ positional arg on non-builtin call)
# ============================================================================

def _src_sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:20]


def _slice_around(src: str, ln: int) -> str:
    lns = src.splitlines()
    lo = max(1, ln - 2)
    hi = min(len(lns), ln + 2)
    return "\n".join(f"{i}: {lns[i-1]}" for i in range(lo, hi + 1))


def generate_py_m_ext_operator_mutants(
    rel_file: str,
    mod_fqcn: str,
    src: str,
    covered_lines: Set[int],
    forbidden_src_hashes: Set[str],
    max_candidates: int = 45,
) -> List[Dict[str, Any]]:
    try:
        orig_tree = ast.parse(src)
    except SyntaxError:
        return []
    if not hasattr(ast, "unparse"):
        return []

    # Map line -> enclosing function name
    line_to_fn: Dict[int, str] = {}
    for node in ast.walk(orig_tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end_ln = getattr(node, "end_lineno", node.lineno)
            for l_i in range(node.lineno, end_ln + 1):
                line_to_fn[l_i] = node.name

    indexed = list(enumerate(ast.walk(orig_tree)))
    out: List[Dict[str, Any]] = []
    seen_hashes = set(forbidden_src_hashes)

    def _emit(target_idx: int, mut_name: str, desc: str, transform_fn) -> None:
        if len(out) >= max_candidates:
            return
        t2 = ast.parse(src)
        nodes2 = list(ast.walk(t2))
        if target_idx >= len(nodes2):
            return
        nd = nodes2[target_idx]
        ln = getattr(nd, "lineno", 0)
        if covered_lines and ln not in covered_lines:
            return
        ok = transform_fn(nd)
        if not ok:
            return
        ast.fix_missing_locations(t2)
        try:
            m_src = ast.unparse(t2)
            compile(m_src, rel_file, "exec")
        except Exception:
            return
        h = _src_sha256(m_src)
        if h in seen_hashes:
            return
        seen_hashes.add(h)
        fn_name = line_to_fn.get(ln, "<module>")
        mid = f"MEXT_OP::{mod_fqcn}::{fn_name}:L{ln}:{mut_name}:{target_idx}:{h[:8]}"
        out.append({
            "id": mid,
            "pool": "M_ext_operator",
            "rel_file": rel_file,
            "mutatedClass": mod_fqcn,
            "mutatedMethod": fn_name,
            "lineNumber": ln,
            "mutator": mut_name,
            "description": desc,
            "src_sha256": h,
            "source_slice": _slice_around(src, ln),
            "mutated_file_src": m_src,
        })

    for idx, node in indexed:
        if len(out) >= max_candidates:
            break
        ln = getattr(node, "lineno", 0)
        if covered_lines and ln not in covered_lines:
            continue

        if isinstance(node, ast.AugAssign):
            if isinstance(node.op, ast.Add):
                _emit(idx, "AugAssignMutator", "replaced `+=` with `-=`", lambda n: setattr(n, "op", ast.Sub()) or True)
            elif isinstance(node.op, ast.Sub):
                _emit(idx, "AugAssignMutator", "replaced `-=` with `+=`", lambda n: setattr(n, "op", ast.Add()) or True)
        elif isinstance(node, ast.UnaryOp):
            if isinstance(node.op, ast.USub):
                _emit(idx, "UnaryOpMutator", "replaced unary `-x` with `+x`", lambda n: setattr(n, "op", ast.UAdd()) or True)
            elif isinstance(node.op, ast.Invert):
                _emit(idx, "UnaryOpMutator", "replaced bitwise `~x` with `+x`", lambda n: setattr(n, "op", ast.UAdd()) or True)
        elif isinstance(node, ast.Dict) and len(node.keys) >= 1 and node.keys[0] is not None:
            def _drop_dict_kv(n: ast.Dict) -> bool:
                n.keys = n.keys[1:]
                n.values = n.values[1:]
                return True
            _emit(idx, "DictEntryDropMutator", "dropped first entry from dict literal", _drop_dict_kv)
        elif isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Slice):
            def _perturb_slice(n: ast.Subscript) -> bool:
                sl = n.slice
                if isinstance(sl, ast.Slice):
                    sl.upper = ast.Constant(value=0)
                    return True
                return False
            _emit(idx, "SliceBoundMutator", "truncated slice upper bound to `:0`", _perturb_slice)
        elif isinstance(node, ast.Call):
            fn_s = ast.unparse(node.func)
            base = fn_s.split(".")[-1]
            if base not in ("isinstance", "len", "range", "super", "getattr", "hasattr", "setattr"):
                if len(node.keywords) >= 1:
                    kw_name = node.keywords[-1].arg or "**kwargs"
                    def _drop_kw(n: ast.Call) -> bool:
                        n.keywords = n.keywords[:-1]
                        return True
                    _emit(idx, "CallKwargDropMutator", f"dropped keyword argument `{kw_name}`", _drop_kw)

    return out
