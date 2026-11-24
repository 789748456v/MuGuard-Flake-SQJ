import ast
import re
from typing import Any, Dict, List, Optional, Tuple


PROMPT_GUARD_CONTRACT_PY = """
CRITICAL ORACLE-PRESERVATION CONTRACT (STRICTLY ENFORCED):
1. Do NOT delete, comment out, or reduce the number of `assert` / `pytest.raises` / `self.assert*` checks in any test function.
2. Do NOT weaken exact value, key-set, element-count, multiplicity, or structural completeness assertions into partial checks (e.g., NEVER replace `assert a == b` with `assert a is not None`, `assert len(a) > 0`, or partial substring/`in` checks).
3. Do NOT swallow exceptions with `try ... except:`, add `@pytest.mark.skip` / `pytest.skip()` / `@pytest.mark.xfail`, or reset/clear/overwrite system-under-test state AFTER test act/setup and BEFORE assertions execute.
4. For OD-victim tests: clean/reset polluted module/class/singleton/global state ONLY at the VERY START of the victim test (before victim sets up its own data) or in a `try ... finally:` block at the VERY END of the polluter test (after all polluter assertions finish).
5. For OD-brittle tests: initialize ONLY the required missing prerequisite state/fixture at the VERY START of the brittle test without mocking away or bypassing the real production call under test.
""".strip()


def _analyze_py_ast(func_src: str) -> Optional[Dict[str, Any]]:
    # Dedent before parsing so class methods parse cleanly standalone
    lines = [ln for ln in func_src.splitlines() if ln.strip() != ""]
    if not lines:
        return None
    indent = len(lines[0]) - len(lines[0].lstrip())
    dedented = "\n".join(ln[min(indent, len(ln) - len(ln.lstrip())) :] if ln.strip() else "" for ln in func_src.splitlines())
    try:
        tree = ast.parse(dedented)
    except SyntaxError:
        return None

    fn_nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    if not fn_nodes:
        return None
    fn = fn_nodes[0]

    # Check non-empty body (excluding docstring and bare `pass`)
    real_stmts = []
    for s in fn.body:
        if isinstance(s, ast.Pass):
            continue
        if isinstance(s, ast.Expr) and isinstance(getattr(s, "value", None), ast.Constant) and isinstance(s.value.value, str):
            continue
        real_stmts.append(s)

    assert_cnt = 0
    eq_cnt = 0
    notnone_cnt = 0
    has_tautology = False
    has_skip = False
    has_swallow = False

    for d in fn.decorator_list:
        d_str = ast.unparse(d) if hasattr(ast, "unparse") else ""
        if any(k in d_str for k in ("skip", "xfail")):
            has_skip = True

    for node in ast.walk(fn):
        if isinstance(node, ast.Assert):
            assert_cnt += 1
            t = node.test
            if isinstance(t, ast.Constant) and bool(t.value) is True:
                has_tautology = True
            if isinstance(t, ast.Compare) and len(t.ops) == 1:
                if isinstance(t.ops[0], ast.Eq):
                    eq_cnt += 1
                    if hasattr(ast, "unparse") and ast.unparse(t.left) == ast.unparse(t.comparators[0]):
                        has_tautology = True
                elif isinstance(t.ops[0], ast.IsNot) and isinstance(t.comparators[0], ast.Constant) and t.comparators[0].value is None:
                    notnone_cnt += 1
        elif isinstance(node, ast.Call):
            f_str = ast.unparse(node.func) if hasattr(ast, "unparse") else ""
            base_name = f_str.split(".")[-1]
            if base_name.startswith("assert") or f_str in ("pytest.raises", "unittest.TestCase.assertRaises"):
                assert_cnt += 1
                if base_name in ("assertEqual", "assertDictEqual", "assertListEqual", "assertSetEqual"):
                    eq_cnt += 1
                elif base_name == "assertIsNotNone":
                    notnone_cnt += 1
                elif base_name == "assertTrue" and node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value is True:
                    has_tautology = True
            if f_str in ("pytest.skip", "pytest.xfail") or base_name in ("skipTest",):
                has_skip = True
        elif isinstance(node, ast.ExceptHandler):
            t_str = ast.unparse(node.type) if (node.type and hasattr(ast, "unparse")) else "BaseException"
            # Check empty handler (`pass` only) or catching AssertionError/Exception without re-raise
            non_pass = [s for s in node.body if not isinstance(s, ast.Pass)]
            if not non_pass:
                has_swallow = True
            elif any(k in t_str for k in ("AssertionError", "BaseException")):
                has_swallow = True

    return {
        "non_empty": len(real_stmts) > 0,
        "assert_cnt": assert_cnt,
        "eq_cnt": eq_cnt,
        "notnone_cnt": notnone_cnt,
        "has_tautology": has_tautology,
        "has_skip": has_skip,
        "has_swallow": has_swallow,
    }


