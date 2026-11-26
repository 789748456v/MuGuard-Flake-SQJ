import ast
import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from typing import Any, Dict, List, Optional, Set, Tuple

PY_PROJECTS_ROOT = "/root/sqj_work/FT1/py_projects"
PIP_CACHE_DIR = "/root/sqj_work/FT1/pip_cache"
IPFLAKIES_ROOT = "/root/sqj_work/FT1/repos/ipflakies_artifact"

# Strictly share <= 7.5 vCPU / <= 12 GB RAM ceiling (3 * 2.5 vCPU = 7.5 vCPU, 3 * 3.5 GB = 10.5 GB)
PY_DOCKER_SEM = threading.Semaphore(3)

DOCKERFILE_PY = """FROM python:3.9-slim
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \\
    git patch build-essential gcc libffi-dev libssl-dev libxml2-dev libxslt1-dev zlib1g-dev sqlite3 \\
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir --upgrade "pip<24.1" "setuptools<68" wheel \\
    "pytest>=6.2,<8.0" pytest-cov pytest-mock mock nose py six requests responses freezegun \\
    pyyaml attrs click jinja2 markupsafe packaging toml "numpy<2.0"
WORKDIR /workspace
"""


def ensure_py_docker_image() -> None:
    os.makedirs(PIP_CACHE_DIR, exist_ok=True)
    p = subprocess.run(["docker", "images", "-q", "ft1_python_env"], capture_output=True, text=True)
    if p.stdout.strip():
        return
    with tempfile.TemporaryDirectory() as tmpd:
        df = os.path.join(tmpd, "Dockerfile")
        open(df, "w", encoding="utf-8").write(DOCKERFILE_PY)
        subprocess.run(["docker", "build", "-t", "ft1_python_env", tmpd], check=True, timeout=600)


def run_py_docker(
    proj_dir: str,
    bash_inner: str,
    timeout: int = 90,
    cpus: str = "2.5",
    mem: str = "3500m",
) -> Tuple[int, str, float]:
    cname = f"ft1_py_{os.getpid()}_{threading.get_ident() % 10000}_{int(time.time() * 1000) % 100000}"
    cmd = [
        "docker", "run", "--rm", "--name", cname,
        f"--cpus={cpus}", f"--memory={mem}",
        "-v", f"{PIP_CACHE_DIR}:/root/.cache/pip",
        "-v", f"{proj_dir}:/workspace",
        "-w", "/workspace",
        "ft1_python_env",
        "bash", "-c",
        f"export PATH=/workspace/.ft1_venv/bin:$PATH && export PYTHONPATH=/workspace:/workspace/src:$PYTHONPATH && {bash_inner}",
    ]
    with PY_DOCKER_SEM:
        t0 = time.time()
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=timeout)
            out_s = p.stdout + "\n" + p.stderr
            try:
                import remote_core as _rc
                _rc.record_subproc_result(p.returncode, out_s, "run_py_docker")
            except Exception:
                pass
            return p.returncode, out_s, (time.time() - t0)
        except subprocess.TimeoutExpired:
            subprocess.run(["docker", "rm", "-f", cname], capture_output=True)
            return 124, f"TIMEOUT after {timeout}s", (time.time() - t0)


def setup_python_project(url: str, sha: str, proj_name: str) -> Optional[Tuple[str, float]]:
    """Clone Python repo at `sha` and build `.ft1_venv` inside container within 120s."""
    pdir = os.path.join(PY_PROJECTS_ROOT, f"{sha[:10]}_{proj_name}")
    venv_ready = os.path.join(pdir, ".ft1_venv/.ft1_ready")
    t0 = time.time()
    if os.path.exists(venv_ready):
        return pdir, 0.1
    try:
        if not os.path.exists(os.path.join(pdir, ".git")):
            os.makedirs(os.path.dirname(pdir), exist_ok=True)
            p = subprocess.run(["git", "clone", "--quiet", url, pdir], capture_output=True, text=True, timeout=120)
            if p.returncode != 0:
                return None
        subprocess.run(["git", "-C", pdir, "checkout", "-f", sha], capture_output=True, text=True, timeout=45, check=True)
        subprocess.run(["git", "-C", pdir, "clean", "-fd", "-e", ".ft1_venv"], capture_output=True, text=True, timeout=45)
    except Exception:
        return None

    setup_bash = (
        "python3 -m venv --system-site-packages .ft1_venv && "
        "export PATH=/workspace/.ft1_venv/bin:$PATH && "
        "for req in requirements.txt test-requirements.txt requirements-test.txt requirements_dev.txt requirements-dev.txt; do "
        "  if [ -f \"$req\" ]; then python3 -m pip install --prefer-binary -q -r \"$req\" || true; fi; "
        "done && "
        "if [ -f setup.py ] || [ -f pyproject.toml ]; then python3 -m pip install --prefer-binary --no-build-isolation -q -e . || python3 -m pip install --prefer-binary -q -e . || true; fi && "
        "python3 -m pytest --version && touch .ft1_venv/.ft1_ready"
    )
    rc, out, dt = run_py_docker(pdir, setup_bash, timeout=120)
    if rc == 0 and "pytest" in out and os.path.exists(venv_ready):
        return pdir, round(time.time() - t0, 2)
    return None


