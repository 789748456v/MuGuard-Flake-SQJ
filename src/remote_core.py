import ast
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Set, Tuple

PROJECTS_ROOT = "/root/sqj_work/FT1/projects"
M2_REPO = "/root/sqj_work/FT1/m2_repo"
FD_ROOT = "/root/sqj_work/FT1/repos/FlakyDoctor"
IDOFT_ROOT = "/root/sqj_work/FT1/repos/idoft"

# Global cap: at most 3 concurrent Docker containers * 2.5 vCPU = 7.5 vCPU (<= 8 vCPU), 3 * 4 GB = 12 GB (<= 14 GB)
DOCKER_SEM = threading.Semaphore(3)

MVN_SKIPS = (
    "-Dfindbugs.skip=true -Dbasepom.check.skip-prettier -Dgpg.skip -Drat.skip "
    "-Dcheckstyle.skip -Denforcer.skip=true -Dspotbugs.skip -Djacoco.skip "
    "-Danimal.sniffer.skip -Dmaven.antrun.skip -Dfmt.skip -Dlicense.skip=true "
    "-Dlicense.skipCheckLicense -Dlicense.skipAddThirdParty=true -Ddependency-check.skip=true "
    "-Dspotless.check.skip=true -Dmaven.javadoc.skip=true "
    "-Dskip.npm -Dskip.yarn -Dskip.bower -Dskip.grunt -Dskip.gulp -DskipDockerBuild -DskipDocker"
)

JUNIT4_ORDER_RUNNER_SRC = r"""
import java.util.*;
import org.junit.runner.*;
import org.junit.runner.manipulation.*;
import org.junit.runner.notification.Failure;

public class FT1OrderRunner {
    public static void main(String[] args) throws Exception {
        final List<String> order = Arrays.asList(args);
        LinkedHashSet<Class<?>> clsSet = new LinkedHashSet<Class<?>>();
        for (String spec : order) {
            clsSet.add(Class.forName(spec.split("#")[0]));
        }
        Request req = Request.classes(clsSet.toArray(new Class<?>[0]));
        req = req.filterWith(new Filter() {
            @Override
            public boolean shouldRun(Description d) {
                if (d.isSuite()) return true;
                String key = d.getClassName() + "#" + d.getMethodName();
                return order.contains(key);
            }
            @Override
            public String describe() { return "FT1Subset"; }
        });
        req = req.sortWith(new Comparator<Description>() {
            private int idx(Description d) {
                String key = d.getClassName() + "#" + d.getMethodName();
                int i = order.indexOf(key);
                if (i >= 0) return i;
                for (int k = 0; k < order.size(); k++) {
                    if (order.get(k).startsWith(d.getClassName() + "#")) return k;
                }
                return 999;
            }
            @Override
            public int compare(Description a, Description b) {
                return Integer.compare(idx(a), idx(b));
            }
        });
        Result res = new JUnitCore().run(req);
        System.out.println("FT1_SUMMARY RUN=" + res.getRunCount() + " FAIL=" + res.getFailureCount() + " IGNORE=" + res.getIgnoreCount());
        for (Failure f : res.getFailures()) {
            System.out.println("FT1_FAILED: " + f.getDescription().getClassName() + "#" + f.getDescription().getMethodName());
            System.out.println("FT1_EXC: " + f.getException());
            String tr = f.getTrace();
            if (tr != null) {
                String[] lines = tr.split("\n");
                for (int i = 0; i < Math.min(lines.length, 15); i++) {
                    System.out.println("  " + lines[i]);
                }
            }
        }
        System.exit((res.getFailureCount() == 0 && res.getIgnoreCount() == 0 && res.getRunCount() == order.size()) ? 0 : 1);
    }
}
""".strip()


def run_docker_cmd(
    proj_dir: str,
    jdk: str,
    bash_inner: str,
    workdir_rel: str = "",
    timeout: int = 150,
    cpus: str = "2.5",
    mem: str = "4g",
) -> Tuple[int, str, float]:
    jhome = f"/usr/lib/jvm/java-{jdk}-openjdk-amd64"
    wdir = "/workspace" if not workdir_rel or workdir_rel == "." else f"/workspace/{workdir_rel}"
    cname = f"ft1_eval_{os.getpid()}_{threading.get_ident() % 10000}_{int(time.time() * 1000) % 100000}"
    cmd = [
        "docker", "run", "--rm", "--name", cname,
        f"--cpus={cpus}", f"--memory={mem}",
        "-v", f"{M2_REPO}:/root/.m2/repository",
        "-v", f"{proj_dir}:/workspace",
        "-w", wdir,
        "ft1_maven_env",
        "bash", "-c",
        f"export JAVA_HOME={jhome} && export PATH=$JAVA_HOME/bin:$PATH && {bash_inner}",
    ]
    with DOCKER_SEM:
        t0 = time.time()
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=timeout)
            return p.returncode, (p.stdout + "\n" + p.stderr), (time.time() - t0)
        except subprocess.TimeoutExpired:
            subprocess.run(["docker", "rm", "-f", cname], capture_output=True)
            return 124, f"TIMEOUT after {timeout}s", (time.time() - t0)


def docker_mvn(proj_dir: str, jdk: str, mvn_args: str, timeout: int = 150) -> Tuple[int, str, float]:
    return run_docker_cmd(proj_dir, jdk, f"mvn {mvn_args} {MVN_SKIPS}", timeout=timeout)


def mask_java_comments_and_literals(code: str) -> str:
    """Return a string of identical length to `code` with comments & string/char literals blanked out."""
    pattern = re.compile(
        r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'',
        re.DOTALL,
    )

    def repl(m: re.Match) -> str:
        s = m.group(0)
        return "".join("\n" if ch == "\n" else " " for ch in s)

    return pattern.sub(repl, code)


def find_java_method_span(java_src: str, method_name: str) -> Optional[Tuple[int, int]]:
    """
    Deterministically locate (start_char_idx, end_char_idx) of `method_name` (including preceding annotations)
    in `java_src`, immune to braces inside comments or string literals and Java 8/11/17 syntax.
    """
    masked = mask_java_comments_and_literals(java_src)
    # Match method signature `<ret_type> <method_name> (` not preceded by `.` or `new `
    sig_re = re.compile(
        r"(?<![.\w$])(?:(?:public|protected|private|static|final|synchronized|abstract|native|strictfp)\s+)*"
        r"(?:<[^>{};]+>\s+)?"
        r"(?:void|[A-Za-z_$][A-Za-z0-9_$.<>\[\],\s?]*?)\s+"
        + re.escape(method_name)
        + r"\s*\(",
        re.MULTILINE,
    )
    for m in sig_re.finditer(masked):
        sig_start = m.start()
        # Check prefix on same line isn't a call like `return foo(` or `new foo(`
        line_start = masked.rfind("\n", 0, sig_start) + 1
        prefix_same_line = masked[line_start:sig_start].strip()
        if prefix_same_line and not prefix_same_line.startswith("@"):
            continue
        # Check brace depth at sig_start >= 1 (inside class)
        depth_here = masked[:sig_start].count("{") - masked[:sig_start].count("}")
        if depth_here < 1:
            continue
        # Find parameter list closing `)`
        p_open = masked.find("(", m.end() - 1)
        if p_open == -1:
            continue
        p_depth = 0
        p_close = -1
        for i in range(p_open, len(masked)):
            if masked[i] == "(":
                p_depth += 1
            elif masked[i] == ")":
                p_depth -= 1
                if p_depth == 0:
                    p_close = i
                    break
        if p_close == -1:
            continue
        # Between `)` and `{` only whitespace, `throws ...` allowed (no `;` which would be abstract/call)
        brace_open = masked.find("{", p_close + 1)
        semi = masked.find(";", p_close + 1)
        if brace_open == -1 or (semi != -1 and semi < brace_open):
            continue
        between = masked[p_close + 1 : brace_open].strip()
        if between and not between.startswith("throws"):
            continue

        # Walk forward to matching `}`
        b_depth = 0
        brace_close = -1
        for i in range(brace_open, len(masked)):
            if masked[i] == "{":
                b_depth += 1
            elif masked[i] == "}":
                b_depth -= 1
                if b_depth == 0:
                    brace_close = i + 1
                    break
        if brace_close == -1:
            continue

        # Walk backward from `line_start` to include contiguous `@Annotation(...)` lines
        lines = masked[:line_start].splitlines(True)
        ann_start = line_start
        idx_cursor = line_start
        k = len(lines) - 1
        while k >= 0:
            ln_stripped = lines[k].strip()
            idx_cursor -= len(lines[k])
            if not ln_stripped:
                # Peek one line further up in case blank line sits right after @Test
                if k - 1 >= 0 and lines[k - 1].strip().startswith("@"):
                    k -= 1
                    continue
                break
            if ln_stripped.startswith("@") or ln_stripped.endswith(")"):
                # Verify if `.endswith(")")` is part of multi-line annotation
                if ln_stripped.startswith("@"):
                    ann_start = idx_cursor
                    k -= 1
                    continue
                # Check upward for `@`
                sub_k = k - 1
                sub_cursor = idx_cursor
                found_at = False
                while sub_k >= 0:
                    sub_ln = lines[sub_k].strip()
                    sub_cursor -= len(lines[sub_k])
                    if sub_ln.startswith("@"):
                        ann_start = sub_cursor
                        k = sub_k - 1
                        found_at = True
                        break
                    if ";" in sub_ln or "}" in sub_ln or "{" in sub_ln:
                        break
                    sub_k -= 1
                if found_at:
                    continue
            break
        return ann_start, brace_close
    return None


