"""
FT-1 v2.0.0-frozen Guards, Affected-Scope Closure, Matched Witness Formatting,
and Bug-Fixed Subset-Sampling Utilities.
"""
import ast
import difflib
import hashlib
import math
import re
from typing import Any, Dict, List, Optional, Set, Tuple

HARNESS_VERSION = "2.3.0-certified"


def extract_pit_xml_indexes_and_blocks(m_el: Any) -> Tuple[str, str]:
    """
    Extract lossless bytecode instruction `index` and `block` identifiers across ALL PIT versions:
      - PIT 1.7.4 emits `<index>10</index>` and `<block>2</block>`.
      - PIT 1.9.11 / 1.15.2 emit `<indexes><index>10</index>...</indexes>` and `<blocks><block>2</block>...</blocks>`.
    """
    idx_vals: List[str] = []
    direct_idx = (m_el.findtext("index") or "").strip()
    if direct_idx:
        idx_vals.append(direct_idx)
    for el in m_el.findall("./indexes/index"):
        txt = (el.text or "").strip()
        if txt:
            idx_vals.append(txt)

    blk_vals: List[str] = []
    direct_blk = (m_el.findtext("block") or "").strip()
    if direct_blk:
        blk_vals.append(direct_blk)
    for el in m_el.findall("./blocks/block"):
        txt = (el.text or "").strip()
        if txt:
            blk_vals.append(txt)

    return ",".join(idx_vals), ",".join(blk_vals)


def java_pit_mutant_exact_id(m: Dict[str, Any]) -> str:
    """
    Lossless 8-field Java PIT mutant identity:
      (mutatedClass, mutatedMethod, methodDescription, lineNumber, mutator_full, index, block, description)
    Eliminates 4-tuple collisions across same-line/same-description bytecode sites.
    """
    mut_full = (m.get("mutator") or m.get("mutator_short") or "").strip()
    mut_short = mut_full.split(".")[-1] if mut_full else ""
    return (
        f"{m.get('mutatedClass', '')}::{m.get('mutatedMethod', '')}"
        f"{m.get('methodDescription', '')}:L{int(m.get('lineNumber', 0))}:"
        f"{mut_short}:idx={m.get('index', '')}:blk={m.get('block', '')}:{m.get('description', '')}"
    )


def resolve_pit_mutants_by_exact_id(
    muts: List[Dict[str, Any]],
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, str]]:
    """
    Aggregate PIT XML mutant rows by lossless `exact_id`.
    If multiple XML rows share the exact 8-tuple with conflicting verdicts (e.g., one KILLED and
    one SURVIVED/UNKNOWN), fail closed as `UNKNOWN_AMBIGUOUS_MUTANT_ID` rather than preferring KILLED.
    Returns `(by_exact_id, status_by_exact_id)`.
    """
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for m in muts:
        eid = m.get("exact_id") or java_pit_mutant_exact_id(m)
        m["exact_id"] = eid
        grouped.setdefault(eid, []).append(m)

    by_eid: Dict[str, Dict[str, Any]] = {}
    status_by_eid: Dict[str, str] = {}
    for eid, rows in grouped.items():
        if len(rows) == 1:
            r0 = rows[0]
            by_eid[eid] = r0
            status_by_eid[eid] = r0.get("status_norm", r0.get("status", "UNKNOWN"))
            continue
        verdicts = {
            ("KILLED" if r.get("attributed_kill") else ("UNKNOWN" if r.get("is_unknown") else r.get("status_norm", "SURVIVED")))
            for r in rows
        }
        if len(verdicts) == 1:
            r0 = dict(rows[0])
            r0["duplicate_exact_id_count"] = len(rows)
            by_eid[eid] = r0
            status_by_eid[eid] = r0.get("status_norm", r0.get("status", "UNKNOWN"))
        else:
            # Fail-closed on genuine exact-id status conflict!
            r0 = dict(rows[0])
            r0["attributed_kill"] = False
            r0["is_unknown"] = True
            r0["status_norm"] = "UNKNOWN_AMBIGUOUS_MUTANT_ID"
            r0["duplicate_exact_id_count"] = len(rows)
            r0["conflicting_statuses"] = sorted(verdicts)
            by_eid[eid] = r0
            status_by_eid[eid] = "UNKNOWN_AMBIGUOUS_MUTANT_ID"
    return by_eid, status_by_eid


def _parse_pit_killing_test(kt: str) -> Tuple[str, Optional[str]]:
    """
    Strictly parse PIT `<killingTest>` into `(exact_fqcn, exact_method_or_None)`.
    Supports:
      1) JUnit 5 engine path: `[engine:junit-jupiter]/[class:pkg.Cls]/[method:m()]` (with optional nested/template segments)
      2) JUnit 3/4 format: `pkg.Cls.method(pkg.Cls)` or `pkg.Cls.method[param](pkg.Cls)` or `pkg.Cls.method`
      3) Class-only format: `pkg.Cls`
    NEVER performs substring containment (`fqcn in kt`).
    """
    s = (kt or "").strip()
    if not s:
        return "", None

    # 1) JUnit 5 unique-id format `[engine:...]/[class:FQCN]/.../[method:NAME(...)]` or `[test-template:NAME(...)]`
    if "[class:" in s:
        cls_matches = re.findall(r"\[class:([^\]]+)\]", s)
        meth_matches = re.findall(r"\[(?:method|test-template):([^\]]+)\]", s)
        if not cls_matches:
            return "", None
        parsed_cls = cls_matches[-1].strip()
        if not meth_matches:
            return parsed_cls, None
        raw_m = meth_matches[-1].strip()
        parsed_m = raw_m.split("(", 1)[0].split("[", 1)[0].strip()
        return parsed_cls, parsed_m

    # 2) JUnit 3/4 `pkg.Cls.method(pkg.Cls)` or `pkg.Cls.method[0](pkg.Cls)`
    m_j4 = re.match(r"^([^\s(]+)\(([^()\s]+)\)$", s)
    if m_j4:
        lhs = m_j4.group(1).strip()
        outer_cls = m_j4.group(2).strip()
        if "." in lhs:
            cls_part, meth_part = lhs.rsplit(".", 1)
            # Outer class in parentheses must match class part (or nested class)
            if cls_part != outer_cls:
                return "", None
            parsed_m = meth_part.split("[", 1)[0].strip()
            return cls_part, parsed_m
        # Rarely `method(pkg.Cls)` without class prefix on lhs
        parsed_m = lhs.split("[", 1)[0].strip()
        return outer_cls, parsed_m

    # 3) Bare `pkg.Cls.method` vs class-only `pkg.Cls`
    # Distinguish method suffix (`pkg.Cls.method[param]` or `pkg.Cls.method`) only when compared against caller's `test_fqcn`
    return s, None


def match_java_pit_killing_test(
    status: str,
    killing_test: str,
    test_fqcn: str,
    test_method: str,
) -> Dict[str, Any]:
    """
    Exact Java PIT `<mutation>` node-and-status attribution (zero substring matching!).
      - `METHOD_EXACT`: `status == "KILLED"` and parsed `(cls, method)` matches `(test_fqcn, test_method)` EXACTLY
        (rejects package-prefix spoofing such as `other.com.acme.FooTest.testBar` and inherited parent methods).
      - `CLASS_ISOLATED_EXACT`: `status == "KILLED"` and `killing_test == test_fqcn` exactly (PIT's
        class-unit attribution when PIT omits method name on an isolated single-method test class).
      - `SURVIVED` / `NO_COVERAGE`: explicit non-kill (`is_unknown=False`, `attributed_kill=False`).
      - `TIMED_OUT`, `MEMORY_ERROR`, `RUN_ERROR`, `NON_VIABLE`, or `KILLED` by a non-matching test/class:
        marked `is_unknown=True` (never silently collapsed into valid survive or valid kill).
    """
    st = (status or "").strip().upper()
    kt = (killing_test or "").strip()
    fqcn = (test_fqcn or "").strip()
    meth = (test_method or "").strip()

    if st == "KILLED":
        if kt and fqcn:
            parsed_cls, parsed_meth = _parse_pit_killing_test(kt)
            if parsed_cls == fqcn and parsed_meth is not None and meth and parsed_meth == meth:
                return {
                    "attributed_kill": True,
                    "is_unknown": False,
                    "status_norm": "KILLED",
                    "attribution_granularity": "METHOD_EXACT",
                }
            # Bare `pkg.Cls.method` or `pkg.Cls.method[param]` without `(pkg.Cls)` suffix
            if parsed_meth is None and meth and parsed_cls.startswith(fqcn + "."):
                rem = parsed_cls[len(fqcn) + 1:]
                rem_base = rem.split("[", 1)[0].split("(", 1)[0]
                if "." not in rem_base and rem_base == meth:
                    return {
                        "attributed_kill": True,
                        "is_unknown": False,
                        "status_norm": "KILLED",
                        "attribution_granularity": "METHOD_EXACT",
                    }
            # Exact class-only attribution (when PIT reports class unit without method name)
            if kt == fqcn:
                return {
                    "attributed_kill": True,
                    "is_unknown": False,
                    "status_norm": "KILLED",
                    "attribution_granularity": "CLASS_ISOLATED_EXACT",
                }
        return {
            "attributed_kill": False,
            "is_unknown": True,
            "status_norm": "UNKNOWN_WRONG_TEST" if kt else "UNKNOWN_EMPTY_KILLING_TEST",
            "attribution_granularity": "NONE",
        }
    if st in ("SURVIVED", "NO_COVERAGE"):
        return {
            "attributed_kill": False,
            "is_unknown": False,
            "status_norm": st,
            "attribution_granularity": "NONE",
        }
    return {
        "attributed_kill": False,
        "is_unknown": True,
        "status_norm": f"UNKNOWN_{st or 'EMPTY'}",
        "attribution_granularity": "NONE",
    }