def parse_pytest_nodeid(nodeid: str) -> Tuple[str, Optional[str], str]:
    """
    Parse `path/to/test_foo.py::TestCls::test_bar[param]` into
    `(rel_file, class_name_or_None, func_name_without_param)`.
    """
    base_id = nodeid.split("[", 1)[0]
    parts = base_id.split("::")
    rel_file = parts[0]
    if len(parts) == 2:
        return rel_file, None, parts[1]
    elif len(parts) >= 3:
        return rel_file, parts[-2], parts[-1]
    raise ValueError(f"Unrecognized pytest nodeid: {nodeid}")


def find_py_func_span(py_src: str, class_name: Optional[str], func_name: str) -> Optional[Tuple[int, int, str]]:
    """
    Use Python `ast` line offsets to deterministically extract `(start_line_1idx, end_line_1idx, indent_prefix)`
    of `func_name` (including decorators) inside `class_name` (or module top-level).
    """
    try:
        tree = ast.parse(py_src)
    except SyntaxError:
        return None
    lines = py_src.splitlines(True)

    target_node = None
    if class_name:
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name == class_name:
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == func_name:
                        target_node = item
                        break
    if target_node is None:
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
                target_node = node
                break
    if target_node is None:
        return None

    start_ln = target_node.lineno
    if target_node.decorator_list:
        start_ln = min(start_ln, *(d.lineno for d in target_node.decorator_list))
    end_ln = getattr(target_node, "end_lineno", start_ln)
    def_line = lines[target_node.lineno - 1]
    indent = def_line[: len(def_line) - len(def_line.lstrip())]
    return start_ln, end_ln, indent


def extract_py_func(py_src: str, class_name: Optional[str], func_name: str) -> Optional[str]:
    span = find_py_func_span(py_src, class_name, func_name)
    if not span:
        return None
    s_ln, e_ln, _ = span
    lines = py_src.splitlines(True)
    return "".join(lines[s_ln - 1 : e_ln])


def reindent_py_func(new_code: str, target_indent: str) -> str:
    raw_lines = [ln for ln in new_code.splitlines() if ln.strip() != ""]
    if not raw_lines:
        return new_code
    # Find minimum indent among def/@ lines (first non-empty line)
    first_ln = raw_lines[0]
    cur_indent = first_ln[: len(first_ln) - len(first_ln.lstrip())]
    out_lines = []
    for ln in new_code.splitlines():
        if not ln.strip():
            out_lines.append("")
            continue
        if cur_indent and ln.startswith(cur_indent):
            stripped = ln[len(cur_indent) :]
        else:
            stripped = ln.lstrip() if ln == first_ln else ln[min(len(cur_indent), len(ln) - len(ln.lstrip())) :]
        out_lines.append(target_indent + stripped)
    return "\n".join(out_lines) + "\n"


def replace_py_func(py_src: str, class_name: Optional[str], func_name: str, new_func_code: str) -> str:
    span = find_py_func_span(py_src, class_name, func_name)
    if not span:
        raise ValueError(f"Could not locate Python function `{func_name}` (class={class_name})")
    s_ln, e_ln, indent = span
    lines = py_src.splitlines(True)
    adjusted = reindent_py_func(new_func_code.strip("\n"), indent)
    return "".join(lines[: s_ln - 1]) + adjusted + "".join(lines[e_ln:])


def inject_py_imports(py_src: str, imports: List[str]) -> str:
    if not imports:
        return py_src
    clean = []
    for imp in imports:
        for ln in str(imp).splitlines():
            ln = ln.strip()
            if (ln.startswith("import ") or ln.startswith("from ")) and ln not in py_src:
                clean.append(ln)
    if not clean:
        return py_src
    lines = py_src.splitlines(True)
    ins_idx = 0
    # Insert after __future__ imports or module docstring
    try:
        tree = ast.parse(py_src)
        for node in tree.body:
            if isinstance(node, ast.Expr) and isinstance(getattr(node, "value", None), ast.Constant) and isinstance(node.value.value, str):
                ins_idx = max(ins_idx, getattr(node, "end_lineno", 1))
            elif isinstance(node, ast.ImportFrom) and node.module == "__future__":
                ins_idx = max(ins_idx, getattr(node, "end_lineno", 1))
            else:
                break
    except SyntaxError:
        pass
    block = "\n".join(dict.fromkeys(clean)) + "\n"
    return "".join(lines[:ins_idx]) + block + "".join(lines[ins_idx:])