def extract_java_method(java_src: str, method_name: str) -> Optional[str]:
    span = find_java_method_span(java_src, method_name)
    if not span:
        return None
    return java_src[span[0] : span[1]]


def replace_java_method(java_src: str, method_name: str, new_method_code: str) -> str:
    span = find_java_method_span(java_src, method_name)
    if not span:
        raise ValueError(f"Could not locate method `{method_name}` in class source")
    orig_slice = java_src[span[0] : span[1]]
    new_stripped = new_method_code.strip()
    # Preserve @Test if original had @Test and new_method_code omitted all test annotations before '{'
    new_hdr = new_stripped.split("{", 1)[0]
    if "@Test" in orig_slice.split("{", 1)[0] and not re.search(
        r"@(?:org\.junit(?:\.jupiter\.api)?\.)?(?:Test|RepeatedTest|ParameterizedTest)\b", new_hdr
    ):
        new_stripped = "@Test\n" + new_stripped
    return java_src[: span[0]] + "\n" + new_stripped + "\n" + java_src[span[1] :]


def inject_java_imports(java_src: str, imports: List[str]) -> str:
    if not imports:
        return java_src
    clean_imps = []
    for imp in imports:
        for ln in str(imp).splitlines():
            ln = ln.strip()
            if ln.startswith("import ") and ln.endswith(";") and ln not in java_src:
                clean_imps.append(ln)
    if not clean_imps:
        return java_src
    block = "\n".join(dict.fromkeys(clean_imps)) + "\n"
    m = re.search(r"^\s*package\s+[A-Za-z0-9_.]+;\s*$", java_src, re.MULTILINE)
    if m:
        return java_src[: m.end()] + "\n" + block + java_src[m.end() :]
    return block + java_src


def inject_pom_dependencies(pom_xml: str, dep_xml: Optional[str]) -> str:
    if not dep_xml or not dep_xml.strip() or dep_xml.strip() == "None":
        return pom_xml
    cleaned = dep_xml.strip()
    cleaned = re.sub(r"</?dependencies>", "", cleaned).strip()
    if "<dependency>" not in cleaned:
        return pom_xml
    # Avoid duplicating artifactId already present in pom.xml
    art_m = re.search(r"<artifactId>\s*([^<\s]+)\s*</artifactId>", cleaned)
    if art_m and f"<artifactId>{art_m.group(1)}</artifactId>" in pom_xml:
        return pom_xml
    # Insert outside <dependencyManagement>
    dm_end = pom_xml.find("</dependencyManagement>")
    search_from = dm_end + len("</dependencyManagement>") if dm_end != -1 else 0
    deps_idx = pom_xml.find("<dependencies>", search_from)
    if deps_idx != -1:
        ins = deps_idx + len("<dependencies>")
        return pom_xml[:ins] + "\n" + cleaned + "\n" + pom_xml[ins:]
    proj_end = pom_xml.rfind("</project>")
    if proj_end != -1:
        return pom_xml[:proj_end] + f"\n<dependencies>\n{cleaned}\n</dependencies>\n" + pom_xml[proj_end:]
    return pom_xml


def isolate_single_test_method(java_src: str, target_method: str) -> str:
    """
    Disable all sibling test methods in `java_src` except `target_method`:
      - JUnit 4 / JUnit 5: comment out `@Test` / `@RepeatedTest` / `@ParameterizedTest`.
      - JUnit 3 (`extends TestCase` or classes without `@Test` annotations): rename sibling
        `public void test*()` methods to `private void /*FT1_ISOLATED_J3*/ _ft1_iso_test*()`
        so JUnit 3 reflection runners execute ONLY `target_method`.
    """
    # First self-heal any leftover FT1_ISOLATED wrappers so nested comments never occur
    java_src = re.sub(r"/\*FT1_ISOLATED\s*(.*?)\s*\*/", r"\1", java_src)
    java_src = re.sub(r"\bprivate\s+void\s+/\*FT1_ISOLATED_J3\*/\s+_ft1_iso_", "public void ", java_src)
    masked = mask_java_comments_and_literals(java_src)
    is_junit3_class = bool(
        re.search(r"\bextends\s+(?:junit\.framework\.)?TestCase\b", masked)
        or (not re.search(r"@(?:org\.junit(?:\.jupiter\.api)?\.)?(?:Test|RepeatedTest|ParameterizedTest)\b", masked))
    )
    sig_re = re.compile(
        r"(?<![.\w$])(?:(?:public|protected|private|static|final|synchronized)\s+)*"
        r"(?:void|[A-Za-z_$][A-Za-z0-9_$.<>\[\]]*)\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*\(",
        re.MULTILINE,
    )
    edits: List[Tuple[int, int, str]] = []
    for m in sig_re.finditer(masked):
        mname = m.group(1)
        if mname == target_method:
            continue
        span = find_java_method_span(java_src, mname)
        if not span:
            continue
        header_end = java_src.find("{", span[0], span[1])
        if header_end == -1:
            continue
        header = java_src[span[0] : header_end]
        new_header = re.sub(
            r"@(?:org\.junit(?:\.jupiter\.api)?\.)?(?:Test|RepeatedTest|ParameterizedTest)(?:\s*\([^)]*\))?",
            lambda am: f"/*FT1_ISOLATED {am.group(0)} */",
            header,
        )
        if new_header == header and is_junit3_class and mname.startswith("test"):
            j3_pat = re.compile(r"\bpublic\s+void\s+" + re.escape(mname) + r"(?=\s*\(\s*\))")
            if j3_pat.search(header):
                new_header = j3_pat.sub(f"private void /*FT1_ISOLATED_J3*/ _ft1_iso_{mname}", header, count=1)
        if new_header != header:
            edits.append((span[0], header_end, new_header))
    if not edits:
        return java_src
    # Deduplicate overlapping spans and apply in reverse order
    unique_edits = sorted(set(edits), key=lambda x: x[0], reverse=True)
    out = java_src
    last_start = len(out) + 1
    for s, e, rep in unique_edits:
        if e <= last_start:
            out = out[:s] + rep + out[e:]
            last_start = s
    return out