def check_py_basic_sanity(orig_methods: Dict[str, str], cand_methods: Dict[str, str]) -> Tuple[bool, List[str]]:
    errs: List[str] = []
    for mname, o_src in orig_methods.items():
        c_src = cand_methods.get(mname)
        if not c_src:
            continue
        c_info = _analyze_py_ast(c_src)
        o_info = _analyze_py_ast(o_src)
        if c_info is None:
            errs.append(f"Function `{mname}` has invalid Python syntax.")
            continue
        if not c_info["non_empty"]:
            errs.append(f"Function `{mname}` cannot have an empty/`pass` body.")
        if c_info["has_skip"] and not (o_info and o_info["has_skip"]):
            errs.append(f"Function `{mname}` cannot add `pytest.skip` / `xfail`.")
        if c_info["has_swallow"] and not (o_info and o_info["has_swallow"]):
            errs.append(f"Function `{mname}` cannot swallow exceptions or catch `AssertionError`.")
    return (len(errs) == 0), errs


def check_py_static_ast_guard(orig_methods: Dict[str, str], cand_methods: Dict[str, str]) -> Tuple[bool, List[str]]:
    violations: List[str] = []
    for mname, o_src in orig_methods.items():
        c_src = cand_methods.get(mname)
        if not c_src:
            continue
        c_info = _analyze_py_ast(c_src)
        o_info = _analyze_py_ast(o_src)
        if c_info is None:
            violations.append(f"Function `{mname}` failed Python AST parsing (`SyntaxError`).")
            continue
        if not c_info["non_empty"]:
            violations.append(f"Function `{mname}` has an empty/`pass` body.")
            continue
        if c_info["has_skip"] and not (o_info and o_info["has_skip"]):
            violations.append(f"Function `{mname}` introduces `skip`/`xfail`.")
        if c_info["has_tautology"] and not (o_info and o_info["has_tautology"]):
            violations.append(f"Function `{mname}` introduces tautological assertion (`assert True` or `assert x == x`).")
        if c_info["has_swallow"] and not (o_info and o_info["has_swallow"]):
            violations.append(f"Function `{mname}` swallows exceptions or catches `AssertionError`.")
        if o_info:
            if c_info["assert_cnt"] < o_info["assert_cnt"]:
                violations.append(
                    f"Function `{mname}` reduced assertion count from {o_info['assert_cnt']} to {c_info['assert_cnt']}."
                )
            if o_info["eq_cnt"] > 0 and c_info["eq_cnt"] == 0 and c_info["notnone_cnt"] > o_info["notnone_cnt"]:
                violations.append(
                    f"Function `{mname}` downgraded equality (`==`) to weak `is not None` checks."
                )
    return (len(violations) == 0), violations


def parse_py_llm_patch_response(raw_text: str) -> Dict[str, Any]:
    def extract_between(text: str, start_tag: str, end_tag: str) -> Optional[str]:
        s = text.find(start_tag)
        if s == -1:
            return None
        e = text.find(end_tag, s + len(start_tag))
        if e == -1:
            return None
        val = text[s + len(start_tag) : e].strip()
        val = re.sub(r"^```[a-zA-Z]*\s*", "", val)
        val = re.sub(r"\s*```$", "", val)
        return val.strip("\n") or None

    vic = extract_between(raw_text, "#<victim start>", "#<victim end>")
    pol = extract_between(raw_text, "#<polluter start>", "#<polluter end>")
    if not vic:
        vic = extract_between(raw_text, "//<victim start>", "//<victim end>") or extract_between(raw_text, "#<fix start>", "#<fix end>")
    if not pol:
        pol = extract_between(raw_text, "//<polluter start>", "//<polluter end>")
    imp_raw = extract_between(raw_text, "#<import start>", "#<import end>") or extract_between(raw_text, "//<import start>", "//<import end>")
    imps: List[str] = []
    if imp_raw:
        for ln in imp_raw.splitlines():
            ln = ln.strip()
            if ln.startswith(("import ", "from ")):
                imps.append(ln)
    if not vic:
        m = re.search(r"```(?:python)?\s*(def\s+.*?)```", raw_text, re.DOTALL)
        if m:
            vic = m.group(1).strip("\n")
    return {
        "victim_code": vic,
        "polluter_code": pol,
        "imports": imps,
    }