def extract_py_context_header(py_src: str, class_name: Optional[str], max_chars: int = 3200) -> str:
    lines = py_src.splitlines()
    top_lines = []
    for ln in lines[:85]:
        if ln.startswith("def test_") or ln.strip().startswith("def test_"):
            break
        top_lines.append(ln)
    cls_ctx = ""
    if class_name:
        try:
            tree = ast.parse(py_src)
            for node in tree.body:
                if isinstance(node, ast.ClassDef) and node.name == class_name:
                    c_lines = [lines[node.lineno - 1]]
                    for sub in node.body:
                        if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            if sub.name in ("setUp", "tearDown", "setUpClass", "tearDownClass", "setup_method", "teardown_method", "setup_class", "teardown_class") or any(
                                "fixture" in ast.unparse(d) for d in sub.decorator_list if hasattr(ast, "unparse")
                            ):
                                s_l, e_l = sub.lineno, getattr(sub, "end_lineno", sub.lineno)
                                c_lines.extend(lines[s_l - 1 : e_l])
                        elif isinstance(sub, (ast.Assign, ast.AnnAssign)):
                            s_l, e_l = sub.lineno, getattr(sub, "end_lineno", sub.lineno)
                            c_lines.extend(lines[s_l - 1 : e_l])
                    cls_ctx = "\n".join(c_lines)
                    break
        except Exception:
            pass
    combined = "\n".join(top_lines) + ("\n\n" + cls_ctx if cls_ctx else "")
    return combined[:max_chars]


def _pytest_strict_ok(rc_code: int, out: str, expected_passed: int) -> bool:
    if rc_code != 0:
        return False
    if re.search(r"\b[1-9]\d*\s+(?:failed|error|errors|skipped|xfailed|xpassed)\b", out):
        return False
    m = re.search(r"\b(\d+)\s+passed\b", out)
    return bool(m and int(m.group(1)) >= expected_passed)


def run_clean_py_e0(
    proj_dir: str,
    od_subtype: str,
    target_nodeid: str,
    partner_nodeid: str,
    runs: int = 5,
) -> Tuple[bool, str, float]:
    """
    Clean reference configuration e_0 for Python:
    - OD-victim: standalone `pytest <target>` passes `runs/runs` times.
    - OD-brittle: `[partner (state-setter), target (brittle)]` passes `runs/runs` times.
    """
    if od_subtype == "OD-brittle" and partner_nodeid:
        specs = f"'{partner_nodeid}' '{target_nodeid}'"
        exp_n = 2
    else:
        specs = f"'{target_nodeid}'"
        exp_n = 1
    bash_cmd = (
        f"for idx in $(seq 1 {runs}); do "
        f"  out=$(python3 -m pytest -q --tb=short -p no:randomly -p no:random_order {specs} 2>&1); rc=$?; "
        f"  if [ $rc -ne 0 ] || echo \"$out\" | grep -Eq '[1-9][0-9]* (failed|error|errors|skipped|xfailed)'; then "
        f"    echo \"PY_E0_FAIL=$idx\"; echo \"$out\"; exit 1; "
        f"  fi; "
        f"done && echo 'PY_E0_ALL_OK'"
    )
    t0 = time.time()
    rc_code, out, _ = run_py_docker(proj_dir, bash_cmd, timeout=max(45, runs * 15))
    if rc_code == 0 and "PY_E0_ALL_OK" in out:
        return True, f"Passed {runs}/{runs} clean e_0 runs", (time.time() - t0)
    return False, out[-1200:], (time.time() - t0)