def extract_context_header(java_src: str, max_chars: int = 3500) -> str:
    """Extract package, imports, fields, and @Before/@BeforeClass/@After/@AfterClass fixture methods."""
    lines = java_src.splitlines()
    imps = [ln for ln in lines if ln.strip().startswith(("package ", "import "))]
    cls_idx = java_src.find("class ")
    fields_part = ""
    if cls_idx != -1:
        brace_idx = java_src.find("{", cls_idx)
        if brace_idx != -1:
            # Grab lines from class declaration up to first `@` or `public void` method
            sub_lines = java_src[cls_idx : brace_idx + 1200].splitlines()
            acc = []
            for i, ln in enumerate(sub_lines):
                if i > 1 and (ln.strip().startswith("@Test") or " void test" in ln):
                    break
                acc.append(ln)
            fields_part = "\n".join(acc[:35])
    # Also find @Before / @After methods
    fixtures = []
    for m in re.finditer(r"@(?:BeforeClass|BeforeAll|BeforeEach|Before|AfterEach|After|AfterClass|AfterAll)\b", java_src):
        pos = m.start()
        m_name_match = re.search(r"\bvoid\s+([A-Za-z0-9_]+)\s*\(", java_src[pos : pos + 400])
        if m_name_match:
            f_src = extract_java_method(java_src, m_name_match.group(1))
            if f_src and f_src not in fixtures:
                fixtures.append(f_src.strip())
    combined = "\n".join(imps[:40]) + "\n\n" + fields_part + "\n\n" + "\n\n".join(fixtures[:4])
    return combined[:max_chars]


def find_test_file(mod_dir: str, test_class_fqcn: str) -> Optional[str]:
    rel_java = test_class_fqcn.replace(".", "/") + ".java"
    for prefix in ("src/test/java", "src/it/java", "test", "tests"):
        cand = os.path.join(mod_dir, prefix, rel_java)
        if os.path.exists(cand):
            return cand
    # Fallback walk inside mod_dir
    cls_file = test_class_fqcn.split(".")[-1] + ".java"
    pkg_tail = "/".join(test_class_fqcn.split(".")[:-1])
    for root, _, files in os.walk(mod_dir):
        if cls_file in files:
            fp = os.path.join(root, cls_file)
            if pkg_tail in fp.replace("\\", "/"):
                return fp
    return None


def discover_focal_classes(
    mod_dir: str,
    test_class_fqcn: str,
    test_class_src: str,
    orig_method_src: str,
    max_classes: int = 12,
) -> Tuple[List[str], Dict[str, str]]:
    """
    Identify same-module production focal classes dynamically/statically exercised by the target test.
    Returns (list_of_fqcn, map_fqcn_to_abs_java_path).
    """
    main_src_root = os.path.join(mod_dir, "src/main/java")
    if not os.path.isdir(main_src_root):
        return [], {}

    fqcn_to_path: Dict[str, str] = {}
    simple_to_fqcns: Dict[str, List[str]] = {}
    pkg_to_fqcns: Dict[str, List[str]] = {}

    for root, _, files in os.walk(main_src_root):
        for fn in files:
            if not fn.endswith(".java") or fn == "package-info.java" or fn == "module-info.java":
                continue
            abs_p = os.path.join(root, fn)
            rel_p = os.path.relpath(abs_p, main_src_root).replace("\\", "/")
            fqcn = rel_p[:-5].replace("/", ".")
            simple = fqcn.split(".")[-1]
            pkg = ".".join(fqcn.split(".")[:-1])
            fqcn_to_path[fqcn] = abs_p
            simple_to_fqcns.setdefault(simple, []).append(fqcn)
            pkg_to_fqcns.setdefault(pkg, []).append(fqcn)

    if not fqcn_to_path:
        return [], {}

    test_pkg = ".".join(test_class_fqcn.split(".")[:-1])
    test_simple = test_class_fqcn.split(".")[-1]
    base_simple = re.sub(r"(?:Tests|Test|TestCase|IT)$", "", test_simple)

    # Identifiers in orig_method_src and class header/fixtures
    context_src = extract_context_header(test_class_src, max_chars=6000) + "\n" + orig_method_src
    tokens_in_method = set(re.findall(r"\b[A-Z][A-Za-z0-9_]*\b", orig_method_src))
    tokens_in_ctx = set(re.findall(r"\b[A-Z][A-Za-z0-9_]*\b", context_src))
    explicit_imports = set(re.findall(r"^\s*import\s+(?:static\s+)?([A-Za-z0-9_.]+);", test_class_src, re.MULTILINE))

    ranked: List[str] = []

    def add_fqcn(c: str) -> None:
        if c in fqcn_to_path and c not in ranked:
            ranked.append(c)

    # 1. Exact focal class matching FooTest -> Foo
    if base_simple:
        for c in simple_to_fqcns.get(base_simple, []):
            if c.startswith(test_pkg + ".") or len(simple_to_fqcns[base_simple]) == 1:
                add_fqcn(c)
        for s_name, c_list in simple_to_fqcns.items():
            if s_name.startswith(base_simple) and len(base_simple) >= 4:
                for c in c_list:
                    if c.startswith(test_pkg + "."):
                        add_fqcn(c)

    # 2. Explicitly imported same-module classes used in orig_method_src or context
    for imp in sorted(explicit_imports):
        if imp in fqcn_to_path:
            add_fqcn(imp)
        else:
            # Static import of Class.member
            parent_cls = ".".join(imp.split(".")[:-1])
            if parent_cls in fqcn_to_path:
                add_fqcn(parent_cls)

    # 3. Classes whose simple name appears directly in orig_method_src
    for tok in sorted(tokens_in_method):
        if tok in simple_to_fqcns:
            cands = simple_to_fqcns[tok]
            same_p = [c for c in cands if c.startswith(test_pkg)]
            for c in (same_p or cands[:2]):
                add_fqcn(c)

    # 4. Classes whose simple name appears in test fixtures/fields
    for tok in sorted(tokens_in_ctx):
        if len(ranked) >= max_classes:
            break
        if tok in simple_to_fqcns:
            cands = simple_to_fqcns[tok]
            same_p = [c for c in cands if c.startswith(test_pkg)]
            for c in (same_p or cands[:1]):
                add_fqcn(c)

    # 5. Same-package classes if we still have room
    for c in sorted(pkg_to_fqcns.get(test_pkg, [])):
        if len(ranked) >= max_classes:
            break
        add_fqcn(c)

    # If module has <= 80 production classes, let PIT's dynamic coverage minion cover all dynamically-executed
    # classes in the module's top-2 package roots (captures internal delegates/serializers in subpackages!).
    if len(fqcn_to_path) <= 80:
        pkg_roots: Set[str] = set()
        for c in fqcn_to_path:
            parts = c.split(".")
            root_p = ".".join(parts[:2]) + ".*" if len(parts) >= 2 else c
            pkg_roots.add(root_p)
        if len(pkg_roots) <= 6:
            return sorted(pkg_roots), fqcn_to_path

    # For larger modules (> 80 classes), include ranked focal classes + same-package subtree if <= 35 classes
    subpkg_classes = [c for c in fqcn_to_path if c == test_pkg or c.startswith(test_pkg + ".")]
    target_list = list(ranked[:max_classes])
    if 0 < len(subpkg_classes) <= 35 and test_pkg:
        glob_pkg = f"{test_pkg}.*"
        if glob_pkg not in target_list:
            target_list.append(glob_pkg)

    return target_list[: max_classes + 2], fqcn_to_path


def get_source_slice(java_path: Optional[str], line_no: int, radius: int = 3) -> str:
    if not java_path or not os.path.exists(java_path) or line_no <= 0:
        return f"(line {line_no})"
    lines = open(java_path, encoding="utf-8", errors="replace").read().splitlines()
    lo = max(1, line_no - radius)
    hi = min(len(lines), line_no + radius)
    out = []
    for ln_i in range(lo, hi + 1):
        marker = ">>" if ln_i == line_no else "  "
        out.append(f"{marker} L{ln_i}: {lines[ln_i - 1]}")
    return "\n".join(out)