def build_py_repair_prompt(
    strategy: str,
    sample: Dict[str, Any],
    round_idx: int,
    prev_candidate: Optional[Dict[str, Any]] = None,
    feedback_msg: str = "",
) -> str:
    od_sub = sample.get("od_subtype", "OD-victim")
    use_guard = strategy in ("B2_Prompt_Guard", "B3_Static_AST_Guard", "Ours_MuGuard_Flake")

    if od_sub == "OD-brittle":
        role_desc = (
            "Order-Dependent Brittle (`OD-brittle`) Python pytest test: the target test passes when preceded by a "
            "state-setter test, but fails when executed standalone in isolation because required prerequisite state/fixture setup is missing.\n"
        )
        partner_label = "State-Setter Test"
    else:
        role_desc = (
            "Order-Dependent Victim (`OD-victim`) Python pytest test: the target test passes standalone in isolation, "
            "but fails when preceded by a polluter test that mutates shared global/module/class/filesystem/singleton state.\n"
        )
        partner_label = "Polluter Test"

    loc_block = (
        f"Target Flaky Test (`{od_sub}`): `{sample['test_full_name']}`\n"
        f"{partner_label}: `{sample['polluter_full_name']}`\n"
        f"Module Imports & Class/Fixture Context:\n```python\n{sample['context_header']}\n```\n"
        f"Original Target Test Function:\n```python\n{sample['orig_test_code']}\n```\n"
        f"Original {partner_label} Function:\n```python\n{sample['orig_polluter_code']}\n```\n"
        f"Failure Diagnostic:\n```\n{sample['flaky_error_summary']}\n```\n"
    )

    guard_section = f"\n{PROMPT_GUARD_CONTRACT_PY}\n" if use_guard else (
        "\nDo not use empty `pass` bodies or `try-except` to swallow assertion errors; ensure both standalone and ordered pytest schedules pass.\n"
    )

    round_section = ""
    if round_idx > 1 and prev_candidate is not None:
        prev_str = f"Target:\n{prev_candidate.get('victim_code')}\n\nPartner:\n{prev_candidate.get('polluter_code')}"
        round_section = (
            f"\n=== ITERATION {round_idx} FEEDBACK ON YOUR PREVIOUS CANDIDATE ===\n"
            f"Your Round {round_idx - 1} candidate was:\n```python\n{prev_str}\n```\n"
            f"Verification Feedback / Rejection Reason:\n{feedback_msg}\n"
            f"Please produce a corrected Python repair addressing the exact issue above.\n"
        )

    fmt = (
        "Reply with ALL code sections delimited using the exact tags below (no extra prose needed):\n"
        "1) Complete fixed target test function (`def ...`, preserving original function name and parameters):\n"
        "#<victim start>\n...complete Python target test function...\n#<victim end>\n"
        f"2) Complete {partner_label.lower()} function (include ONLY if you also add teardown cleanup to it; otherwise repeat unchanged):\n"
        "#<polluter start>\n...complete Python partner test function...\n#<polluter end>\n"
        "3) Additional Python `import ...` / `from ... import ...` lines if needed (leave empty if none):\n"
        "#<import start>\n#<import end>"
    )

    return (
        "You are an expert Python software testing engineer repairing an order-dependent flaky pytest test.\n"
        + role_desc
        + loc_block
        + guard_section
        + round_section
        + "\n"
        + fmt
    )


def format_py_mutant_witness_feedback(survived_mutants: List[Dict[str, Any]], mode: str = "full") -> str:
    """
    Support all 3 retry feedback granularities for RQ3 N14-style diagnostic ablation:
    - mode == "full": exact surviving focal mutant witness (`Class/Module::func:Line`, mutator, Python source slice)
    - mode == "binary": score-drop notification ONLY (no class/function/line/mutator location)
    - mode == "placebo": N14 mismatched mutant witness (caller passes non-surviving mutant(s))
    """
    if mode == "binary":
        return (
            "STATUS: EROSION_RETRY — Your candidate patch passed pytest stability schedules (W+), "
            f"but SILENTLY ERODED the test oracle: {len(survived_mutants)} deterministic focal mutant(s) "
            "that the original test killed 3/3 times NOW SURVIVED!\n"
            "Restore full deterministic assertion and SUT setup fidelity while preserving order-independence."
        )
    lines = [
        "STATUS: EROSION_RETRY — Your candidate patch passed pytest stability schedules (W+), "
        f"but SILENTLY ERODED the test oracle: {len(survived_mutants)} deterministic focal mutant(s) "
        "that the original test killed 3/3 times in clean state NOW SURVIVED!",
        "",
        "Surviving Deterministic Focal Mutant Counterexample Witness(es):",
    ]
    for idx, m in enumerate(survived_mutants[:4], start=1):
        lines.append(
            f"  [Mutant #{idx}] Focal Module `{m['mutatedClass']}` :: `{m['mutatedMethod']}` "
            f"(Line {m['lineNumber']}, Mutator: `{m['mutator']}`)\n"
            f"    Mutation Effect: {m['description']}\n"
            f"    Production Source Slice around Line {m['lineNumber']}:\n"
            f"    ```python\n{m.get('source_slice', '(line ' + str(m['lineNumber']) + ')')}\n    ```"
        )
    if len(survived_mutants) > 4:
        lines.append(f"  ... plus {len(survived_mutants) - 4} additional surviving deterministic focal mutant(s).")
    lines.extend([
        "",
        "MINIMAL-SCOPE REPAIR REQUIREMENT:",
        "- Keep order-independence (standalone and paired pytest schedules must pass),",
        "- BUT restore full deterministic SUT state setup and exact assertions so EVERY surviving mutant above fails an assertion again!",
    ])
    return "\n".join(lines)