def check_py_stability_w_plus(
    proj_dir: str,
    target_nodeid: str,
    partner_nodeid: str,
) -> Tuple[bool, str, float]:
    """
    Verify W+ stability across all 3 fresh-process schedules:
    1) standalone `target`
    2) `partner -> target`
    3) `target -> partner` (if partner != target)
    """
    t0 = time.time()
    rev_part = ""
    if partner_nodeid and partner_nodeid != target_nodeid:
        rev_part = (
            f"out3=$(python3 -m pytest -q --tb=short -p no:randomly -p no:random_order '{target_nodeid}' '{partner_nodeid}' 2>&1); rc3=$?; "
            "if [ $rc3 -ne 0 ] || echo \"$out3\" | grep -Eq '[1-9][0-9]* (failed|error|errors|skipped|xfailed)'; then "
            "  echo 'Reverse schedule (target -> partner) failed:'; echo \"$out3\"; exit 1; fi; "
        )
    bash_cmd = (
        f"out1=$(python3 -m pytest -q --tb=short -p no:randomly -p no:random_order '{target_nodeid}' 2>&1); rc1=$?; "
        "if [ $rc1 -ne 0 ] || echo \"$out1\" | grep -Eq '[1-9][0-9]* (failed|error|errors|skipped|xfailed)'; then "
        "  echo 'Standalone schedule (target) failed:'; echo \"$out1\"; exit 1; fi && "
        f"out2=$(python3 -m pytest -q --tb=short -p no:randomly -p no:random_order '{partner_nodeid}' '{target_nodeid}' 2>&1); rc2=$?; "
        "if [ $rc2 -ne 0 ] || echo \"$out2\" | grep -Eq '[1-9][0-9]* (failed|error|errors|skipped|xfailed)'; then "
        "  echo 'Order schedule (partner -> target) failed:'; echo \"$out2\"; exit 1; fi && "
        f"{rev_part}"
        "echo 'PY_W_PLUS_ALL_OK'"
    )
    rc_code, out, _ = run_py_docker(proj_dir, bash_cmd, timeout=75)
    if rc_code == 0 and "PY_W_PLUS_ALL_OK" in out:
        return True, "Passed standalone, partner->target, and target->partner", (time.time() - t0)
    return False, f"[rc={rc_code}]\n{out[-1400:]}", (time.time() - t0)


def trace_covered_focal_lines(
    proj_dir: str,
    od_subtype: str,
    target_nodeid: str,
    partner_nodeid: str,
) -> Dict[str, List[int]]:
    """
    Run `pytest --cov=.` in clean e_0 and extract exact covered line numbers in same-repo production `.py` files
    (excluding `.ft1_venv`, `tests/`, `test_*.py`, `*_test.py`, `setup.py`, `conftest.py`).
    """
    specs = f"'{partner_nodeid}' '{target_nodeid}'" if (od_subtype == "OD-brittle" and partner_nodeid) else f"'{target_nodeid}'"
    cov_json = os.path.join(proj_dir, ".ft1_cov.json")
    cov_db = os.path.join(proj_dir, ".coverage")
    try:
        bash_cmd = (
            f"rm -f .coverage .ft1_cov.json && "
            f"python3 -m coverage run -m pytest -q -p no:randomly -p no:random_order {specs} >/dev/null 2>&1 && "
            f"python3 -m coverage json -o .ft1_cov.json >/dev/null 2>&1 && echo 'COV_OK'"
        )
        rc_code, out, _ = run_py_docker(proj_dir, bash_cmd, timeout=60)
        if rc_code != 0 or not os.path.exists(cov_json):
            return {}
        data = json.load(open(cov_json, encoding="utf-8", errors="replace"))
        covered: Dict[str, List[int]] = {}
        for fpath, fdata in data.get("files", {}).items():
            rel = fpath.replace("\\", "/").lstrip("./")
            if rel.startswith("/workspace/"):
                rel = rel[len("/workspace/") :]
            base = os.path.basename(rel)
            parts = rel.split("/")
            if (
                ".ft1_venv" in parts
                or "tests" in parts
                or "test" in parts
                or base.startswith("test_")
                or base.endswith("_test.py")
                or base in ("setup.py", "conftest.py", "version.py", "_version.py")
            ):
                continue
            abs_p = os.path.join(proj_dir, rel)
            if os.path.exists(abs_p):
                exec_lines = sorted(set(fdata.get("executed_lines", [])))
                if exec_lines:
                    covered[rel] = exec_lines
        return covered
    finally:
        for fp in (cov_json, cov_db):
            if os.path.exists(fp):
                try:
                    os.remove(fp)
                except OSError:
                    pass


def _get_py_source_slice(py_src: str, line_no: int, radius: int = 3) -> str:
    lines = py_src.splitlines()
    lo = max(1, line_no - radius)
    hi = min(len(lines), line_no + radius)
    out = []
    for i in range(lo, hi + 1):
        mk = ">>" if i == line_no else "  "
        out.append(f"{mk} L{i}: {lines[i - 1]}")
    return "\n".join(out)