def ensure_order_runner_ready(proj_dir: str, mod_rel: str, jdk: str) -> bool:
    """Build target/ft1_cp.txt once per module so JUnit4 order/clean probes execute in <1s."""
    mod_dir = proj_dir if mod_rel in ("", ".") else os.path.join(proj_dir, mod_rel)
    cp_file = os.path.join(mod_dir, "target/ft1_cp.txt")
    if not os.path.exists(cp_file):
        pl = f"-pl {mod_rel}" if mod_rel not in ("", ".") else ""
        rc, out, _ = docker_mvn(
            proj_dir, jdk,
            f"test-compile dependency:build-classpath {pl} -Dmdep.outputFile=target/ft1_cp.txt",
            timeout=120,
        )
        if not os.path.exists(cp_file):
            return False
    return True


def run_junit4_specs(
    proj_dir: str,
    mod_rel: str,
    jdk: str,
    specs: List[str],
    timeout: int = 60,
) -> Tuple[bool, str, float]:
    """Run ordered `Class#method` list in a single fresh JVM via FT1OrderRunner."""
    mod_dir = proj_dir if mod_rel in ("", ".") else os.path.join(proj_dir, mod_rel)
    tclasses = os.path.join(mod_dir, "target/test-classes")
    os.makedirs(tclasses, exist_ok=True)
    runner_java_path = os.path.join(tclasses, "FT1OrderRunner.java")
    runner_cls_path = os.path.join(tclasses, "FT1OrderRunner.class")
    quoted_specs = " ".join(f"'{s}'" for s in specs)
    try:
        open(runner_java_path, "w", encoding="utf-8").write(JUNIT4_ORDER_RUNNER_SRC)
        bash_cmd = (
            "CP=target/test-classes:target/classes:$(cat target/ft1_cp.txt) && "
            "if [ ! -f target/test-classes/FT1OrderRunner.class ]; then javac -cp \"$CP\" target/test-classes/FT1OrderRunner.java; fi && "
            f"java -cp \"$CP\" FT1OrderRunner {quoted_specs}"
        )
        rc, out, dt = run_docker_cmd(proj_dir, jdk, bash_cmd, workdir_rel=mod_rel, timeout=timeout)
        ok = (rc == 0) and (f"FT1_SUMMARY RUN={len(specs)} FAIL=0 IGNORE=0" in out)
        return ok, out, dt
    finally:
        for fp in (runner_java_path, runner_cls_path):
            if os.path.exists(fp):
                try:
                    os.remove(fp)
                except OSError:
                    pass
        # Also clean inner classes FT1OrderRunner$1.class, FT1OrderRunner$2.class
        for fn in os.listdir(tclasses):
            if fn.startswith("FT1OrderRunner"):
                try:
                    os.remove(os.path.join(tclasses, fn))
                except OSError:
                    pass


def run_clean_single_test(
    proj_dir: str,
    mod_rel: str,
    jdk: str,
    uses_junit5: bool,
    test_fqcn: str,
    test_method: str,
    runs: int = 5,
) -> Tuple[bool, str, float]:
    t0 = time.time()
    spec = f"{test_fqcn}#{test_method}"
    if not uses_junit5 and ensure_order_runner_ready(proj_dir, mod_rel, jdk):
        mod_dir = proj_dir if mod_rel in ("", ".") else os.path.join(proj_dir, mod_rel)
        tclasses = os.path.join(mod_dir, "target/test-classes")
        os.makedirs(tclasses, exist_ok=True)
        runner_java_path = os.path.join(tclasses, "FT1OrderRunner.java")
        try:
            open(runner_java_path, "w", encoding="utf-8").write(JUNIT4_ORDER_RUNNER_SRC)
            bash_cmd = (
                "CP=target/test-classes:target/classes:$(cat target/ft1_cp.txt) && "
                "if [ ! -f target/test-classes/FT1OrderRunner.class ]; then javac -cp \"$CP\" target/test-classes/FT1OrderRunner.java; fi && "
                f"for idx in $(seq 1 {runs}); do "
                f"out=$(java -cp \"$CP\" FT1OrderRunner '{spec}' 2>&1); rc=$?; "
                "if [ $rc -ne 0 ] || ! echo \"$out\" | grep -q 'FT1_SUMMARY RUN=1 FAIL=0 IGNORE=0'; then "
                "echo \"CLEAN_FAIL_ITER=$idx\"; echo \"$out\"; exit 1; fi; "
                "done && echo 'ALL_CLEAN_OK'"
            )
            rc_code, out, _ = run_docker_cmd(proj_dir, jdk, bash_cmd, workdir_rel=mod_rel, timeout=max(45, runs * 20))
            if rc_code == 0 and "ALL_CLEAN_OK" in out:
                return True, f"Passed {runs}/{runs} clean isolated JVM runs", (time.time() - t0)
            return False, f"Clean run failed:\n{out[-1000:]}", (time.time() - t0)
        finally:
            for fn in os.listdir(tclasses):
                if fn.startswith("FT1OrderRunner"):
                    try:
                        os.remove(os.path.join(tclasses, fn))
                    except OSError:
                        pass
    else:
        pl = f"-pl {mod_rel}" if mod_rel not in ("", ".") else ""
        for i in range(runs):
            rc_code, out, _ = docker_mvn(proj_dir, jdk, f"test {pl} -Dtest={spec}", timeout=90)
            if "BUILD SUCCESS" not in out or re.search(r"Failures:\s*[1-9]|Errors:\s*[1-9]|Skipped:\s*[1-9]", out):
                return False, f"Clean mvn test {i+1}/{runs} failed:\n{out[-1000:]}", (time.time() - t0)
        return True, f"Passed {runs}/{runs} clean mvn test runs", (time.time() - t0)