# ============================================================================
# 1. Java & Python Structural Correspondence Guard (B3+)
# ============================================================================

JAVA_COMMENT_STR_RE = re.compile(
    r'//.*?$|/\*.*?\*/|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'',
    re.DOTALL | re.MULTILINE,
)

JAVA_STRONG_EQ_NAMES = {
    "assertEquals",
    "assertArrayEquals",
    "assertIterableEquals",
    "assertLinesMatch",
    "isEqualTo",
    "containsExactly",
    "containsExactlyInAnyOrder",
    "containsOnly",
    "isEqualToComparingFieldByField",
}
JAVA_WEAK_EXIST_NAMES = {
    "assertNotNull",
    "isNotNull",
    "assertTrue",
    "assertFalse",
}
JAVA_SIZE_OR_PROJ_RE = re.compile(
    r"\.\s*(?:size|length|isEmpty|keySet|contains|startsWith|endsWith)\s*\("
)
JAVA_CLEAR_CLOBBER_RE = re.compile(
    r"(?:\.\s*(?:clear|reset|removeAll|invalidateAll)\s*\(\s*\)|\bSystem\.clearProperty\s*\()"
)
JAVA_UTILITY_CALLS = {
    "assertEquals", "assertArrayEquals", "assertIterableEquals", "assertLinesMatch",
    "assertTrue", "assertFalse", "assertNotNull", "assertNull", "assertSame", "assertNotSame",
    "assertThat", "assertThrows", "fail", "verify", "when", "given", "mock", "spy",
    "size", "length", "isEmpty", "get", "put", "add", "addAll", "remove", "clear", "reset",
    "contains", "containsKey", "containsValue", "keySet", "values", "entrySet", "iterator",
    "stream", "collect", "toList", "toSet", "sort", "sorted", "equals", "hashCode", "toString",
    "valueOf", "asList", "of", "emptyList", "emptyMap", "emptySet", "singletonList",
    "println", "print", "format", "trim", "split", "replace", "substring",
}


def strip_java_comments_keep_strings_masked(code: str) -> str:
    def _repl(m: re.Match) -> str:
        s = m.group(0)
        if s.startswith("//") or s.startswith("/*"):
            return " "
        return '"__STR__"'
    return JAVA_COMMENT_STR_RE.sub(_repl, code)


def _extract_balanced_call_args(text: str, open_paren_idx: int) -> Tuple[List[str], int]:
    """Return top-level comma-separated arguments inside `(...)` starting at `open_paren_idx`."""
    args: List[str] = []
    depth = 0
    cur: List[str] = []
    i = open_paren_idx
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "(":
            depth += 1
            if depth > 1:
                cur.append(ch)
        elif ch == ")":
            depth -= 1
            if depth == 0:
                arg_s = "".join(cur).strip()
                if arg_s:
                    args.append(arg_s)
                return args, i
            cur.append(ch)
        elif ch == "," and depth == 1:
            args.append("".join(cur).strip())
            cur = []
        else:
            if depth >= 1:
                cur.append(ch)
        i += 1
    return args, n - 1


def _extract_java_calls_with_pos(clean_code: str) -> List[Dict[str, Any]]:
    calls: List[Dict[str, Any]] = []
    for m in re.finditer(r"([A-Za-z_][A-Za-z0-9_.]*)\s*\(", clean_code):
        full_name = m.group(1)
        if full_name in ("if", "for", "while", "switch", "catch", "synchronized", "return", "throw", "new"):
            continue
        open_idx = m.end() - 1
        args, close_idx = _extract_balanced_call_args(clean_code, open_idx)
        short_name = full_name.split(".")[-1]
        calls.append({
            "full": full_name,
            "name": short_name,
            "args": args,
            "start": m.start(),
            "end": close_idx + 1,
            "raw": clean_code[m.start() : close_idx + 1],
        })
    return calls