def generate_focal_py_mutants(
    proj_dir: str,
    covered_files: Dict[str, List[int]],
    max_mutants: int = 55,
) -> List[Dict[str, Any]]:
    """
    Deterministically generate standard AST mutants strictly on dynamically-executed focal production lines,
    mirroring PIT's core operators (ConditionalsBoundary, NegateConditionals, Math, BooleanReturn, EmptyReturn, VoidMethodCall).
    """
    mutants: List[Dict[str, Any]] = []
    cmp_swaps = {
        ast.Eq: (ast.NotEq, "negated `==` to `!=`"),
        ast.NotEq: (ast.Eq, "negated `!=` to `==`"),
        ast.Lt: (ast.GtE, "negated `<` to `>=`"),
        ast.LtE: (ast.Gt, "negated `<=` to `>`"),
        ast.Gt: (ast.LtE, "negated `>` to `<=`"),
        ast.GtE: (ast.Lt, "negated `>=` to `<`"),
        ast.In: (ast.NotIn, "negated `in` to `not in`"),
        ast.NotIn: (ast.In, "negated `not in` to `in`"),
        ast.Is: (ast.IsNot, "negated `is` to `is not`"),
        ast.IsNot: (ast.Is, "negated `is not` to `is`"),
    }
    bin_swaps = {
        ast.Add: (ast.Sub, "replaced `+` with `-`"),
        ast.Sub: (ast.Add, "replaced `-` with `+`"),
        ast.Mult: (ast.FloorDiv, "replaced `*` with `//`"),
    }

    # Rank files with most covered lines first
    ranked_files = sorted(covered_files.items(), key=lambda kv: len(kv[1]), reverse=True)
    for rel_path, exec_lines in ranked_files:
        if len(mutants) >= max_mutants:
            break
        abs_p = os.path.join(proj_dir, rel_path)
        src = open(abs_p, encoding="utf-8", errors="replace").read()
        try:
            orig_tree = ast.parse(src)
        except SyntaxError:
            continue
        exec_set = set(exec_lines)
        mod_fqcn = rel_path[:-3].replace("/", ".")

        # Map AST node index in pre-order traversal -> enclosing function name
        enclosing_fn: Dict[int, str] = {}

        def walk_with_fn(node: ast.AST, cur_fn: str) -> List[Tuple[int, ast.AST, str]]:
            items: List[Tuple[int, ast.AST, str]] = []
            stack = [(node, cur_fn)]
            idx_counter = 0
            # Deterministic recursive pre-order
            def rec(n: ast.AST, fn_name: str) -> None:
                nonlocal idx_counter
                my_idx = idx_counter
                idx_counter += 1
                next_fn = n.name if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) else fn_name
                items.append((my_idx, n, next_fn))
                for ch in ast.iter_child_nodes(n):
                    rec(ch, next_fn)
            rec(node, cur_fn)
            return items

        indexed_nodes = walk_with_fn(orig_tree, "<module>")

        def make_mutated_source(target_idx: int, kind: str) -> Optional[str]:
            t_copy = copy.deepcopy(orig_tree)
            copy_nodes = walk_with_fn(t_copy, "<module>")
            _, t_node, _ = copy_nodes[target_idx]
            if kind == "cmp" and isinstance(t_node, ast.Compare) and t_node.ops:
                new_cls, _ = cmp_swaps[type(t_node.ops[0])]
                t_node.ops[0] = new_cls()
            elif kind == "boolop" and isinstance(t_node, ast.BoolOp):
                t_node.op = ast.Or() if isinstance(t_node.op, ast.And) else ast.And()
            elif kind == "binop" and isinstance(t_node, ast.BinOp):
                new_cls, _ = bin_swaps[type(t_node.op)]
                t_node.op = new_cls()
            elif kind == "boolconst" and isinstance(t_node, ast.Constant) and isinstance(t_node.value, bool):
                t_node.value = not t_node.value
            elif kind == "ret_none" and isinstance(t_node, ast.Return) and t_node.value is not None:
                if isinstance(t_node.value, ast.Constant) and t_node.value.value is None:
                    return None
                t_node.value = ast.Constant(value=None)
            elif kind == "if_neg" and isinstance(t_node, ast.If):
                t_node.test = ast.UnaryOp(op=ast.Not(), operand=t_node.test)
            elif kind == "assign_none" and isinstance(t_node, ast.Assign) and t_node.lineno in exec_set:
                if isinstance(t_node.value, ast.Constant) and t_node.value.value is None:
                    return None
                t_node.value = ast.Constant(value=None)
            else:
                return None
            ast.fix_missing_locations(t_copy)
            try:
                new_src = ast.unparse(t_copy)
                compile(new_src, rel_path, "exec")
                return new_src
            except Exception:
                return None

        for n_idx, node, fn_name in indexed_nodes:
            if len(mutants) >= max_mutants:
                break
            ln = getattr(node, "lineno", 0)
            if ln not in exec_set or fn_name == "<module>":
                continue

            cand_specs: List[Tuple[str, str, str]] = []
            if isinstance(node, ast.Compare) and node.ops and type(node.ops[0]) in cmp_swaps:
                cand_specs.append(("cmp", "NegateConditionalsMutator", cmp_swaps[type(node.ops[0])][1]))
            elif isinstance(node, ast.BoolOp) and isinstance(node.op, (ast.And, ast.Or)):
                desc = "replaced `and` with `or`" if isinstance(node.op, ast.And) else "replaced `or` with `and`"
                cand_specs.append(("boolop", "BooleanOperatorMutator", desc))
            elif isinstance(node, ast.If):
                cand_specs.append(("if_neg", "NegateIfConditionMutator", "negated `if` branch condition"))
            elif isinstance(node, ast.Return) and node.value is not None:
                cand_specs.append(("ret_none", "NullReturnValsMutator", "replaced return value with `None`"))
            elif isinstance(node, ast.BinOp) and type(node.op) in bin_swaps:
                cand_specs.append(("binop", "MathMutator", bin_swaps[type(node.op)][1]))
            elif isinstance(node, ast.Constant) and isinstance(node.value, bool):
                cand_specs.append(("boolconst", "BooleanConstantMutator", f"replaced `{node.value}` with `{not node.value}`"))

            for kind, mut_name, desc in cand_specs:
                if len(mutants) >= max_mutants:
                    break
                m_src = make_mutated_source(n_idx, kind)
                if not m_src or m_src == src:
                    continue
                mid = f"{mod_fqcn}::{fn_name}:L{ln}:{mut_name}:{n_idx}:{desc}"
                mutants.append({
                    "id": mid,
                    "rel_file": rel_path,
                    "mutatedClass": mod_fqcn,
                    "mutatedMethod": fn_name,
                    "lineNumber": ln,
                    "mutator": mut_name,
                    "index": str(n_idx),
                    "description": desc,
                    "source_slice": _get_py_source_slice(src, ln),
                    "mutated_file_src": m_src,
                })
    return mutants