def check_stability_w_plus(
    proj_dir: str,
    mod_rel: str,
    jdk: str,
    uses_junit5: bool,
    test_type: str,
    victim_fqcn: str,
    victim_method: str,
    polluter_fqcn: str = "",
    polluter_method: str = "",
    nondex_runs: int = 5,
) -> Tuple[bool, str, float]:
    """
    Evaluate W+ stability gate:
    - ID: NonDex multi-seed execution (-DnondexRuns=5) with 0 failures/errors/skips.
    - OD: standalone victim, polluter -> victim, AND victim -> polluter all pass with 0 failures/skips.
    """
    pl = f"-pl {mod_rel}" if mod_rel not in ("", ".") else ""
    if test_type == "ID":
        spec = f"{victim_fqcn}#{victim_method}"
        rc_code, out, dt = docker_mvn(
            proj_dir,
            jdk,
            f"edu.illinois:nondex-maven-plugin:2.1.7:nondex {pl} -Dtest={spec} -DnondexRuns={nondex_runs} -Dstyle.color=never",
            timeout=150,
        )
        has_fail = (
            ("BUILD FAILURE" in out)
            or bool(re.search(r"Failures:\s*[1-9]|Errors:\s*[1-9]|Skipped:\s*[1-9]", out))
            or ("There are test failures" in out)
        )
        if rc_code == 0 and "BUILD SUCCESS" in out and not has_fail:
            return True, "NonDex passed across all seeds", dt
        # Extract concise failure diagnostic
        err_lines = []
        for ln in out.splitlines():
            if any(k in ln for k in ("[ERROR]", "AssertionError", "ComparisonFailure", "Exception", "expected", "but was", victim_fqcn.split(".")[-1])):
                err_lines.append(ln)
        return False, "\n".join(err_lines[-25:])[-1800:], dt
    else:
        t0 = time.time()
        v_spec = f"{victim_fqcn}#{victim_method}"
        p_spec = f"{polluter_fqcn}#{polluter_method}"
        if not uses_junit5 and ensure_order_runner_ready(proj_dir, mod_rel, jdk):
            mod_dir = proj_dir if mod_rel in ("", ".") else os.path.join(proj_dir, mod_rel)
            tclasses = os.path.join(mod_dir, "target/test-classes")
            os.makedirs(tclasses, exist_ok=True)
            runner_java_path = os.path.join(tclasses, "FT1OrderRunner.java")
            try:
                open(runner_java_path, "w", encoding="utf-8").write(JUNIT4_ORDER_RUNNER_SRC)
                rev_check = ""
                if p_spec != v_spec:
                    rev_check = (
                        f"out3=$(java -cp \"$CP\" FT1OrderRunner '{v_spec}' '{p_spec}' 2>&1); rc3=$?; "
                        "if [ $rc3 -ne 0 ] || ! echo \"$out3\" | grep -q 'FT1_SUMMARY RUN=2 FAIL=0 IGNORE=0'; then "
                        "echo 'Reverse order victim -> polluter failed:'; echo \"$out3\"; exit 1; fi; "
                    )
                bash_cmd = (
                    "CP=target/test-classes:target/classes:$(cat target/ft1_cp.txt) && "
                    "if [ ! -f target/test-classes/FT1OrderRunner.class ]; then javac -cp \"$CP\" target/test-classes/FT1OrderRunner.java; fi && "
                    f"out1=$(java -cp \"$CP\" FT1OrderRunner '{v_spec}' 2>&1); rc1=$?; "
                    "if [ $rc1 -ne 0 ] || ! echo \"$out1\" | grep -q 'FT1_SUMMARY RUN=1 FAIL=0 IGNORE=0'; then "
                    "echo 'Standalone victim failed:'; echo \"$out1\"; exit 1; fi && "
                    f"out2=$(java -cp \"$CP\" FT1OrderRunner '{p_spec}' '{v_spec}' 2>&1); rc2=$?; "
                    "if [ $rc2 -ne 0 ] || ! echo \"$out2\" | grep -q 'FT1_SUMMARY RUN=2 FAIL=0 IGNORE=0'; then "
                    "echo 'Order polluter -> victim failed:'; echo \"$out2\"; exit 1; fi && "
                    f"{rev_check}"
                    "echo 'FT1_OD_ALL_OK'"
                )
                rc_code, out, _ = run_docker_cmd(proj_dir, jdk, bash_cmd, workdir_rel=mod_rel, timeout=90)
                if rc_code == 0 and "FT1_OD_ALL_OK" in out:
                    return True, "OD passed standalone, polluter->victim, and victim->polluter", (time.time() - t0)
                return False, out[-1200:], (time.time() - t0)
            finally:
                for fn in os.listdir(tclasses):
                    if fn.startswith("FT1OrderRunner"):
                        try:
                            os.remove(os.path.join(tclasses, fn))
                        except OSError:
                            pass
        else:
            for order_arg in (v_spec, f"{p_spec},{v_spec}", f"{v_spec},{p_spec}"):
                rc_code, out, _ = docker_mvn(
                    proj_dir, jdk,
                    f"test {pl} -Dsurefire.runOrder=testorder -Dtest={order_arg}",
                    timeout=90,
                )
                if "BUILD SUCCESS" not in out or re.search(r"Failures:\s*[1-9]|Errors:\s*[1-9]|Skipped:\s*[1-9]", out):
                    return False, f"Surefire order `{order_arg}` failed:\n{out[-1200:]}", (time.time() - t0)
            return True, "OD passed Surefire orders", (time.time() - t0)


def inject_pit_plugin_xml(pom_xml: str, pit_ver: str, j5_ver: Optional[str]) -> str:
    # Remove any existing pitest-maven block first if project already configured an old PIT version
    cleaned_pom = re.sub(
        r"<plugin>\s*<groupId>\s*org\.pitest\s*</groupId>\s*<artifactId>\s*pitest-maven\s*</artifactId>.*?</plugin>",
        "",
        pom_xml,
        flags=re.DOTALL,
    )
    deps = ""
    if j5_ver:
        deps = (
            "<dependencies><dependency>"
            "<groupId>org.pitest</groupId>"
            "<artifactId>pitest-junit5-plugin</artifactId>"
            f"<version>{j5_ver}</version>"
            "</dependency></dependencies>"
        )
    plugin_block = (
        "<plugin>"
        "<groupId>org.pitest</groupId>"
        "<artifactId>pitest-maven</artifactId>"
        f"<version>{pit_ver}</version>"
        f"{deps}"
        "</plugin>"
    )
    b_idx = cleaned_pom.find("<build>")
    if b_idx != -1:
        end_b = cleaned_pom.find("</build>", b_idx)
        sub = cleaned_pom[b_idx:end_b]
        pm_e = sub.find("</pluginManagement>")
        search_start = pm_e + len("</pluginManagement>") if pm_e != -1 else 0
        p_idx = sub.find("<plugins>", search_start)
        if p_idx != -1:
            ins_pos = b_idx + p_idx + len("<plugins>")
            return cleaned_pom[:ins_pos] + plugin_block + cleaned_pom[ins_pos:]
        return cleaned_pom[:end_b] + f"<plugins>{plugin_block}</plugins>" + cleaned_pom[end_b:]
    return cleaned_pom.replace("</project>", f"<build><plugins>{plugin_block}</plugins></build></project>")


def backup_class_files(mod_dir: str, test_fqcn: str, invalidate: bool = True) -> Dict[str, Any]:
    """
    Backup compiled .class files of test_fqcn (and inner classes) together with their original
    `(bytes, atime, mtime)` metadata. When `invalidate=True` (default), deletes the backed-up
    `.class` files immediately so `mvn test-compile` under JDK 8's 1-second timestamp resolution
    NEVER skips recompilation with stale bytecode when candidates run back-to-back.
    """
    backups: Dict[str, Any] = {}
    tclasses = os.path.join(mod_dir, "target/test-classes")
    pkg_rel = "/".join(test_fqcn.split(".")[:-1])
    cls_simple = test_fqcn.split(".")[-1]
    pkg_dir = os.path.join(tclasses, pkg_rel) if pkg_rel else tclasses
    if os.path.isdir(pkg_dir):
        for fn in os.listdir(pkg_dir):
            if fn == f"{cls_simple}.class" or fn.startswith(f"{cls_simple}$"):
                fp = os.path.join(pkg_dir, fn)
                st = os.stat(fp)
                with open(fp, "rb") as fh:
                    data = fh.read()
                backups[fp] = (data, st.st_atime, st.st_mtime)
                if invalidate:
                    try:
                        os.remove(fp)
                    except OSError:
                        pass
    return backups


def restore_class_files(mod_dir: str, test_fqcn: str, backups: Dict[str, Any]) -> None:
    tclasses = os.path.join(mod_dir, "target/test-classes")
    pkg_rel = "/".join(test_fqcn.split(".")[:-1])
    cls_simple = test_fqcn.split(".")[-1]
    pkg_dir = os.path.join(tclasses, pkg_rel) if pkg_rel else tclasses
    if os.path.isdir(pkg_dir):
        for fn in os.listdir(pkg_dir):
            if fn == f"{cls_simple}.class" or fn.startswith(f"{cls_simple}$"):
                fp = os.path.join(pkg_dir, fn)
                if fp not in backups and os.path.exists(fp):
                    os.remove(fp)
    now_t = time.time()
    for fp, item in backups.items():
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        if isinstance(item, tuple):
            data, atime, mtime = item
        else:
            data, atime, mtime = item, now_t - 10.0, now_t - 10.0
        with open(fp, "wb") as fh:
            fh.write(data)
        # Preserve old mtime (strictly <= now - 5s) so subsequent edits are always strictly newer
        safe_m = min(float(mtime), now_t - 5.0)
        try:
            os.utime(fp, (float(atime), safe_m))
        except OSError:
            pass


def mutant_canonical_id(m_dict: Dict[str, Any]) -> str:
    return (
        f"{m_dict['mutatedClass']}::{m_dict['mutatedMethod']}"
        f"{m_dict.get('methodDescription', '')}:L{m_dict['lineNumber']}:"
        f"{m_dict['mutator'].split('.')[-1]}:{m_dict.get('index', '')}:{m_dict['description']}"
    )