def _extract_java_primary_callee_head(expr: str) -> Optional[str]:
    m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_.]*)\s*\(", expr)
    if not m:
        return None
    fn = m.group(1)
    short = fn.split(".")[-1]
    if short in JAVA_UTILITY_CALLS:
        return None
    return fn


def analyze_java_method_structure(method_src: str) -> Dict[str, Any]:
    clean = strip_java_comments_keep_strings_masked(method_src)
    b_start = clean.find("{")
    b_end = clean.rfind("}")
    body = clean[b_start + 1 : b_end].strip() if (b_start != -1 and b_end > b_start) else ""
    non_empty = len(body.replace(";", "").strip()) > 0

    has_ignore = bool(re.search(
        r"(?:@(?:org\.junit\.)?Ignore\b|@(?:org\.junit\.jupiter\.api\.)?Disabled\b|\bassumeTrue\s*\(\s*false\s*\)|\bassumeFalse\s*\(\s*true\s*\))",
        clean,
    ))
    has_swallow = bool(re.search(
        r"catch\s*\(\s*(?:final\s+)?(?:AssertionError|Throwable|Error|Exception)\s+[A-Za-z0-9_]+\s*\)\s*\{\s*\}",
        clean,
        re.DOTALL,
    )) or bool(re.search(r"catch\s*\(\s*(?:final\s+)?(?:AssertionError|Throwable|Error)\b", clean))

    calls = _extract_java_calls_with_pos(body)
    total_asserts = 0
    strong_value_eq = 0
    size_proj_eq = 0
    weak_exist_or_contains = 0
    self_or_dual_tautologies: List[str] = []
    sut_calls: List[Tuple[str, int]] = []
    first_assert_pos: Optional[int] = None

    for c in calls:
        nm = c["name"]
        fl = c["full"]
        args = c["args"]
        is_assert = (
            nm.startswith("assert")
            or nm in ("fail", "verify", "verifyNoMoreInteractions", "verifyZeroInteractions")
            or fl.endswith("JSONAssert.assertEquals")
        )
        if is_assert:
            total_asserts += 1
            if first_assert_pos is None or c["start"] < first_assert_pos:
                first_assert_pos = c["start"]

            if fl.endswith("JSONAssert.assertEquals"):
                # JSONAssert.assertEquals(exp, act, strict) -> strong if strict != false
                if len(args) >= 3 and args[-1].strip() == "false":
                    weak_exist_or_contains += 1
                else:
                    strong_value_eq += 1
            elif nm in JAVA_STRONG_EQ_NAMES:
                # Check 2-arg or 3-arg JUnit assertEquals
                cmp_args = args[-2:] if len(args) >= 2 else args
                if len(cmp_args) == 2:
                    lhs, rhs = cmp_args[0].strip(), cmp_args[1].strip()
                    if lhs == rhs and lhs not in ('"__STR__"', ""):
                        self_or_dual_tautologies.append(f"{nm}({lhs}, {rhs})")
                    else:
                        lh_call = _extract_java_primary_callee_head(lhs)
                        rh_call = _extract_java_primary_callee_head(rhs)
                        if lh_call and rh_call and lh_call == rh_call:
                            self_or_dual_tautologies.append(f"dual-SUT-call {nm}({lhs}, {rhs})")
                    if JAVA_SIZE_OR_PROJ_RE.search(lhs) or JAVA_SIZE_OR_PROJ_RE.search(rhs):
                        size_proj_eq += 1
                    else:
                        strong_value_eq += 1
                else:
                    strong_value_eq += 1
            elif nm in ("assertTrue", "assertFalse") and args:
                cond = args[-1]
                if cond.strip() in ("true", "false"):
                    self_or_dual_tautologies.append(f"{nm}({cond.strip()})")
                elif (".equals(" in cond or ("==" in cond and "null" not in cond)) and not JAVA_SIZE_OR_PROJ_RE.search(cond):
                    if "==" in cond:
                        parts_eq = [p.strip() for p in cond.split("==", 1)]
                        if len(parts_eq) == 2 and parts_eq[0] == parts_eq[1]:
                            self_or_dual_tautologies.append(f"{nm}({cond.strip()})")
                        else:
                            strong_value_eq += 1
                    else:
                        strong_value_eq += 1
                else:
                    weak_exist_or_contains += 1
            elif nm in JAVA_WEAK_EXIST_NAMES:
                weak_exist_or_contains += 1
        elif nm in ("isEqualTo", "containsExactly", "containsExactlyInAnyOrder", "containsOnly"):
            # AssertJ chained predicate
            if args and JAVA_SIZE_OR_PROJ_RE.search(args[0]):
                size_proj_eq += 1
            else:
                strong_value_eq += 1
        elif nm not in JAVA_UTILITY_CALLS and not nm.startswith("check"):
            sut_calls.append((nm, c["start"]))

    # Detect post-SUT state clobbering before first assertion
    post_act_clobber = False
    if sut_calls and first_assert_pos is not None:
        pre_assert_sut = [pos for _, pos in sut_calls if pos < first_assert_pos]
        if pre_assert_sut:
            first_sut_pos = min(pre_assert_sut)
            between_slice = body[first_sut_pos:first_assert_pos]
            if JAVA_CLEAR_CLOBBER_RE.search(between_slice):
                post_act_clobber = True

    return {
        "non_empty": non_empty,
        "has_ignore": has_ignore,
        "has_swallow": has_swallow,
        "total_asserts": total_asserts,
        "strong_value_eq": strong_value_eq,
        "size_proj_eq": size_proj_eq,
        "weak_exist_or_contains": weak_exist_or_contains,
        "tautologies": self_or_dual_tautologies,
        "sut_call_names": {nm for nm, _ in sut_calls},
        "post_act_clobber": post_act_clobber,
    }


def check_java_b3_plus(
    orig_methods: Dict[str, str],
    cand_methods: Dict[str, str],
    extra_helper_code: str = "",
) -> Tuple[bool, List[Dict[str, str]]]:
    """
    Upgraded Structural Correspondence Guard (B3+) for Java.
    Returns `(passed, structured_violations)`.
    """
    reasons: List[Dict[str, str]] = []
    helper_calls: Set[str] = set()
    if extra_helper_code:
        hc = strip_java_comments_keep_strings_masked(extra_helper_code)
        helper_calls = {c["name"] for c in _extract_java_calls_with_pos(hc)}

    for role, o_src in orig_methods.items():
        c_src = cand_methods.get(role)
        if not c_src:
            continue
        o = analyze_java_method_structure(o_src)
        c = analyze_java_method_structure(c_src)

        if not c["non_empty"]:
            reasons.append({"code": "R_EMPTY_BODY", "role": role, "detail": f"`{role}` has an empty method body."})
            continue
        if c["has_ignore"] and not o["has_ignore"]:
            reasons.append({"code": "R_SKIP_INTRODUCED", "role": role, "detail": f"`{role}` introduces `@Ignore`/`@Disabled`/`assume`."})
        if c["has_swallow"] and not o["has_swallow"]:
            reasons.append({"code": "R_EXCEPTION_SWALLOW", "role": role, "detail": f"`{role}` introduces empty `catch` or catches `AssertionError`/`Throwable`."})
        if c["tautologies"] and len(c["tautologies"]) > len(o["tautologies"]):
            reasons.append({"code": "R_TAUTOLOGY_OR_DUAL_SUT", "role": role, "detail": f"`{role}` introduces tautological/dual-SUT comparison: {c['tautologies'][0]}."})
        if c["total_asserts"] < o["total_asserts"]:
            reasons.append({
                "code": "R_ASSERT_COUNT_DROP",
                "role": role,
                "detail": f"`{role}` reduced total assertions from {o['total_asserts']} to {c['total_asserts']}.",
            })
        if c["strong_value_eq"] < o["strong_value_eq"]:
            if c["size_proj_eq"] > o["size_proj_eq"] or c["weak_exist_or_contains"] > o["weak_exist_or_contains"]:
                reasons.append({
                    "code": "R_PROJECTION_OR_WEAK_DOWNGRADE",
                    "role": role,
                    "detail": (
                        f"`{role}` downgraded strong value equality ({o['strong_value_eq']} -> {c['strong_value_eq']}) "
                        f"into projection/weak checks (size/contains/notNull)."
                    ),
                })
            else:
                reasons.append({
                    "code": "R_STRONG_PREDICATE_DROP",
                    "role": role,
                    "detail": f"`{role}` reduced strong value-equality assertions from {o['strong_value_eq']} to {c['strong_value_eq']}.",
                })
        # Check focal SUT call preservation (for victim/target test)
        if role == "victim" and o["sut_call_names"]:
            retained = c["sut_call_names"] | helper_calls
            dropped = o["sut_call_names"] - retained
            added = retained - o["sut_call_names"]
            if dropped and (len(c["sut_call_names"]) == 0 or (len(added) == 0 and len(retained) < len(o["sut_call_names"]))):
                reasons.append({
                    "code": "R_SUT_CALL_REMOVED",
                    "role": role,
                    "detail": f"`{role}` removed focal SUT call(s) ({sorted(dropped)}) without replacement.",
                })
        if c["post_act_clobber"] and not o["post_act_clobber"]:
            reasons.append({
                "code": "R_POST_ACT_STATE_CLOBBER",
                "role": role,
                "detail": f"`{role}` clears/resets shared state between SUT execution and assertions.",
            })

    return (len(reasons) == 0), reasons


PY_UTILITY_FUNCS = {
    "len", "set", "list", "dict", "tuple", "sorted", "str", "int", "float", "bool",
    "isinstance", "hasattr", "getattr", "setattr", "delattr", "print", "range", "enumerate",
    "zip", "any", "all", "min", "max", "sum", "abs", "type", "id", " copy", "deepcopy",
}


def _dedent_py(src: str) -> str:
    lines = [ln for ln in src.splitlines() if ln.strip()]
    if not lines:
        return ""
    ind = min(len(ln) - len(ln.lstrip()) for ln in lines)
    return "\n".join(ln[ind:] if ln.strip() else "" for ln in src.splitlines())


def _is_py_proj_expr(node: ast.AST) -> bool:
    if isinstance(node, ast.Call):
        fn_s = ast.unparse(node.func) if hasattr(ast, "unparse") else ""
        base = fn_s.split(".")[-1]
        if base in ("len", "keys", "startswith", "endswith"):
            return True
    return False


def _py_primary_sut_callee(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Call) and hasattr(ast, "unparse"):
        fn_s = ast.unparse(node.func)
        base = fn_s.split(".")[-1]
        if base not in PY_UTILITY_FUNCS and not base.startswith("assert"):
            return fn_s
    return None


def analyze_py_method_structure(func_src: str) -> Optional[Dict[str, Any]]:
    try:
        tree = ast.parse(_dedent_py(func_src))
    except SyntaxError:
        return None
    fns = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    if not fns:
        return None
    fn = fns[0]

    real_stmts = [
        s for s in fn.body
        if not isinstance(s, ast.Pass)
        and not (isinstance(s, ast.Expr) and isinstance(getattr(s, "value", None), ast.Constant) and isinstance(s.value.value, str))
    ]
    has_skip = any(
        any(k in (ast.unparse(d) if hasattr(ast, "unparse") else "") for k in ("skip", "xfail"))
        for d in fn.decorator_list
    )
    has_swallow = False
    total_asserts = 0
    strong_value_eq = 0
    proj_or_in_checks = 0
    weak_exist_checks = 0
    tautologies: List[str] = []
    sut_calls: List[Tuple[str, int]] = []
    clear_positions: List[int] = []
    first_assert_lineno: Optional[int] = None

    for node in ast.walk(fn):
        lno = getattr(node, "lineno", 0)
        if isinstance(node, ast.ExceptHandler):
            t_str = ast.unparse(node.type) if (node.type and hasattr(ast, "unparse")) else "BaseException"
            non_pass = [s for s in node.body if not isinstance(s, ast.Pass)]
            if not non_pass or any(k in t_str for k in ("AssertionError", "BaseException")):
                has_swallow = True
        elif isinstance(node, ast.Assert):
            total_asserts += 1
            if first_assert_lineno is None or lno < first_assert_lineno:
                first_assert_lineno = lno
            t = node.test
            if isinstance(t, ast.Constant) and bool(t.value) is True:
                tautologies.append("assert True")
            elif isinstance(t, ast.Compare) and len(t.ops) == 1:
                op = t.ops[0]
                lhs, rhs = t.left, t.comparators[0]
                if isinstance(op, ast.Eq):
                    lhs_s = ast.unparse(lhs) if hasattr(ast, "unparse") else ""
                    rhs_s = ast.unparse(rhs) if hasattr(ast, "unparse") else ""
                    if lhs_s and lhs_s == rhs_s:
                        tautologies.append(f"assert {lhs_s} == {rhs_s}")
                    else:
                        lc, rc_ = _py_primary_sut_callee(lhs), _py_primary_sut_callee(rhs)
                        if lc and rc_ and lc == rc_:
                            tautologies.append(f"dual-SUT assert {lhs_s} == {rhs_s}")
                    if _is_py_proj_expr(lhs) or _is_py_proj_expr(rhs):
                        proj_or_in_checks += 1
                    else:
                        strong_value_eq += 1
                elif isinstance(op, (ast.In, ast.NotIn)):
                    proj_or_in_checks += 1
                elif isinstance(op, ast.IsNot) and isinstance(rhs, ast.Constant) and rhs.value is None:
                    weak_exist_checks += 1
                else:
                    weak_exist_checks += 1
            else:
                weak_exist_checks += 1
        elif isinstance(node, ast.Call):
            f_str = ast.unparse(node.func) if hasattr(ast, "unparse") else ""
            base = f_str.split(".")[-1]
            if f_str in ("pytest.skip", "pytest.xfail") or base == "skipTest":
                has_skip = True
            elif base.startswith("assert") or f_str in ("pytest.raises", "unittest.TestCase.assertRaises"):
                total_asserts += 1
                if first_assert_lineno is None or lno < first_assert_lineno:
                    first_assert_lineno = lno
                if base in ("assertEqual", "assertDictEqual", "assertListEqual", "assertSetEqual"):
                    if len(node.args) >= 2 and (_is_py_proj_expr(node.args[0]) or _is_py_proj_expr(node.args[1])):
                        proj_or_in_checks += 1
                    else:
                        strong_value_eq += 1
                elif base in ("assertIn",):
                    proj_or_in_checks += 1
                elif base in ("assertIsNotNone", "assertTrue", "assertFalse"):
                    if base == "assertTrue" and node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value is True:
                        tautologies.append("assertTrue(True)")
                    weak_exist_checks += 1
                elif f_str in ("pytest.raises", "unittest.TestCase.assertRaises"):
                    strong_value_eq += 1
            elif base in ("clear", "reset", "cache_clear"):
                clear_positions.append(lno)
            elif base not in PY_UTILITY_FUNCS:
                sut_calls.append((base, lno))

    post_act_clobber = False
    if sut_calls and first_assert_lineno is not None and clear_positions:
        pre_sut = [ln for _, ln in sut_calls if ln < first_assert_lineno]
        if pre_sut:
            first_sut_ln = min(pre_sut)
            if any(first_sut_ln < cl < first_assert_lineno for cl in clear_positions):
                post_act_clobber = True

    return {
        "non_empty": len(real_stmts) > 0,
        "has_skip": has_skip,
        "has_swallow": has_swallow,
        "total_asserts": total_asserts,
        "strong_value_eq": strong_value_eq,
        "proj_or_in_checks": proj_or_in_checks,
        "weak_exist_checks": weak_exist_checks,
        "tautologies": tautologies,
        "sut_call_names": {nm for nm, _ in sut_calls},
        "post_act_clobber": post_act_clobber,
    }


def check_py_b3_plus(
    orig_methods: Dict[str, str],
    cand_methods: Dict[str, str],
) -> Tuple[bool, List[Dict[str, str]]]:
    reasons: List[Dict[str, str]] = []
    for role, o_src in orig_methods.items():
        c_src = cand_methods.get(role)
        if not c_src:
            continue
        o = analyze_py_method_structure(o_src)
        c = analyze_py_method_structure(c_src)
        if c is None:
            reasons.append({"code": "R_SYNTAX_ERROR", "role": role, "detail": f"`{role}` failed Python AST parsing."})
            continue
        if not c["non_empty"]:
            reasons.append({"code": "R_EMPTY_BODY", "role": role, "detail": f"`{role}` has an empty/`pass` body."})
            continue
        if o is None:
            continue
        if c["has_skip"] and not o["has_skip"]:
            reasons.append({"code": "R_SKIP_INTRODUCED", "role": role, "detail": f"`{role}` introduces `skip`/`xfail`."})
        if c["has_swallow"] and not o["has_swallow"]:
            reasons.append({"code": "R_EXCEPTION_SWALLOW", "role": role, "detail": f"`{role}` swallows exceptions or catches `AssertionError`."})
        if c["tautologies"] and len(c["tautologies"]) > len(o["tautologies"]):
            reasons.append({"code": "R_TAUTOLOGY_OR_DUAL_SUT", "role": role, "detail": f"`{role}` introduces tautology: {c['tautologies'][0]}."})
        if c["total_asserts"] < o["total_asserts"]:
            reasons.append({
                "code": "R_ASSERT_COUNT_DROP",
                "role": role,
                "detail": f"`{role}` reduced assertions from {o['total_asserts']} to {c['total_asserts']}.",
            })
        if c["strong_value_eq"] < o["strong_value_eq"]:
            if c["proj_or_in_checks"] > o["proj_or_in_checks"] or c["weak_exist_checks"] > o["weak_exist_checks"]:
                reasons.append({
                    "code": "R_PROJECTION_OR_WEAK_DOWNGRADE",
                    "role": role,
                    "detail": f"`{role}` downgraded equality ({o['strong_value_eq']} -> {c['strong_value_eq']}) into `len`/`in`/`is not None`.",
                })
            else:
                reasons.append({
                    "code": "R_STRONG_PREDICATE_DROP",
                    "role": role,
                    "detail": f"`{role}` reduced strong value assertions from {o['strong_value_eq']} to {c['strong_value_eq']}.",
                })
        if role == "victim" and o["sut_call_names"]:
            dropped = o["sut_call_names"] - c["sut_call_names"]
            added = c["sut_call_names"] - o["sut_call_names"]
            if dropped and (len(c["sut_call_names"]) == 0 or (len(added) == 0 and len(c["sut_call_names"]) < len(o["sut_call_names"]))):
                reasons.append({
                    "code": "R_SUT_CALL_REMOVED",
                    "role": role,
                    "detail": f"`{role}` removed focal SUT call(s) ({sorted(dropped)}) without replacement.",
                })
        if c["post_act_clobber"] and not o["post_act_clobber"]:
            reasons.append({
                "code": "R_POST_ACT_STATE_CLOBBER",
                "role": role,
                "detail": f"`{role}` clears/resets state between SUT call and assertion.",
            })
    return (len(reasons) == 0), reasons


# ============================================================================
# 2. Affected-Scope Diff & Role-Tagged Joint Obligation Accounting
# ============================================================================

def compute_unified_diff(orig_src: str, new_src: str, fname: str) -> str:
    if orig_src == new_src:
        return ""
    return "".join(
        difflib.unified_diff(
            orig_src.splitlines(keepends=True),
            new_src.splitlines(keepends=True),
            fromfile=f"a/{fname}",
            tofile=f"b/{fname}",
        )
    )


def extract_declared_java_method_name(method_code: str) -> Optional[str]:
    clean = strip_java_comments_keep_strings_masked(method_code)
    m = re.search(
        r"(?:public|protected|private|static|final|synchronized|\s)*[\w<>\[\],\s]+\s+([A-Za-z_][A-Za-z0-9_]*)\s*\([^)]*\)\s*(?:throws\s+[\w.,\s]+)?\{",
        clean,
    )
    return m.group(1) if m else None


def is_code_semantically_changed(language: str, orig_code: str, cand_code: Optional[str]) -> bool:
    """
    Return True ONLY if `cand_code` is non-empty and differs from `orig_code` beyond
    indentation, blank lines, comments, or quote-style AST formatting (when LLM repeats
    the partner method unchanged as instructed by the prompt template).
    """
    if not cand_code or not cand_code.strip():
        return False
    if not orig_code or not orig_code.strip():
        return True
    if language == "python":
        try:
            t_o = ast.parse(_dedent_py(orig_code))
            t_c = ast.parse(_dedent_py(cand_code))
            if hasattr(ast, "unparse"):
                return ast.unparse(t_o).strip() != ast.unparse(t_c).strip()
            return ast.dump(t_o) != ast.dump(t_c)
        except SyntaxError:
            return _dedent_py(orig_code).strip() != _dedent_py(cand_code).strip()
    else:
        def _norm_java_tokens(s: str) -> str:
            # Preserve string and char literals verbatim; only strip comments and normalize outside whitespace!
            tok_re = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|//[^\n]*|/\*[\s\S]*?\*/|\s+|[^\s"\'/]+|/')
            parts: List[str] = []
            for m in tok_re.finditer(s):
                tok = m.group(0)
                if tok.startswith("//") or tok.startswith("/*"):
                    continue
                if tok.isspace():
                    if parts and parts[-1] != " ":
                        parts.append(" ")
                else:
                    parts.append(tok)
            return "".join(parts).strip()
        return _norm_java_tokens(orig_code) != _norm_java_tokens(cand_code)


def analyze_affected_test_scope(
    language: str,
    test_type: str,
    orig_vic_file_src: str,
    new_vic_file_src: str,
    orig_pol_file_src: str,
    new_pol_file_src: str,
    same_file: bool,
    orig_target_method_src: str,
    new_target_method_src: str,
    orig_partner_method_src: str = "",
    new_partner_method_src: str = "",
    pom_modified: bool = False,
) -> Dict[str, Any]:
    """
    Determine whether candidate edits are strictly confined to `target_method` (+ purely additive imports)
    or also affect `partner_method`, rebind/remove existing imports, or modify shared class/module fixtures/helpers.
    """
    def _extract_imports(src: str, lang: str) -> Set[str]:
        imps: Set[str] = set()
        for ln in src.splitlines():
            s = ln.strip()
            if lang == "java" and s.startswith("import ") and s.endswith(";"):
                imps.add(re.sub(r"\s+", " ", s))
            elif lang == "python" and (s.startswith("import ") or s.startswith("from ")):
                imps.add(re.sub(r"\s+", " ", s))
        return imps

    def _norm_no_imports(src: str, lang: str) -> str:
        out_lns = []
        for ln in src.splitlines():
            s = ln.strip()
            if not s:
                continue
            if lang == "java" and s.startswith("import ") and s.endswith(";"):
                continue
            if lang == "python" and (s.startswith("import ") or s.startswith("from ")):
                continue
            out_lns.append(s)
        return "\n".join(out_lns)

    partner_method_changed = bool(
        test_type == "OD"
        and is_code_semantically_changed(language, orig_partner_method_src, new_partner_method_src)
    )

    def _extract_imported_symbols(imps: Set[str], lang: str) -> Tuple[Set[str], bool]:
        syms: Set[str] = set()
        has_wildcard = False
        for s in imps:
            if lang == "java":
                if s.endswith(".*;"):
                    has_wildcard = True
                else:
                    body = s[:-1].strip().split()[-1]
                    syms.add(body.split(".")[-1])
            else:
                if " import *" in s:
                    has_wildcard = True
                elif s.startswith("from ") and " import " in s:
                    rhs = s.split(" import ", 1)[1]
                    for part in rhs.split(","):
                        p = part.strip()
                        if " as " in p:
                            syms.add(p.split(" as ", 1)[1].strip())
                        elif p:
                            syms.add(p.strip())
                elif s.startswith("import "):
                    rhs = s[len("import "):]
                    for part in rhs.split(","):
                        p = part.strip()
                        if " as " in p:
                            syms.add(p.split(" as ", 1)[1].strip())
                        elif p:
                            syms.add(p.split(".")[0].strip())
        return syms, has_wildcard

    def _imports_rebound_or_shadowed(orig_imps: Set[str], new_imps: Set[str], lang: str) -> bool:
        if orig_imps - new_imps:
            return True
        added = new_imps - orig_imps
        if not added:
            return False
        orig_syms, _ = _extract_imported_symbols(orig_imps, lang)
        add_syms, add_wild = _extract_imported_symbols(added, lang)
        return bool(add_wild or (orig_syms & add_syms))

    # Detect removed/replaced OR shadowing imports (`from real_sut import X` -> `from fake_sut import X`)!
    orig_vic_imps = _extract_imports(orig_vic_file_src, language)
    new_vic_imps = _extract_imports(new_vic_file_src, language)
    import_rebound = _imports_rebound_or_shadowed(orig_vic_imps, new_vic_imps, language)
    if not same_file and test_type == "OD":
        orig_pol_imps = _extract_imports(orig_pol_file_src, language)
        new_pol_imps = _extract_imports(new_pol_file_src, language)
        if _imports_rebound_or_shadowed(orig_pol_imps, new_pol_imps, language):
            import_rebound = True

    # Check if non-import text outside target_method (and outside partner_method) changed!
    vic_residual_orig = orig_vic_file_src.replace(orig_target_method_src, "/*__TARGET__*/", 1)
    vic_residual_new = new_vic_file_src.replace(new_target_method_src, "/*__TARGET__*/", 1) if new_target_method_src else new_vic_file_src
    if same_file and orig_partner_method_src:
        vic_residual_orig = vic_residual_orig.replace(orig_partner_method_src, "/*__PARTNER__*/", 1)
        if new_partner_method_src:
            vic_residual_new = vic_residual_new.replace(new_partner_method_src, "/*__PARTNER__*/", 1)

    shared_fixture_or_helper_changed = (
        import_rebound
        or (_norm_no_imports(vic_residual_orig, language) != _norm_no_imports(vic_residual_new, language))
    )
    if not same_file and test_type == "OD":
        pol_res_orig = orig_pol_file_src.replace(orig_partner_method_src, "/*__PARTNER__*/", 1) if orig_partner_method_src else orig_pol_file_src
        pol_res_new = new_pol_file_src.replace(new_partner_method_src, "/*__PARTNER__*/", 1) if new_partner_method_src else new_pol_file_src
        if _norm_no_imports(pol_res_orig, language) != _norm_no_imports(pol_res_new, language):
            shared_fixture_or_helper_changed = True
            partner_method_changed = True

    affects_partner = bool(
        (test_type == "OD" and (partner_method_changed or shared_fixture_or_helper_changed or pom_modified))
        or import_rebound
    )
    if import_rebound:
        scope_kind = "IMPORT_REBINDING_OR_SHARED_SCOPE"
    elif shared_fixture_or_helper_changed:
        scope_kind = "SHARED_FIXTURE_OR_HELPER_MODIFIED"
    elif partner_method_changed:
        scope_kind = "PARTNER_METHOD_MODIFIED"
    else:
        scope_kind = "TARGET_ONLY"

    return {
        "scope_kind": scope_kind,
        "import_rebound": import_rebound,
        "partner_method_changed": partner_method_changed,
        "shared_fixture_or_helper_changed": shared_fixture_or_helper_changed,
        "pom_modified": pom_modified,
        "affects_partner_or_shared": affects_partner,
    }


def compute_role_tagged_retention(
    target_m_det: List[Dict[str, Any]],
    target_killed_ids: Set[str],
    test_type: str,
    partner_m_det: List[Dict[str, Any]],
    affects_partner: bool,
    partner_eval_ran: bool,
    partner_eval_ok: bool,
    partner_killed_ids: Optional[Set[str]],
    target_unknown_ids: Optional[List[str]] = None,
    partner_unknown_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Closed-fail, role-tagged `(role, mutant_id)` obligation accounting:
      - Never merges a mutant protected by both `target` and `partner` into a single obligation.
      - Never folds `UNKNOWN` mutant verdicts into `SURVIVED` (`target_unknown_ids` / `partner_unknown_ids`
        trigger closed-fail `TARGET_MUT_UNKNOWN` / `PARTNER_MUT_UNKNOWN` override and `gate_safe = False`).
      - Never outputs `gate_safe = True` when `affects_partner` is True and either
        `len(partner_m_det) == 0` (`PARTNER_UNCOVERED`) or `not partner_eval_ok` (`PARTNER_EVAL_ERROR`).
    """
    t_unk_set = set(target_unknown_ids or [])
    p_unk_set = set(partner_unknown_ids or [])
    t_tot = len(target_m_det)
    t_surv = [m for m in target_m_det if m["id"] not in target_killed_ids and m["id"] not in t_unk_set]
    t_kill = sum(1 for m in target_m_det if m["id"] in target_killed_ids and m["id"] not in t_unk_set)
    t_ret = round(t_kill / t_tot, 4) if (t_tot > 0 and not t_unk_set) else None

    p_tot = len(partner_m_det) if test_type == "OD" else 0
    p_surv: List[Dict[str, Any]] = []
    p_kill: Optional[int] = None
    p_ret: Optional[float] = None
    gate_status_override: Optional[str] = "TARGET_MUT_UNKNOWN" if t_unk_set else None

    if test_type == "OD":
        if affects_partner:
            if p_tot == 0:
                gate_status_override = gate_status_override or "ABSTAIN_PARTNER_UNCOVERED"
            elif not partner_eval_ran or not partner_eval_ok or partner_killed_ids is None:
                gate_status_override = gate_status_override or "PARTNER_MUT_EVAL_ERROR"
            elif p_unk_set:
                gate_status_override = gate_status_override or "PARTNER_MUT_UNKNOWN"
                for pm in partner_m_det:
                    if pm["id"] not in partner_killed_ids and pm["id"] not in p_unk_set:
                        pm_tag = dict(pm)
                        pm_tag["role"] = "partner"
                        pm_tag["description"] = "[Partner Obligation Eroded] " + pm_tag.get("description", "")
                        p_surv.append(pm_tag)
                p_kill = sum(1 for pm in partner_m_det if pm["id"] in partner_killed_ids and pm["id"] not in p_unk_set)
                p_ret = None
            else:
                for pm in partner_m_det:
                    if pm["id"] not in partner_killed_ids:
                        pm_tag = dict(pm)
                        pm_tag["role"] = "partner"
                        pm_tag["description"] = "[Partner Obligation Eroded] " + pm_tag.get("description", "")
                        p_surv.append(pm_tag)
                p_kill = p_tot - len(p_surv)
                p_ret = round(p_kill / p_tot, 4)
        else:
            # Partner and shared fixtures untouched -> standalone clean-e_0 behavior of partner is unchanged
            if p_tot > 0:
                p_kill = p_tot
                p_ret = 1.0

    # Role-tagged joint retention over `(target, m)` union `(partner, m)`
    if gate_status_override is not None:
        j_tot = t_tot + p_tot
        j_kill = t_kill + (p_kill if p_kill is not None else 0)
        j_ret = None
    elif p_tot > 0 and p_kill is not None:
        j_tot = t_tot + p_tot
        j_kill = t_kill + p_kill
        j_ret = round(j_kill / j_tot, 4) if j_tot > 0 else None
    else:
        j_tot = t_tot
        j_kill = t_kill
        j_ret = t_ret

    tagged_target_surv = []
    for tm in t_surv:
        tm_c = dict(tm)
        tm_c["role"] = "target"
        tagged_target_surv.append(tm_c)

    all_surv = tagged_target_surv + p_surv
    eroded = (len(all_surv) > 0) and (gate_status_override not in ("TARGET_MUT_UNKNOWN", "PARTNER_MUT_UNKNOWN", "PARTNER_MUT_EVAL_ERROR"))
    gate_safe = (gate_status_override is None) and (len(all_surv) == 0) and (t_tot > 0)

    return {
        "target_m_det_total": t_tot,
        "target_m_det_killed": t_kill,
        "target_retention": t_ret,
        "partner_m_det_total": p_tot,
        "partner_m_det_killed": p_kill,
        "partner_retention": p_ret,
        "joint_m_det_total": j_tot,
        "joint_m_det_killed": j_kill,
        "joint_retention": j_ret,
        "survived_mutants": all_surv,
        "eroded": eroded,
        "gate_status_override": gate_status_override,
        "gate_safe": gate_safe,
    }


# ============================================================================
# 3. Prefix-Forked Matched Feedback Builder (A1 / A2 / A3)
# ============================================================================

COMMON_REPAIR_FOOTER_JAVA = (
    "MINIMAL-SCOPE REPAIR REQUIREMENT:\n"
    "- Preserve your flakiness stabilization (order-insensitivity for ID, or pre-test/post-polluter state cleanup for OD),\n"
    "- Restore full deterministic predicate and setup strength so every baseline focal obligation is killed again,\n"
    "- Ensure state resets happen ONLY before setting up the test's own SUT state (or in `finally` after polluter assertions)."
)

COMMON_REPAIR_FOOTER_PY = (
    "MINIMAL-SCOPE REPAIR REQUIREMENT:\n"
    "- Preserve order-independence across standalone and paired pytest schedules,\n"
    "- Restore full deterministic SUT setup and exact assertions so every baseline focal obligation is killed again,\n"
    "- Never clear or overwrite the SUT state under test between the focal action and assertions."
)


def build_matched_ablation_feedback(
    language: str,
    total_obligations: int,
    survived_mutants: List[Dict[str, Any]],
    non_survived_pool: List[Dict[str, Any]],
    mode: str,
) -> Dict[str, Any]:
    """
    Construct strictly controlled feedback payloads for:
      - `A1_ScalarRatio`: identical header + scalar `k / M` count + identical `COMMON_REPAIR_FOOTER` (concise, natural, no junk padding).
      - `A2_MisleadingLocation`: identical header + `m` already-killed focal mutants (matched count `m` & template) + identical `COMMON_REPAIR_FOOTER`.
      - `A3_TrueWitness`: identical header + `m` true surviving focal mutants + identical `COMMON_REPAIR_FOOTER`.
    """
    n_surv = len(survived_mutants)
    n_killed = max(0, total_obligations - n_surv)
    footer = COMMON_REPAIR_FOOTER_PY if language == "python" else COMMON_REPAIR_FOOTER_JAVA
    fence = "python" if language == "python" else "java"

    header = (
        f"STATUS: EROSION_RETRY — Your candidate patch passed stability validation (W+), "
        f"but killed only {n_killed}/{total_obligations} deterministic focal obligations "
        f"({n_surv} baseline obligation(s) survived)!"
    )

    if mode == "A1_ScalarRatio":
        txt = f"{header}\n\n{footer}"
        return {
            "mode": mode,
            "shown_count": 0,
            "char_len": len(txt),
            "approx_tokens": max(1, round(len(txt) / 3.8)),
            "text": txt,
        }

    # Match item count `m` strictly when non_survived_pool is used
    if mode == "A2_MisleadingLocation":
        m_cap = min(4, n_surv, len(non_survived_pool)) if non_survived_pool else min(4, n_surv)
        chosen = non_survived_pool[:m_cap]
    else:
        m_cap = min(4, n_surv, len(non_survived_pool)) if non_survived_pool else min(4, n_surv)
        if m_cap == 0 and n_surv > 0:
            m_cap = min(4, n_surv)
        chosen = survived_mutants[:m_cap]

    lines = [header, "", "Diagnostic Focal Mutant Witness(es):"]
    for idx, m in enumerate(chosen, start=1):
        role_prefix = "[Partner] " if m.get("role") == "partner" else ""
        mut_short = m.get("mutator", "").split(".")[-1]
        sl = m.get("source_slice") or f"(line {m.get('lineNumber', 0)})"
        lines.append(
            f"  [Mutant #{idx}] {role_prefix}`{m.get('mutatedClass', '')}` :: `{m.get('mutatedMethod', '')}` "
            f"(Line {m.get('lineNumber', 0)}, Mutator: `{mut_short}`)\n"
            f"    Mutation Effect: {m.get('description', '')}\n"
            f"    Production Source Slice:\n"
            f"    ```{fence}\n{sl}\n    ```"
        )
    if n_surv > len(chosen):
        lines.append(f"  ... plus {n_surv - len(chosen)} additional surviving focal obligation(s).")
    lines.extend(["", footer])
    txt = "\n".join(lines)
    return {
        "mode": mode,
        "shown_count": len(chosen),
        "char_len": len(txt),
        "approx_tokens": max(1, round(len(txt) / 3.8)),
        "text": txt,
    }


# ============================================================================
# 4. Bug-Fixed Top-k Subset-Sampling Sensitivity Analysis
# ============================================================================

def _hypergeom_miss_prob(N: int, S: int, k: int) -> float:
    """Exact probability `C(N - S, k) / C(N, k)` that `k` draws without replacement miss all `S` survivors."""
    if S <= 0:
        return 1.0
    if k >= N or k > (N - S):
        return 0.0
    return math.comb(N - S, k) / math.comb(N, k)


def _mutator_priority_rank(mutator_str: str, desc_str: str) -> int:
    ms = (mutator_str + " " + desc_str).lower()
    if any(k in ms for k in ("negate", "conditional", "compare", "boundary", "bool")):
        return 0  # W-_cond
    if any(k in ms for k in ("return", "math", "binop", "constant", "primitive", "true", "false", "null")):
        return 1  # W-_val / bool
    if any(k in ms for k in ("void", "empty")):
        return 2  # W-_void / empty
    return 3


def compute_v2_topk_sensitivity(
    eroded_patch_records: List[Dict[str, Any]],
    k_budgets: Tuple[int, ...] = (1, 3, 5, 10, 15, 25),
    include_partner_in_pool: bool = True,
) -> Dict[str, Any]:
    """
    Bug-fixed offline subset-sampling sensitivity analysis over unique eroded patches:
      1) Uses `(role, file_or_class, lineNumber)` so same line numbers in different files NEVER collide.
      2) Never fabricates `max(1, ...)` target survivors when erosion occurred only in `partner`.
      3) Cleanly separates `joint_pool` (`target + partner` obligations when `include_partner_in_pool=True`)
         vs `target_only_pool` (when `include_partner_in_pool=False`, partner-only erosion has 0 target survivors -> 0% recall, even at `All`!).
      4) Never hardcodes `All = 100%` — computes `All` directly from actual pool membership!
    """
    if not eroded_patch_records:
        return {"n_patches": 0, "rows": []}

    rows = []
    all_ks: List[Optional[int]] = list(k_budgets) + [None]  # None == All obligations in selected pool

    for k in all_ks:
        unif_hits = 0.0
        line_disp_hits = 0.0
        prio_hits = 0.0
        frac_sampled_list: List[float] = []

        for rec in eroded_patch_records:
            pool: List[Dict[str, Any]] = []
            for m in rec["target_m_det"]:
                mc = dict(m)
                mc["_role_id"] = f"target::{m['id']}"
                mc["_loc_key"] = ("target", m.get("rel_file") or m.get("mutatedClass", ""), int(m.get("lineNumber", 0)))
                pool.append(mc)
            if include_partner_in_pool and rec.get("partner_m_det"):
                for pm in rec["partner_m_det"]:
                    pmc = dict(pm)
                    pmc["_role_id"] = f"partner::{pm['id']}"
                    pmc["_loc_key"] = ("partner", pm.get("rel_file") or pm.get("mutatedClass", ""), int(pm.get("lineNumber", 0)))
                    pool.append(pmc)

            N = len(pool)
            if N == 0:
                continue

            surv_role_ids: Set[str] = set(rec["survived_role_ids"])
            pool_role_ids = {m["_role_id"] for m in pool}
            S = len(surv_role_ids & pool_role_ids)  # Exact! Zero phantom `max(1, ...)`!

            eff_k = N if k is None else min(k, N)
            frac_sampled_list.append(eff_k / N)

            # (a) Exact hypergeometric uniform random draw without replacement
            p_det_unif = 1.0 - _hypergeom_miss_prob(N, S, eff_k)
            unif_hits += p_det_unif

            # (b) Deterministic Cross-(File, Line) Dispersion ordering (1 per distinct `(role, file, line)` round-robin)
            by_loc: Dict[Tuple[str, str, int], List[Dict[str, Any]]] = {}
            for m in pool:
                by_loc.setdefault(m["_loc_key"], []).append(m)
            disp_order: List[Dict[str, Any]] = []
            loc_keys = sorted(by_loc.keys())
            idx_round = 0
            while len(disp_order) < N:
                added = False
                for lk in loc_keys:
                    if idx_round < len(by_loc[lk]):
                        disp_order.append(by_loc[lk][idx_round])
                        added = True
                if not added:
                    break
                idx_round += 1
            picked_disp = {m["_role_id"] for m in disp_order[:eff_k]}
            line_disp_hits += 1.0 if bool(picked_disp & surv_role_ids) else 0.0

            # (c) Operator-Priority + Cross-(File, Line) Dispersion ordering
            prio_sorted = sorted(
                pool,
                key=lambda m: (_mutator_priority_rank(m.get("mutator", ""), m.get("description", "")), m["_loc_key"], m["_role_id"]),
            )
            picked_prio = {m["_role_id"] for m in prio_sorted[:eff_k]}
            prio_hits += 1.0 if bool(picked_prio & surv_role_ids) else 0.0

        denom = len(eroded_patch_records)
        rows.append({
            "budget_label": "All" if k is None else f"Top-{k}",
            "k": k,
            "mean_obligations_sampled_pct": round(100.0 * sum(frac_sampled_list) / max(1, len(frac_sampled_list)), 2),
            "uniform_random_recall_pct": round(100.0 * unif_hits / denom, 2),
            "file_line_dispersion_recall_pct": round(100.0 * line_disp_hits / denom, 2),
            "operator_priority_recall_pct": round(100.0 * prio_hits / denom, 2),
        })

    return {
        "pool_mode": "joint_target_plus_partner" if include_partner_in_pool else "target_only",
        "n_patches": len(eroded_patch_records),
        "rows": rows,
    }


# ============================================================================
# 5. Exact Node- and Lifecycle-Attributed Python Repeated-Pollution Verifier (R5-3 / R5-4)
# ============================================================================

def _match_py_nodeid_exact(nodeid: str, hint: str) -> bool:
    """
    Exact Python nodeid matcher (same discipline as `v2_pytest_attribution_runner._match_node`):
      - Normalizes leading `./` and path separators.
      - If `hint` specifies an explicit parameterized instance (`[...]`), requires exact full-nodeid match
        (never strips `[...]` to match a different parameter instance).
      - If `hint` does NOT contain `[`, matches exact `nodeid == hint` or parameterized children `hint[...]`
        (never prefix-matches `test_loading` against `test_loading_failure`).
    """
    nid = str(nodeid or "").replace("\\", "/").lstrip("./")
    h = str(hint or "").replace("\\", "/").lstrip("./")
    if not nid or not h:
        return False
    if nid == h:
        return True
    if "[" in h:
        return False
    return nid.startswith(h + "[")


def evaluate_py_repeated_pollution_report(
    rep_d: Dict[str, Any],
    target_node: str,
    partner_node: str,
    allow_upstream_nonidempotent_p2_call_fail: bool = False,
) -> Dict[str, Any]:
    """
    Strictly evaluates a single-session Python `partner -> partner -> target` lifecycle report (`R5-3` / `R5-4`).
    Never infers roles from array position alone and never swallows setup/teardown errors:
      - Groups lifecycle events (`setup`, `call`, `teardown`) by `(occ, nodeid)`.
      - Verifies every occurrence matches its expected exact full nodeid (`p1` == `partner_node`,
        `p2` == `partner_node`, `target` == `target_node`), with `p1`, `p2`, and `target` ALL present
        and executed in order (`max_occ(p1) < min_occ(p2)` and `max_occ(p2) < min_occ(target)`).
      - Requires `setup == "passed"` and `teardown == "passed"` on ALL occurrences (`p1`, `p2`, and `target`).
        Any setup/teardown crash (including on `p2`!) fails closed as `UNKNOWN_*`.
      - Reports BOTH:
          * `strict_passed`: `p1`, `p2`, and `target` ALL pass `setup`, `call`, and `teardown`.
          * `target_stress_passed`: `p1` passes `setup/call/teardown`, `p2` passes `setup/teardown`,
            `target` passes `setup/call/teardown`, AND either `p2` `call` passed OR
            `allow_upstream_nonidempotent_p2_call_fail` is explicitly True (backed by matched upstream control
            with unmodified partner).
    """
    events = rep_d.get("events")
    if not isinstance(events, list) or not events:
        return {
            "strict_passed": False,
            "target_stress_passed": False,
            "is_unknown": True,
            "status": "UNKNOWN_EMPTY_REPORT",
            "p1_ok": False,
            "p2_call_ok": False,
            "p2_lifecycle_ok": False,
            "target_ok": False,
        }

    # Build ordered per-occurrence records: occ -> {nodeid, role_hint, phases: {when: outcome}}
    by_occ: Dict[int, Dict[str, Any]] = {}
    for ev in events:
        if not isinstance(ev, dict) or "occ" not in ev or "nodeid" not in ev or "when" not in ev:
            return {
                "strict_passed": False, "target_stress_passed": False, "is_unknown": True,
                "status": "UNKNOWN_MALFORMED_EVENT",
                "p1_ok": False, "p2_call_ok": False, "p2_lifecycle_ok": False, "target_ok": False,
            }
        occ = int(ev["occ"])
        nid = str(ev["nodeid"])
        when = str(ev["when"])
        outcome = str(ev.get("outcome", ""))
        rec = by_occ.setdefault(occ, {"occ": occ, "nodeid": nid, "role": ev.get("role"), "phases": {}})
        if rec["nodeid"] != nid:
            return {
                "strict_passed": False, "target_stress_passed": False, "is_unknown": True,
                "status": "UNKNOWN_OCC_NODEID_MISMATCH",
                "p1_ok": False, "p2_call_ok": False, "p2_lifecycle_ok": False, "target_ok": False,
            }
        rec["phases"][when] = outcome

    ordered = [by_occ[k] for k in sorted(by_occ.keys())]
    same_hint = _match_py_nodeid_exact(target_node, partner_node) or _match_py_nodeid_exact(partner_node, target_node)

    # Partition occurrences strictly by BOTH expected exact nodeid AND order!
    p1_items: List[Dict[str, Any]] = []
    p2_items: List[Dict[str, Any]] = []
    tgt_items: List[Dict[str, Any]] = []
    unexpected_nodes: List[str] = []

    if all(r.get("role") in ("p1", "p2", "target") for r in ordered):
        for r in ordered:
            rl = r["role"]
            if rl == "p1":
                if not _match_py_nodeid_exact(r["nodeid"], partner_node):
                    unexpected_nodes.append(f"p1:{r['nodeid']}")
                else:
                    p1_items.append(r)
            elif rl == "p2":
                if not _match_py_nodeid_exact(r["nodeid"], partner_node):
                    unexpected_nodes.append(f"p2:{r['nodeid']}")
                else:
                    p2_items.append(r)
            else:
                if not _match_py_nodeid_exact(r["nodeid"], target_node):
                    unexpected_nodes.append(f"target:{r['nodeid']}")
                else:
                    tgt_items.append(r)
    else:
        # Infer partition without trusting position blindly: require exact nodeid matches!
        for r in ordered:
            is_p = _match_py_nodeid_exact(r["nodeid"], partner_node)
            is_t = _match_py_nodeid_exact(r["nodeid"], target_node)
            if not is_p and not is_t:
                unexpected_nodes.append(r["nodeid"])
            elif is_p and not is_t:
                if not p1_items:
                    p1_items.append(r)
                elif not tgt_items:
                    p2_items.append(r)
                else:
                    unexpected_nodes.append(f"late_partner:{r['nodeid']}")
            elif is_t and not is_p:
                tgt_items.append(r)
            else:
                # Same nodeid (degenerate partner == target)
                if not p1_items:
                    p1_items.append(r)
                elif not p2_items:
                    p2_items.append(r)
                else:
                    tgt_items.append(r)

    if unexpected_nodes:
        return {
            "strict_passed": False, "target_stress_passed": False, "is_unknown": True,
            "status": f"UNKNOWN_UNEXPECTED_NODE:{unexpected_nodes[0]}",
            "p1_ok": False, "p2_call_ok": False, "p2_lifecycle_ok": False, "target_ok": False,
        }
    if not p1_items or not p2_items or not tgt_items:
        return {
            "strict_passed": False, "target_stress_passed": False, "is_unknown": True,
            "status": f"UNKNOWN_MISSING_ROLE:p1={len(p1_items)},p2={len(p2_items)},target={len(tgt_items)}",
            "p1_ok": bool(p1_items), "p2_call_ok": False, "p2_lifecycle_ok": False, "target_ok": bool(tgt_items),
        }
    if not (max(r["occ"] for r in p1_items) < min(r["occ"] for r in p2_items) and max(r["occ"] for r in p2_items) < min(r["occ"] for r in tgt_items)):
        return {
            "strict_passed": False, "target_stress_passed": False, "is_unknown": True,
            "status": "UNKNOWN_INVALID_EXEC_ORDER",
            "p1_ok": False, "p2_call_ok": False, "p2_lifecycle_ok": False, "target_ok": False,
        }

    def _check_lifecycle(items_list: List[Dict[str, Any]]) -> Tuple[bool, bool, str]:
        """Returns `(lifecycle_setup_teardown_ok, all_calls_passed, detail_status)`."""
        for r in items_list:
            ph = r["phases"]
            if ph.get("setup") != "passed":
                return False, False, f"SETUP_{ph.get('setup', 'MISSING')}"
            if ph.get("teardown") != "passed":
                return False, False, f"TEARDOWN_{ph.get('teardown', 'MISSING')}"
            if "call" not in ph or ph["call"] not in ("passed", "failed"):
                return False, False, f"CALL_{ph.get('call', 'MISSING')}"
        calls_ok = all(r["phases"]["call"] == "passed" for r in items_list)
        return True, calls_ok, "OK" if calls_ok else "CALL_FAILED"

    p1_lc, p1_call, p1_st = _check_lifecycle(p1_items)
    p2_lc, p2_call, p2_st = _check_lifecycle(p2_items)
    t_lc, t_call, t_st = _check_lifecycle(tgt_items)

    # Any setup/teardown/missing-phase error on ANY role (p1, p2, or target) is UNKNOWN (fail-closed!)
    if not p1_lc:
        return {
            "strict_passed": False, "target_stress_passed": False, "is_unknown": True,
            "status": f"UNKNOWN_P1_{p1_st}",
            "p1_ok": False, "p2_call_ok": p2_call, "p2_lifecycle_ok": p2_lc, "target_ok": (t_lc and t_call),
        }
    if not p2_lc:
        return {
            "strict_passed": False, "target_stress_passed": False, "is_unknown": True,
            "status": f"UNKNOWN_P2_{p2_st}",
            "p1_ok": p1_call, "p2_call_ok": p2_call, "p2_lifecycle_ok": False, "target_ok": (t_lc and t_call),
        }
    if not t_lc:
        return {
            "strict_passed": False, "target_stress_passed": False, "is_unknown": True,
            "status": f"UNKNOWN_TARGET_{t_st}",
            "p1_ok": p1_call, "p2_call_ok": p2_call, "p2_lifecycle_ok": True, "target_ok": False,
        }

    p1_ok = p1_lc and p1_call
    target_ok = t_lc and t_call
    strict_ok = p1_ok and p2_call and target_ok
    stress_ok = p1_ok and target_ok and (p2_call or bool(allow_upstream_nonidempotent_p2_call_fail))

    if strict_ok:
        st_lbl = "STRICT_ALL_PASSED"
    elif not p1_ok:
        st_lbl = "P1_CALL_FAILED"
    elif not target_ok:
        st_lbl = "TARGET_CALL_FAILED"
    elif not p2_call:
        st_lbl = "P2_CALL_FAILED_TARGET_PASSED"
    else:
        st_lbl = "FAILED"

    return {
        "strict_passed": strict_ok,
        "target_stress_passed": stress_ok,
        "is_unknown": False,
        "status": st_lbl,
        "p1_ok": p1_ok,
        "p2_call_ok": p2_call,
        "p2_lifecycle_ok": p2_lc,
        "target_ok": target_ok,
        "n_p1": len(p1_items),
        "n_p2": len(p2_items),
        "n_target": len(tgt_items),
    }


def evaluate_java_repeated_pollution_report(
    rep: Dict[str, Any],
    allow_upstream_nonidempotent_p2_call_fail: bool = False,
    expected_p2_call_exc: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Fail-closed phase-level evaluator for Java single-JVM `partner -> partner -> target`
    repeated-pollution runs (`R6-1` & `R6-2`):
      - Requires `order_exact == True`, `mode != "UNKNOWN"`, `total_run == 3`, `total_ign == 0`.
      - When `phase_level_known` is False (e.g., `CUSTOM_RUNWITH_SEQUENTIAL_SAME_JVM` or
        `SUREFIRE_FALLBACK`), `p2_lifecycle_ok` is False and Stress exemption is STRICTLY
        FORBIDDEN (`target_stress_passed == strict_passed`); any non-strict outcome is `is_unknown=True`.
      - When `phase_level_known` is True (`SINGLE_LIFECYCLE_BLOCK_RUNNER`), verifies
        `setup == PASS`, `teardown == PASS`, and `not outer_fail` on ALL three roles
        (`p1`, `p2`, `target`). Any `@Before`, `@After`, or `@Rule`/outer failure on any
        role fails closed as `is_unknown=True`, `strict_passed=False`, `target_stress_passed=False`.
      - Stress exemption (`p2` `call` failed while `p1` and `target` passed all phases) is
        granted ONLY when `allow_upstream_nonidempotent_p2_call_fail` is True,
        `p2_lifecycle_ok` is True, `total_fail == 1`, and (when `expected_p2_call_exc` is
        provided) `p2_call_exception_type == expected_p2_call_exc`.
    """
    mode = str(rep.get("mode") or "UNKNOWN")
    order_exact = bool(rep.get("order_exact"))
    phase_known = bool(rep.get("phase_level_known"))
    total_run = int(rep.get("total_run", 0))
    total_fail = int(rep.get("total_fail", 0))
    total_ign = int(rep.get("total_ign", 0))
    rc_c = int(rep.get("rc_c", 0))
    phases = rep.get("phases") or {}

    base_out: Dict[str, Any] = {
        "mode": mode,
        "exec_order": str(rep.get("exec_order") or ""),
        "order_exact": order_exact,
        "phase_level_known": phase_known,
        "total_run": total_run,
        "total_fail": total_fail,
        "total_ign": total_ign,
        "phases": phases,
        "fail_lines": list(rep.get("fail_lines") or [])[:3],
    }

    if mode == "UNKNOWN" or not order_exact or total_run != 3 or total_ign != 0:
        return {
            **base_out,
            "strict_passed": False,
            "target_stress_passed": False,
            "is_unknown": True,
            "status": f"UNKNOWN_ORDER_OR_INFRA:mode={mode},run={total_run},ign={total_ign}",
            "p1_ok": False,
            "p2_ok": False,
            "p2_call_ok": False,
            "p2_lifecycle_ok": False,
            "target_ok": False,
            "p2_call_exception_type": None,
        }

    if not phase_known:
        p1_raw = bool(rep.get("p1_ok"))
        p2_raw = bool(rep.get("p2_ok"))
        tgt_raw = bool(rep.get("target_ok"))
        strict_ok = bool(p1_raw and p2_raw and tgt_raw and total_fail == 0 and rc_c == 0)
        return {
            **base_out,
            "strict_passed": strict_ok,
            "target_stress_passed": strict_ok,  # Phase-unknown NEVER grants Stress exemption!
            "is_unknown": (not strict_ok),
            "status": "STRICT_ALL_PASSED_UNPHASED" if strict_ok else "UNKNOWN_PHASE_UNAVAILABLE",
            "p1_ok": p1_raw,
            "p2_ok": p2_raw,
            "p2_call_ok": p2_raw,
            "p2_lifecycle_ok": False,
            "target_ok": tgt_raw,
            "p2_call_exception_type": None,
        }

    def _check_role(rname: str) -> Tuple[bool, bool, str, Optional[str]]:
        ph = phases.get(rname)
        if not isinstance(ph, dict):
            return False, False, "MISSING_ROLE_PHASES", None
        st_s = str(ph.get("setup") or "MISSING")
        st_c = str(ph.get("call") or "MISSING")
        st_t = str(ph.get("teardown") or "MISSING")
        outer = bool(ph.get("outer_fail"))
        cexc = ph.get("call_exc")
        if cexc in ("", "NONE", None):
            cexc = None
        if st_s != "PASS":
            return False, False, f"SETUP_{st_s}", cexc
        if st_t != "PASS":
            return False, False, f"TEARDOWN_{st_t}", cexc
        if outer:
            return False, False, "OUTER_OR_RULE_FAIL", cexc
        if st_c not in ("PASS", "FAIL"):
            return False, False, f"CALL_{st_c}", cexc
        return True, (st_c == "PASS"), ("OK" if st_c == "PASS" else "CALL_FAILED"), cexc

    p1_lc, p1_call, p1_st, _ = _check_role("p1")
    p2_lc, p2_call, p2_st, p2_exc = _check_role("p2")
    t_lc, t_call, t_st, _ = _check_role("target")

    if not p1_lc:
        return {
            **base_out,
            "strict_passed": False,
            "target_stress_passed": False,
            "is_unknown": True,
            "status": f"UNKNOWN_P1_{p1_st}",
            "p1_ok": False,
            "p2_ok": (p2_lc and p2_call),
            "p2_call_ok": p2_call,
            "p2_lifecycle_ok": p2_lc,
            "target_ok": (t_lc and t_call),
            "p2_call_exception_type": p2_exc,
        }
    if not p2_lc:
        return {
            **base_out,
            "strict_passed": False,
            "target_stress_passed": False,
            "is_unknown": True,
            "status": f"UNKNOWN_P2_{p2_st}",
            "p1_ok": p1_call,
            "p2_ok": False,
            "p2_call_ok": p2_call,
            "p2_lifecycle_ok": False,
            "target_ok": (t_lc and t_call),
            "p2_call_exception_type": p2_exc,
        }
    if not t_lc:
        return {
            **base_out,
            "strict_passed": False,
            "target_stress_passed": False,
            "is_unknown": True,
            "status": f"UNKNOWN_TARGET_{t_st}",
            "p1_ok": p1_call,
            "p2_ok": p2_call,
            "p2_call_ok": p2_call,
            "p2_lifecycle_ok": True,
            "target_ok": False,
            "p2_call_exception_type": p2_exc,
        }

    p1_ok = p1_call
    target_ok = t_call
    strict_ok = bool(p1_ok and p2_call and target_ok and total_fail == 0 and rc_c == 0)
    exc_match = (expected_p2_call_exc is None) or (bool(expected_p2_call_exc) and p2_exc == expected_p2_call_exc)
    stress_ok = bool(
        strict_ok
        or (
            p1_ok
            and (not p2_call)
            and p2_lc
            and target_ok
            and total_fail == 1
            and bool(allow_upstream_nonidempotent_p2_call_fail)
            and exc_match
        )
    )

    if strict_ok:
        st_lbl = "STRICT_ALL_PASSED"
    elif not p1_ok:
        st_lbl = "P1_CALL_FAILED"
    elif not target_ok:
        st_lbl = "TARGET_CALL_FAILED"
    elif not p2_call:
        st_lbl = "P2_CALL_FAILED_TARGET_PASSED" if exc_match else "P2_CALL_FAILED_EXC_MISMATCH"
    else:
        st_lbl = "FAILED"

    return {
        **base_out,
        "strict_passed": strict_ok,
        "target_stress_passed": stress_ok,
        "is_unknown": False,
        "status": st_lbl,
        "p1_ok": p1_ok,
        "p2_ok": p2_call,
        "p2_call_ok": p2_call,
        "p2_lifecycle_ok": p2_lc,
        "target_ok": target_ok,
        "p2_call_exception_type": p2_exc,
    }