PY_BATCH_MUT_RUNNER = r"""
import json, os, shutil, subprocess, sys, time

spec_file = sys.argv[1]
cfg = json.load(open(spec_file, encoding="utf-8"))
pytest_args = cfg["pytest_args"]
mutants = cfg["mutants"]

orig_backups = {}
for m in mutants:
    rf = m["rel_file"]
    if rf not in orig_backups:
        orig_backups[rf] = open(rf, "rb").read()

killed_ids = []
try:
    for m in mutants:
        rf = m["rel_file"]
        # Remove stale .pyc so Python reloads mutated source immediately
        pycache_dir = os.path.join(os.path.dirname(rf) or ".", "__pycache__")
        if os.path.isdir(pycache_dir):
            shutil.rmtree(pycache_dir, ignore_errors=True)
        open(rf, "w", encoding="utf-8").write(m["mutated_file_src"])
        try:
            p = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "--tb=no", "-p", "no:randomly", "-p", "no:random_order"] + pytest_args,
                capture_output=True, text=True, timeout=8,
            )
            # Strict assertion/exception failure kill in pytest returns exit code 1 (not 0 passed, not 2/3/4 internal/usage/syntax, not timeout)
            if p.returncode == 1:
                killed_ids.append(m["id"])
        except subprocess.TimeoutExpired:
            pass
        finally:
            open(rf, "wb").write(orig_backups[rf])
            if os.path.isdir(pycache_dir):
                shutil.rmtree(pycache_dir, ignore_errors=True)
finally:
    for rf, raw in orig_backups.items():
        open(rf, "wb").write(raw)

print("===FT1_PY_MUT_START===")
print(json.dumps({"killed_ids": killed_ids}))
print("===FT1_PY_MUT_END===")
""".strip()


def run_single_focal_py_mut(
    proj_dir: str,
    pytest_args: List[str],
    mutants: List[Dict[str, Any]],
    timeout: int = 120,
) -> Tuple[bool, Set[str], float]:
    """Evaluate `mutants` in a single container batch, returning `(ok, killed_id_set, wall_sec)`."""
    if not mutants:
        return True, set(), 0.0
    runner_p = os.path.join(proj_dir, ".ft1_mut_runner.py")
    spec_p = os.path.join(proj_dir, ".ft1_mut_spec.json")
    orig_files = {m["rel_file"]: open(os.path.join(proj_dir, m["rel_file"]), "rb").read() for m in mutants}
    try:
        open(runner_p, "w", encoding="utf-8").write(PY_BATCH_MUT_RUNNER)
        open(spec_p, "w", encoding="utf-8").write(
            json.dumps({"pytest_args": pytest_args, "mutants": mutants}, ensure_ascii=False)
        )
        rc_code, out, dt = run_py_docker(
            proj_dir,
            "python3 .ft1_mut_runner.py .ft1_mut_spec.json",
            timeout=timeout,
        )
        s_tag, e_tag = "===FT1_PY_MUT_START===", "===FT1_PY_MUT_END==="
        if rc_code == 0 and s_tag in out and e_tag in out:
            payload = json.loads(out.split(s_tag, 1)[1].split(e_tag, 1)[0].strip())
            return True, set(payload.get("killed_ids", [])), round(dt, 2)
        return False, set(), round(dt, 2)
    finally:
        for rf, raw in orig_files.items():
            open(os.path.join(proj_dir, rf), "wb").write(raw)
        for fp in (runner_p, spec_p):
            if os.path.exists(fp):
                try:
                    os.remove(fp)
                except OSError:
                    pass