def run_single_focal_pit(
    proj_dir: str,
    mod_rel: str,
    jdk: str,
    uses_junit5: bool,
    test_file_path: str,
    test_fqcn: str,
    test_method: str,
    focal_classes: List[str],
    fqcn_to_path: Optional[Dict[str, str]] = None,
    forced_pit_pair: Optional[Tuple[str, Optional[str]]] = None,
    timeout: int = 120,
) -> Tuple[bool, List[Dict[str, Any]], float, Tuple[str, Optional[str]], str]:
    """
    Execute a single-test isolated focal PIT run and parse `mutations.xml`.
    Returns (ok, mutants_list, wall_sec, working_pit_pair, raw_tail).
    """
    mod_dir = proj_dir if mod_rel in ("", ".") else os.path.join(proj_dir, mod_rel)
    pom_path = os.path.join(mod_dir, "pom.xml")
    orig_test_src = open(test_file_path, encoding="utf-8", errors="replace").read()
    orig_pom_src = open(pom_path, encoding="utf-8", errors="replace").read()
    cls_backups = backup_class_files(mod_dir, test_fqcn)

    if forced_pit_pair:
        pit_candidates = [forced_pit_pair]
    elif not uses_junit5:
        pit_candidates = [("1.9.11" if jdk == "8" else "1.15.2", None)]
    else:
        if jdk == "8":
            pit_candidates = [("1.7.4", "0.15"), ("1.9.11", "1.1.0")]
        else:
            pit_candidates = [("1.7.4", "0.15"), ("1.15.2", "1.2.1"), ("1.9.11", "1.1.0")]

    pl = f"-pl {mod_rel}" if mod_rel not in ("", ".") else ""
    target_cls_arg = ",".join(focal_classes)
    mxml_path = os.path.join(mod_dir, "target/pit-reports/mutations.xml")

    try:
        isolated_src = isolate_single_test_method(orig_test_src, test_method)
        open(test_file_path, "w", encoding="utf-8").write(isolated_src)

        last_out = ""
        for pit_ver, j5_ver in pit_candidates:
            if os.path.exists(mxml_path):
                os.remove(mxml_path)
            open(pom_path, "w", encoding="utf-8").write(inject_pit_plugin_xml(orig_pom_src, pit_ver, j5_ver))
            rc, out, dt = docker_mvn(
                proj_dir,
                jdk,
                f"test-compile org.pitest:pitest-maven:{pit_ver}:mutationCoverage {pl} "
                f"-DtargetClasses={target_cls_arg} -DtargetTests={test_fqcn} "
                f"-DoutputFormats=XML -DtimestampedReports=false -Dthreads=3 -DfailWhenNoMutations=false",
                timeout=timeout,
            )
            last_out = out
            if os.path.exists(mxml_path) and os.path.getsize(mxml_path) > 0:
                try:
                    root = ET.parse(mxml_path).getroot()
                except ET.ParseError:
                    continue
                muts: List[Dict[str, Any]] = []
                for m_el in root.findall("mutation"):
                    m_cls = m_el.findtext("mutatedClass", "")
                    # Normalize inner class Foo$Bar -> Foo for source lookup
                    outer_cls = m_cls.split("$")[0]
                    line_no = int(m_el.findtext("lineNumber", "0") or "0")
                    m_obj: Dict[str, Any] = {
                        "status": m_el.attrib.get("status", ""),
                        "detected": m_el.attrib.get("detected", "") == "true",
                        "mutatedClass": m_cls,
                        "mutatedMethod": m_el.findtext("mutatedMethod", ""),
                        "methodDescription": m_el.findtext("methodDescription", ""),
                        "lineNumber": line_no,
                        "mutator": m_el.findtext("mutator", ""),
                        "index": m_el.findtext("index", ""),
                        "description": m_el.findtext("description", ""),
                    }
                    m_obj["id"] = mutant_canonical_id(m_obj)
                    if fqcn_to_path and outer_cls in fqcn_to_path:
                        m_obj["source_slice"] = get_source_slice(fqcn_to_path[outer_cls], line_no)
                    muts.append(m_obj)
                return True, muts, dt, (pit_ver, j5_ver), out[-800:]
        return False, [], 0.0, pit_candidates[0], last_out[-1200:]
    finally:
        open(test_file_path, "w", encoding="utf-8").write(orig_test_src)
        open(pom_path, "w", encoding="utf-8").write(orig_pom_src)
        restore_class_files(mod_dir, test_fqcn, cls_backups)


def parse_flakydoctor_patch_file(
    patch_path: str,
    test_type: str,
    victim_method: str,
    polluter_method: str = "",
) -> Optional[Dict[str, Any]]:
    """Parse any of the 4 FlakyDoctor published .patch file formats into a normalized candidate dict."""
    if not os.path.exists(patch_path):
        return None
    txt = open(patch_path, encoding="utf-8", errors="replace").read()

    def parse_import_val(s: str) -> List[str]:
        s = s.strip()
        if not s or s == "None" or s == "[]":
            return []
        if s.startswith("["):
            try:
                arr = ast.literal_eval(s)
                return [str(x).strip() for x in arr if str(x).strip()]
            except Exception:
                pass
        return [ln.strip() for ln in s.splitlines() if ln.strip().startswith("import ")]

    def parse_pom_val(s: str) -> Optional[str]:
        s = s.strip()
        if not s or s == "None":
            return None
        return s

    if test_type == "ID":
        work = txt
        if "Patch after Stitching:" in work:
            work = work.split("Patch after Stitching:", 1)[1]
            if "Patch before Stitching:" in work:
                work = work.split("Patch before Stitching:", 1)[0]
        if "test_after_fix:" in work and "\ncode:\n" in work:
            after = work.split("test_after_fix:", 1)[1]
            code_part = after.split("\ncode:\n", 1)[1]
            imp_part = ""
            pom_part = ""
            if "\nimport:\n" in code_part:
                code_part, rest = code_part.split("\nimport:\n", 1)
                if "\npom:\n" in rest:
                    imp_part, pom_part = rest.split("\npom:\n", 1)
                else:
                    imp_part = rest
            return {
                "test_code": code_part.strip() or None,
                "imports": parse_import_val(imp_part),
                "pom": parse_pom_val(pom_part),
            }
        if "\ntest_code:\n" in work or work.startswith("test_code:\n"):
            after = work.split("test_code:\n", 1)[1]
            code_part = after
            imp_part = ""
            pom_part = ""
            if "\nimport:\n" in code_part:
                code_part, rest = code_part.split("\nimport:\n", 1)
                if "\npom:\n" in rest:
                    imp_part, pom_part = rest.split("\npom:\n", 1)
                else:
                    imp_part = rest
            code_s = code_part.strip()
            return {
                "test_code": None if code_s in ("", "None") else code_s,
                "imports": parse_import_val(imp_part),
                "pom": parse_pom_val(pom_part),
            }
        return None
    else:
        # OD Variant A: victim_test_code: / polluter_test_code:
        if "victim_test_code:" in txt:
            work = txt
            if "Patch after Stitching:" in work:
                work = work.split("Patch after Stitching:", 1)[1]
                if "Patch before Stitching:" in work:
                    work = work.split("Patch before Stitching:", 1)[0]
            vic_s, pol_s, imp_s, pom_s = "", "", "", ""
            m_v = re.search(r"victim_test_code:\n(.*?)(?=\npolluter_test_code:|\nimport:|\npom:|\Z)", work, re.DOTALL)
            m_p = re.search(r"polluter_test_code:\n(.*?)(?=\nvictim_test_code:|\nimport:|\npom:|\Z)", work, re.DOTALL)
            m_i = re.search(r"\nimport:\n(.*?)(?=\npom:|\Z)", work, re.DOTALL)
            m_pom = re.search(r"\npom:\n(.*?)\Z", work, re.DOTALL)
            if m_v:
                vic_s = m_v.group(1).strip()
            if m_p:
                pol_s = m_p.group(1).strip()
            if m_i:
                imp_s = m_i.group(1).strip()
            if m_pom:
                pom_s = m_pom.group(1).strip()
            return {
                "victim_code": None if vic_s in ("", "None") else vic_s,
                "polluter_code": None if pol_s in ("", "None") else pol_s,
                "imports": parse_import_val(imp_s),
                "pom": parse_pom_val(pom_s),
            }
        # OD Variant B: goodPatches dict format (`victim\n:code:\n{...}` / `polluter\n:code:\n{...}`)
        vic_code = None
        pol_code = None
        imps: List[str] = []
        pom_val: Optional[str] = None
        for role, mname in (("victim", victim_method), ("polluter", polluter_method)):
            m_c = re.search(rf"{role}\s*:code:\s*(\{{.*?\}})(?={role}\s*:import:|polluter|victim|\Z)", txt, re.DOTALL)
            if m_c:
                try:
                    d = ast.literal_eval(m_c.group(1).strip())
                    methods_d = d.get("methods", {}) or {}
                    if mname in methods_d and methods_d[mname]:
                        if role == "victim":
                            vic_code = methods_d[mname]
                        else:
                            pol_code = methods_d[mname]
                    elif len(methods_d) == 1:
                        only_m, only_src = next(iter(methods_d.items()))
                        if only_m == victim_method:
                            vic_code = only_src
                        elif only_m == polluter_method:
                            pol_code = only_src
                except Exception:
                    pass
            m_i = re.search(rf"{role}\s*:import:\s*(\[.*?\])", txt, re.DOTALL)
            if m_i:
                imps.extend(parse_import_val(m_i.group(1)))
            m_pm = re.search(rf"{role}\s*:pom:\s*(.*?)(?={role}\s*:toreplace:|polluter|victim|\Z)", txt, re.DOTALL)
            if m_pm and m_pm.group(1).strip():
                pom_val = parse_pom_val(m_pm.group(1))
        if vic_code or pol_code:
            return {
                "victim_code": vic_code,
                "polluter_code": pol_code,
                "imports": imps,
                "pom": pom_val,
            }
        return None