def apply_ipflakies_patch_file(orig_file_src: str, patch_path: str) -> Optional[str]:
    if not patch_path or not os.path.exists(patch_path):
        return None
    patch_txt = open(patch_path, encoding="utf-8", errors="replace").read()
    idx_hunk = patch_txt.find("\n@@ ")
    if idx_hunk == -1:
        return None
    with tempfile.TemporaryDirectory() as tmpd:
        tmp_py = os.path.join(tmpd, "target.py")
        tmp_diff = os.path.join(tmpd, "ipf.diff")
        open(tmp_py, "w", encoding="utf-8").write(orig_file_src)
        body = "--- target.py\n+++ target.py" + patch_txt[idx_hunk:]
        if not body.endswith("\n"):
            body += "\n"
        open(tmp_diff, "w", encoding="utf-8").write(body)
        p = subprocess.run(["patch", "--batch", "--fuzz=2", tmp_py, tmp_diff], capture_output=True, text=True, timeout=10)
        if p.returncode != 0:
            return None
        return open(tmp_py, encoding="utf-8", errors="replace").read()


def evaluate_py_candidate_on_sample(
    sample: Dict[str, Any],
    candidate: Dict[str, Any],
    run_mut_if_stable: bool = True,
) -> Dict[str, Any]:
    """
    Deterministic End-to-End Python Evaluator (`B1_iPFlakies`, `B0`, `B2`, `B3`, `Ours_MuGuard_Flake`):
      1) Apply candidate function(s) & imports (or full patched file for `B1_iPFlakies`), restoring 100% cleanly in `finally:`
      2) Syntax / import compile check (`py_compile`)
      3) W+ stability across standalone `target`, `partner -> target`, and `target -> partner`
      4) Focal AST mutation conservation probe on `M_det(T_orig)` (evaluated on standalone `target`, plus `partner_m_det` if `partner` was edited)!
    """
    t0_total = time.time()
    proj_dir = sample["proj_dir"]
    vic_file = os.path.join(proj_dir, sample["victim_file_rel"])
    pol_file = os.path.join(proj_dir, sample["polluter_file_rel"]) if sample.get("polluter_file_rel") else vic_file

    orig_vic_bytes = open(vic_file, "rb").read()
    orig_pol_bytes = open(pol_file, "rb").read() if pol_file != vic_file else orig_vic_bytes

    res: Dict[str, Any] = {
        "compiled": False,
        "compile_error": "",
        "compile_sec": 0.0,
        "w_plus_passed": False,
        "w_plus_diag": "",
        "w_plus_sec": 0.0,
        "pit_ran": False,
        "pit_sec": 0.0,
        "m_det_total": len(sample["m_det"]),
        "m_det_killed": 0,
        "retention_m_det": None,
        "survived_mutants": [],
        "m_gold_total": 0,
        "m_gold_killed": 0,
        "retention_m_gold": None,
        "eroded": False,
        "sound_fix": False,
        "total_eval_sec": 0.0,
    }

    try:
        t0_c = time.time()
        orig_vic_src = orig_vic_bytes.decode("utf-8", errors="replace")
        orig_pol_src = orig_pol_bytes.decode("utf-8", errors="replace")

        if candidate.get("patched_file_src"):
            new_v = candidate["patched_file_src"]
            compile(new_v, sample["victim_file_rel"], "exec")
            open(vic_file, "w", encoding="utf-8").write(new_v)
        else:
            vcode = candidate.get("victim_code") or candidate.get("test_code")
            pcode = candidate.get("polluter_code")
            if not vcode and not pcode:
                res["compile_error"] = "Missing candidate Python test function code."
                return res
            if vic_file == pol_file:
                new_s = orig_vic_src
                if vcode:
                    new_s = replace_py_func(new_s, sample.get("victim_class"), sample["victim_method"], vcode)
                if pcode and sample.get("polluter_method") and sample["polluter_method"] != sample["victim_method"]:
                    if pcode.strip() != sample.get("orig_polluter_code", "").strip():
                        new_s = replace_py_func(new_s, sample.get("polluter_class"), sample["polluter_method"], pcode)
                new_s = inject_py_imports(new_s, candidate.get("imports") or [])
                compile(new_s, sample["victim_file_rel"], "exec")
                open(vic_file, "w", encoding="utf-8").write(new_s)
            else:
                if vcode:
                    nv = replace_py_func(orig_vic_src, sample.get("victim_class"), sample["victim_method"], vcode)
                    nv = inject_py_imports(nv, candidate.get("imports") or [])
                    compile(nv, sample["victim_file_rel"], "exec")
                    open(vic_file, "w", encoding="utf-8").write(nv)
                if pcode and pcode.strip() != sample.get("orig_polluter_code", "").strip():
                    np = replace_py_func(orig_pol_src, sample.get("polluter_class"), sample["polluter_method"], pcode)
                    np = inject_py_imports(np, candidate.get("imports") or [])
                    compile(np, sample["polluter_file_rel"], "exec")
                    open(pol_file, "w", encoding="utf-8").write(np)

        res["compiled"] = True
        res["compile_sec"] = round(time.time() - t0_c, 2)
        for tf in {vic_file, pol_file}:
            pdir_cache = os.path.join(os.path.dirname(tf), "__pycache__")
            if os.path.isdir(pdir_cache):
                shutil.rmtree(pdir_cache, ignore_errors=True)

        # 2. Check W+ Stability
        w_ok, w_diag, dt_w = check_py_stability_w_plus(
            proj_dir=proj_dir,
            target_nodeid=sample["test_full_name"],
            partner_nodeid=sample["polluter_full_name"],
        )
        res["w_plus_passed"] = w_ok
        res["w_plus_diag"] = w_diag
        res["w_plus_sec"] = round(dt_w, 2)
        if not w_ok or not run_mut_if_stable:
            return res

        # 3. Single-Test Focal AST Mutation Probe on M_det(T_orig)
        # Once stabilized (W+ passed), standalone `target` must kill 100% of M_det(T_orig)!
        m_ok, killed_now, dt_mut = run_single_focal_py_mut(
            proj_dir=proj_dir,
            pytest_args=[sample["test_full_name"]],
            mutants=sample["m_det_exec"],
            timeout=120,
        )
        res["pit_ran"] = m_ok
        res["pit_sec"] = dt_mut
        if not m_ok:
            res["w_plus_diag"] += "\n[PY_MUT ERROR]"
            return res

        survived_list: List[Dict[str, Any]] = []
        killed_count = 0
        for m_orig in sample["m_det"]:
            if m_orig["id"] in killed_now:
                killed_count += 1
            else:
                survived_list.append(m_orig)

        # Also check partner_m_det if partner was modified
        pcode_cand = candidate.get("polluter_code")
        if pcode_cand and sample.get("polluter_m_det_exec") and pcode_cand.strip() != sample.get("orig_polluter_code", "").strip():
            pm_ok, pk_now, dt_pmut = run_single_focal_py_mut(
                proj_dir=proj_dir,
                pytest_args=[sample["polluter_full_name"]],
                mutants=sample["polluter_m_det_exec"],
                timeout=90,
            )
            res["pit_sec"] = round(res["pit_sec"] + dt_pmut, 2)
            if pm_ok:
                for pm in sample["polluter_m_det"]:
                    if pm["id"] not in pk_now:
                        pm_c = dict(pm)
                        pm_c["description"] = "[Partner Test Oracle Eroded] " + pm_c["description"]
                        survived_list.append(pm_c)

        total_oblig = len(sample["m_det"])
        res["m_det_killed"] = killed_count
        res["survived_mutants"] = survived_list
        eff_total = total_oblig + max(0, len(survived_list) - (total_oblig - killed_count))
        res["retention_m_det"] = round((eff_total - len(survived_list)) / eff_total, 4) if eff_total > 0 else 1.0
        res["eroded"] = len(survived_list) > 0
        res["sound_fix"] = bool(res["w_plus_passed"] and len(survived_list) == 0)
        return res
    except SyntaxError as se:
        res["compile_error"] = f"SyntaxError: {se}"
        return res
    except Exception as ex:
        res["compile_error"] = str(ex)
        return res
    finally:
        open(vic_file, "wb").write(orig_vic_bytes)
        if pol_file != vic_file:
            open(pol_file, "wb").write(orig_pol_bytes)
        for tf in {vic_file, pol_file}:
            pdir_cache = os.path.join(os.path.dirname(tf), "__pycache__")
            if os.path.isdir(pdir_cache):
                shutil.rmtree(pdir_cache, ignore_errors=True)
        res["total_eval_sec"] = round(time.time() - t0_total, 2)