def fetch_pr_method_patch(
    pr_url: str,
    test_file_rel: str,
    orig_class_src: str,
    method_name: str,
) -> Optional[Dict[str, Any]]:
    """
    Fetch developer merged PR .diff from GitHub and apply unified diff hunks if it cleanly patches `test_file_rel`,
    extracting the developer's fixed method `T_dev` and added imports for `M_gold` cross-anchor verification.
    """
    if not pr_url or "github.com/" not in pr_url or "/pull/" not in pr_url:
        return None
    clean_url = pr_url.strip().rstrip("/")
    diff_url = clean_url + ".diff"
    try:
        req = urllib.request.Request(diff_url, headers={"User-Agent": "FT1-Research/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            diff_txt = resp.read().decode("utf-8", errors="replace")
    except Exception:
        return None

    norm_rel = test_file_rel.replace("\\", "/").lstrip("/")
    # Find file diff section matching norm_rel
    sections = re.split(r"^diff --git ", diff_txt, flags=re.MULTILINE)
    target_sec = None
    for sec in sections[1:]:
        first_line = sec.splitlines()[0] if sec else ""
        if norm_rel in first_line or first_line.endswith(norm_rel.split("/")[-1]):
            if norm_rel.split("/")[-2] in first_line:
                target_sec = "diff --git " + sec
                break
    if not target_sec:
        return None

    # Apply patch to a temp copy using `patch` CLI
    import tempfile
    with tempfile.TemporaryDirectory() as tmpd:
        tmp_java = os.path.join(tmpd, "Target.java")
        tmp_diff = os.path.join(tmpd, "pr.diff")
        open(tmp_java, "w", encoding="utf-8").write(orig_class_src)
        # Strip header up to `@@ ` so `patch` applies directly to `tmp_java`
        idx_hunk = target_sec.find("\n@@ ")
        if idx_hunk == -1:
            return None
        patch_body = "--- Target.java\n+++ Target.java" + target_sec[idx_hunk:]
        if not patch_body.endswith("\n"):
            patch_body += "\n"
        open(tmp_diff, "w", encoding="utf-8").write(patch_body)
        p = subprocess.run(
            ["patch", "--batch", "--fuzz=2", tmp_java, tmp_diff],
            capture_output=True, text=True, timeout=10,
        )
        if p.returncode != 0:
            return None
        patched_src = open(tmp_java, encoding="utf-8", errors="replace").read()
        if patched_src.strip() == orig_class_src.strip():
            return None
        dev_method = extract_java_method(patched_src, method_name)
        orig_method = extract_java_method(orig_class_src, method_name)
        if not dev_method:
            return None
        if dev_method.strip() == (orig_method or "").strip():
            dev_method = "[Class-level @Before/@After or polluter PR fix]\n" + dev_method
        new_imports = [
            ln.strip()
            for ln in patched_src.splitlines()
            if ln.strip().startswith("import ") and ln.strip() not in orig_class_src
        ]
        return {
            "patched_class_src": patched_src,
            "dev_method_code": dev_method,
            "imports": new_imports,
        }


def evaluate_candidate_on_sample(
    sample: Dict[str, Any],
    candidate: Dict[str, Any],
    run_pit_if_stable: bool = True,
) -> Dict[str, Any]:
    """
    Deterministic End-to-End Evaluator for any candidate patch (B1, T_dev, B0, B2, B3, Ours_MuGuard_Flake):
      1) Apply candidate patch to test file(s) & pom.xml (restoring 100% cleanly in `finally:`)
      2) Compile (`mvn test-compile`)
      3) Check W+ stability (`NonDex` 5 seeds for ID; standalone + `polluter->victim` + `victim->polluter` for OD)
      4) If `run_pit_if_stable` and W+ passes: run single-test isolated focal PIT probe and check `M_det` & `M_gold`!
    """
    t0_total = time.time()
    proj_dir = sample["proj_dir"]
    mod_rel = sample["module"]
    jdk = sample["jdk"]
    uses_j5 = sample["uses_junit5"]
    ttype = sample["test_type"]
    mod_dir = proj_dir if mod_rel in ("", ".") else os.path.join(proj_dir, mod_rel)
    pom_path = os.path.join(mod_dir, "pom.xml")
    vic_file = sample["victim_file_path"]
    pol_file = sample.get("polluter_file_path") or vic_file

    orig_vic_src = re.sub(r"/\*FT1_ISOLATED\s*(.*?)\s*\*/", r"\1", open(vic_file, encoding="utf-8", errors="replace").read())
    orig_pol_src = re.sub(r"/\*FT1_ISOLATED\s*(.*?)\s*\*/", r"\1", open(pol_file, encoding="utf-8", errors="replace").read()) if pol_file != vic_file else orig_vic_src
    orig_pom_src = open(pom_path, encoding="utf-8", errors="replace").read()
    vic_cls_bak = backup_class_files(mod_dir, sample["victim_fqcn"])
    pol_cls_bak = backup_class_files(mod_dir, sample["polluter_fqcn"]) if sample.get("polluter_fqcn") and sample["polluter_fqcn"] != sample["victim_fqcn"] else {}

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
        "m_gold_total": len(sample.get("m_gold_ids", [])),
        "m_gold_killed": 0,
        "retention_m_gold": None,
        "eroded": False,
        "sound_fix": False,
        "total_eval_sec": 0.0,
    }

    try:
        # 1. Construct modified source files
        if candidate.get("patched_class_src"):
            new_vic_src = candidate["patched_class_src"]
            open(vic_file, "w", encoding="utf-8").write(new_vic_src)
        elif ttype == "ID":
            tcode = candidate.get("test_code")
            if not tcode:
                res["compile_error"] = "Missing // <fix start> ... // <fix end> method code."
                return res
            new_vic_src = replace_java_method(orig_vic_src, sample["victim_method"], tcode)
            new_vic_src = inject_java_imports(new_vic_src, candidate.get("imports") or [])
            open(vic_file, "w", encoding="utf-8").write(new_vic_src)
        else:
            vcode = candidate.get("victim_code")
            pcode = candidate.get("polluter_code")
            if not vcode and not pcode:
                res["compile_error"] = "Missing both victim and polluter method code."
                return res
            if vic_file == pol_file:
                new_src = orig_vic_src
                if vcode:
                    new_src = replace_java_method(new_src, sample["victim_method"], vcode)
                if pcode and sample["polluter_method"] != sample["victim_method"]:
                    new_src = replace_java_method(new_src, sample["polluter_method"], pcode)
                elif pcode and not vcode:
                    new_src = replace_java_method(new_src, sample["polluter_method"], pcode)
                new_src = inject_java_imports(new_src, candidate.get("imports") or [])
                open(vic_file, "w", encoding="utf-8").write(new_src)
            else:
                if vcode:
                    nv = replace_java_method(orig_vic_src, sample["victim_method"], vcode)
                    nv = inject_java_imports(nv, candidate.get("imports") or [])
                    open(vic_file, "w", encoding="utf-8").write(nv)
                if pcode:
                    np = replace_java_method(orig_pol_src, sample["polluter_method"], pcode)
                    np = inject_java_imports(np, candidate.get("imports") or [])
                    open(pol_file, "w", encoding="utf-8").write(np)

        if candidate.get("pom"):
            new_pom = inject_pom_dependencies(orig_pom_src, candidate["pom"])
            if new_pom != orig_pom_src:
                open(pom_path, "w", encoding="utf-8").write(new_pom)

        # 2. Compile test classes (and refresh ft1_cp.txt if pom changed)
        pl = f"-pl {mod_rel}" if mod_rel not in ("", ".") else ""
        extra_cp = "dependency:build-classpath -Dmdep.outputFile=target/ft1_cp.txt" if candidate.get("pom") else ""
        rc_c, out_c, dt_c = docker_mvn(proj_dir, jdk, f"test-compile {extra_cp} {pl}", timeout=120)
        res["compile_sec"] = round(dt_c, 2)
        if "BUILD SUCCESS" not in out_c and candidate.get("allow_static_assert_import_salvage") and "assert" in out_c:
            wildcard = "import static org.junit.jupiter.api.Assertions.*;" if uses_j5 else "import static org.junit.Assert.*;"
            cur_v = open(vic_file, encoding="utf-8", errors="replace").read()
            open(vic_file, "w", encoding="utf-8").write(inject_java_imports(cur_v, [wildcard]))
            if pol_file != vic_file:
                cur_p = open(pol_file, encoding="utf-8", errors="replace").read()
                open(pol_file, "w", encoding="utf-8").write(inject_java_imports(cur_p, [wildcard]))
            rc_c2, out_c2, dt_c2 = docker_mvn(proj_dir, jdk, f"test-compile {extra_cp} {pl}", timeout=120)
            res["compile_sec"] = round(dt_c + dt_c2, 2)
            if "BUILD SUCCESS" in out_c2:
                res["salvaged_missing_assert_import"] = True
                out_c = out_c2
        if "BUILD SUCCESS" not in out_c:
            err_lns = [ln for ln in out_c.splitlines() if "[ERROR]" in ln]
            res["compile_error"] = "\n".join(err_lns[:25])[-1800:] or out_c[-1000:]
            return res
        res["compiled"] = True

        # 3. Check W+ Stability
        w_ok, w_diag, dt_w = check_stability_w_plus(
            proj_dir=proj_dir,
            mod_rel=mod_rel,
            jdk=jdk,
            uses_junit5=uses_j5,
            test_type=ttype,
            victim_fqcn=sample["victim_fqcn"],
            victim_method=sample["victim_method"],
            polluter_fqcn=sample.get("polluter_fqcn", ""),
            polluter_method=sample.get("polluter_method", ""),
            nondex_runs=5,
        )
        res["w_plus_passed"] = w_ok
        res["w_plus_diag"] = w_diag
        res["w_plus_sec"] = round(dt_w, 2)

        if not w_ok or not run_pit_if_stable:
            return res

        # 4. Single-Test Focal PIT Conservation Probe on M_det(T_orig)
        pit_pair = tuple(sample["pit_pair"]) if sample.get("pit_pair") else None
        pit_ok, muts, dt_pit, _, pit_tail = run_single_focal_pit(
            proj_dir=proj_dir,
            mod_rel=mod_rel,
            jdk=jdk,
            uses_junit5=uses_j5,
            test_file_path=vic_file,
            test_fqcn=sample["victim_fqcn"],
            test_method=sample["victim_method"],
            focal_classes=sample["focal_classes"],
            fqcn_to_path=sample.get("fqcn_to_path"),
            forced_pit_pair=pit_pair,
            timeout=120,
        )
        res["pit_ran"] = pit_ok
        res["pit_sec"] = round(dt_pit, 2)
        if not pit_ok:
            res["w_plus_diag"] += f"\n[PIT ERROR]: {pit_tail[-500:]}"
            return res

        killed_now: Set[str] = {m["id"] for m in muts if m["status"] == "KILLED"}
        res["all_killed_ids"] = sorted(killed_now)

        survived_list: List[Dict[str, Any]] = []
        killed_count = 0
        for m_orig in sample["m_det"]:
            if m_orig["id"] in killed_now:
                killed_count += 1
            else:
                survived_list.append(m_orig)

        # For OD: if polluter_code was edited and sample has polluter_m_det, verify polluter didn't erode its own M_det
        if ttype == "OD" and candidate.get("polluter_code") and sample.get("polluter_m_det"):
            if candidate["polluter_code"].strip() != sample.get("orig_polluter_code", "").strip():
                p_ok, p_muts, dt_ppit, _, _ = run_single_focal_pit(
                    proj_dir=proj_dir,
                    mod_rel=mod_rel,
                    jdk=jdk,
                    uses_junit5=uses_j5,
                    test_file_path=pol_file,
                    test_fqcn=sample["polluter_fqcn"],
                    test_method=sample["polluter_method"],
                    focal_classes=sample["polluter_focal_classes"],
                    fqcn_to_path=sample.get("fqcn_to_path"),
                    forced_pit_pair=pit_pair,
                    timeout=120,
                )
                res["pit_sec"] = round(res["pit_sec"] + dt_ppit, 2)
                if p_ok:
                    p_killed_now = {m["id"] for m in p_muts if m["status"] == "KILLED"}
                    for pm in sample["polluter_m_det"]:
                        if pm["id"] not in p_killed_now:
                            pm_copy = dict(pm)
                            pm_copy["description"] = "[Polluter Oracle Eroded] " + pm_copy["description"]
                            survived_list.append(pm_copy)

        total_oblig = len(sample["m_det"])
        res["m_det_killed"] = killed_count
        res["survived_mutants"] = survived_list
        # If polluter also lost mutants, reflect in retention < 1.0
        eff_total = total_oblig + max(0, len(survived_list) - (total_oblig - killed_count))
        res["retention_m_det"] = round((eff_total - len(survived_list)) / eff_total, 4) if eff_total > 0 else 1.0

        gold_ids = sample.get("m_gold_ids") or []
        if gold_ids:
            g_killed = sum(1 for gid in gold_ids if gid in killed_now)
            res["m_gold_killed"] = g_killed
            res["retention_m_gold"] = round(g_killed / len(gold_ids), 4)

        res["eroded"] = len(survived_list) > 0
        res["sound_fix"] = bool(res["w_plus_passed"] and len(survived_list) == 0)
        return res
    except ValueError as ve:
        res["compile_error"] = str(ve)
        return res
    finally:
        open(vic_file, "w", encoding="utf-8").write(orig_vic_src)
        if pol_file != vic_file:
            open(pol_file, "w", encoding="utf-8").write(orig_pol_src)
        open(pom_path, "w", encoding="utf-8").write(orig_pom_src)
        restore_class_files(mod_dir, sample["victim_fqcn"], vic_cls_bak)
        if pol_cls_bak:
            restore_class_files(mod_dir, sample["polluter_fqcn"], pol_cls_bak)
        res["total_eval_sec"] = round(time.time() - t0_total, 2)
