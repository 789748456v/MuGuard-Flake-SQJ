"""
FT-1 v3.0 Remote Dynamic Coverage Preservation Gate Backend (`B5_Coverage_Gate`).

Measures exact single-test focal line coverage (and Java branch coverage) WITHOUT running
any mutation engine:
  - Java  : Attaches `org.jacoco.agent-0.8.10-runtime.jar` via `-javaagent` to isolated
            single-test execution (`FT1OrderRunner` for JUnit 3/4 or `mvn surefire:test`
            with `-DargLine` for JUnit 5), then parses `target/ft1_jacoco.exec` against
            `target/classes` with `FT1JacocoDump.java` using cached `org.jacoco.core-0.8.10.jar`
            + ASM 9.5 jars.
  - Python: Executes `python3 -m coverage run -m pytest` via `remote_python_core` and parses
            exact executed line numbers in same-repository production `.py` files.

CLI modes (reads JSON payload from stdin, writes single JSON line to stdout):
  - `baseline_cov` : computes baseline covered focal lines for target (and partner if OD)
  - `eval_cand_cov`: applies candidate patch, checks compile & W+ (or reuses pre-verified patch),
                     measures candidate focal coverage & computes lost lines + +-3 line source slices
  - `eval_pr_cov`  : evaluates a clean developer Java PR under Coverage Gate (line preservation)
"""
import json
import os
import re
import shutil
import sys
import time
from typing import Any, Dict, List, Optional, Set, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import remote_core as rc
import remote_python_core as rpc
from v2_guards_and_attribution import analyze_affected_test_scope, is_code_semantically_changed

JACOCO_AGENT = "/root/.m2/repository/org/jacoco/org.jacoco.agent/0.8.10/org.jacoco.agent-0.8.10-runtime.jar"
JACOCO_CORE = "/root/.m2/repository/org/jacoco/org.jacoco.core/0.8.10/org.jacoco.core-0.8.10.jar"
ASM_JARS = ":".join([
    "/root/.m2/repository/org/ow2/asm/asm/9.5/asm-9.5.jar",
    "/root/.m2/repository/org/ow2/asm/asm-commons/9.5/asm-commons-9.5.jar",
    "/root/.m2/repository/org/ow2/asm/asm-tree/9.5/asm-tree-9.5.jar",
])

JACOCO_DUMP_JAVA = r"""
import java.io.*;
import java.util.*;
import org.jacoco.core.analysis.*;
import org.jacoco.core.data.*;
import org.jacoco.core.tools.ExecFileLoader;

public class FT1JacocoDump {
    public static void main(String[] args) throws Exception {
        File execFile = new File(args[0]);
        File classesDir = new File(args[1]);
        Set<String> filterSet = new HashSet<String>();
        for (int i = 2; i < args.length; i++) {
            if (!args[i].isEmpty()) filterSet.add(args[i]);
        }
        ExecFileLoader loader = new ExecFileLoader();
        loader.load(execFile);
        CoverageBuilder builder = new CoverageBuilder();
        Analyzer analyzer = new Analyzer(loader.getExecutionDataStore(), builder);
        analyzer.analyzeAll(classesDir);

        StringBuilder sb = new StringBuilder();
        sb.append("{");
        boolean firstCls = true;
        for (IClassCoverage cc : builder.getClasses()) {
            String fqcn = cc.getName().replace('/', '.');
            String outer = fqcn.contains("$") ? fqcn.substring(0, fqcn.indexOf('$')) : fqcn;
            if (!filterSet.isEmpty()) {
                boolean match = filterSet.contains(fqcn) || filterSet.contains(outer);
                if (!match) {
                    for (String f : filterSet) {
                        if (f.endsWith(".*") && fqcn.startsWith(f.substring(0, f.length() - 1))) {
                            match = true; break;
                        }
                    }
                }
                if (!match) continue;
            }
            List<Integer> covLines = new ArrayList<Integer>();
            int firstL = cc.getFirstLine();
            int lastL = cc.getLastLine();
            if (firstL > 0 && lastL >= firstL) {
                for (int ln = firstL; ln <= lastL; ln++) {
                    int st = cc.getLine(ln).getStatus();
                    if (st == ICounter.FULLY_COVERED || st == ICounter.PARTLY_COVERED) {
                        covLines.add(ln);
                    }
                }
            }
            if (covLines.isEmpty()) continue;
            if (!firstCls) sb.append(",");
            firstCls = false;
            sb.append("\"").append(fqcn.replace("\"", "\\\"")).append("\":[");
            for (int k = 0; k < covLines.size(); k++) {
                if (k > 0) sb.append(",");
                sb.append(covLines.get(k));
            }
            sb.append("]");
        }
        sb.append("}");
        System.out.println("FT1_JACOCO_JSON=" + sb.toString());
    }
}
""".strip()


def run_java_jacoco_single_test(
    proj_dir: str,
    mod_rel: str,
    jdk: str,
    uses_j5: bool,
    test_fqcn: str,
    test_method: str,
    focal_filter: List[str],
    timeout: int = 90,
) -> Tuple[bool, Dict[str, List[int]], float, str]:
    """
    Execute ONE isolated Java test method with JaCoCo 0.8.10 agent and return:
      (ok, {fqcn: sorted_covered_lines}, elapsed_sec, diag)
    """
    t0 = time.time()
    mod_dir = proj_dir if mod_rel in ("", ".") else os.path.join(proj_dir, mod_rel)
    exec_rel = "target/ft1_jacoco.exec"
    exec_abs = os.path.join(mod_dir, exec_rel)
    tclasses = os.path.join(mod_dir, "target/test-classes")
    os.makedirs(tclasses, exist_ok=True)

    dump_java = os.path.join(tclasses, "FT1JacocoDump.java")
    dump_cls = os.path.join(tclasses, "FT1JacocoDump.class")
    runner_java = os.path.join(tclasses, "FT1OrderRunner.java")
    runner_cls = os.path.join(tclasses, "FT1OrderRunner.class")

    filter_args = " ".join(f"'{f}'" for f in focal_filter if f)
    agent_opt = f"-javaagent:{JACOCO_AGENT}=destfile={exec_rel},output=file"
    dump_cp = f"{JACOCO_CORE}:{ASM_JARS}:target/test-classes"

    try:
        if os.path.exists(exec_abs):
            os.remove(exec_abs)
        open(dump_java, "w", encoding="utf-8").write(JACOCO_DUMP_JAVA)

        # Ensure classpath file exists for fast JUnit 4 direct JVM execution
        used_direct = False
        if not uses_j5 and rc.ensure_order_runner_ready(proj_dir, mod_rel, jdk):
            open(runner_java, "w", encoding="utf-8").write(rc.JUNIT4_ORDER_RUNNER_SRC)
            spec = f"{test_fqcn}#{test_method}"
            bash_cmd = (
                f"rm -f {exec_rel} && "
                "CP=target/test-classes:target/classes:$(cat target/ft1_cp.txt) && "
                "javac -cp \"$CP\" target/test-classes/FT1OrderRunner.java && "
                f"java {agent_opt} -cp \"$CP\" FT1OrderRunner '{spec}' && "
                f"javac -cp '{dump_cp}' target/test-classes/FT1JacocoDump.java && "
                f"java -cp '{dump_cp}' FT1JacocoDump {exec_rel} target/classes {filter_args}"
            )
            rc_code, out, _ = rc.run_docker_cmd(proj_dir, jdk, bash_cmd, workdir_rel=mod_rel, timeout=timeout)
            used_direct = True
        else:
            rc_code, out = 1, ""

        # Fallback to Maven Surefire with JaCoCo prepare-agent (handles JUnit 5, custom @RunWith, and pom.xml <argLine>!)
        if not used_direct or "FT1_JACOCO_JSON=" not in out:
            if os.path.exists(exec_abs):
                os.remove(exec_abs)
            mvn_exec = os.path.join("/workspace", mod_rel, exec_rel) if mod_rel not in ("", ".") else f"/workspace/{exec_rel}"
            agent_mvn = f"-javaagent:{JACOCO_AGENT}=destfile={mvn_exec},output=file"
            mvn_skips_no_jac = rc.MVN_SKIPS.replace("-Djacoco.skip", "")
            bash_cmd = (
                f"rm -f {exec_rel} && "
                f"mvn org.jacoco:jacoco-maven-plugin:0.8.10:prepare-agent -Djacoco.destFile='{mvn_exec}' "
                f"surefire:test -Dtest='{test_fqcn}#{test_method}' -DfailIfNoTests=false "
                f"-DargLine=\"$argLine {agent_mvn}\" {mvn_skips_no_jac} && "
                f"javac -cp '{dump_cp}' target/test-classes/FT1JacocoDump.java && "
                f"java -cp '{dump_cp}' FT1JacocoDump {exec_rel} target/classes {filter_args}"
            )
            rc_code, out, _ = rc.run_docker_cmd(proj_dir, jdk, bash_cmd, workdir_rel=mod_rel, timeout=timeout)

        elapsed = round(time.time() - t0, 3)
        for ln in out.splitlines():
            if ln.startswith("FT1_JACOCO_JSON="):
                raw_j = ln.split("FT1_JACOCO_JSON=", 1)[1].strip()
                parsed = json.loads(raw_j)
                # Merge inner classes into outer FQCN if present, keeping exact class:line keys
                cleaned = {k: sorted(set(int(x) for x in v)) for k, v in parsed.items() if v}
                return True, cleaned, elapsed, ""
        return False, {}, elapsed, out[-400:]
    finally:
        for fp in (exec_abs, dump_java, dump_cls, runner_java, runner_cls):
            if os.path.exists(fp):
                try:
                    os.remove(fp)
                except Exception:
                    pass


def cov_map_to_set(cov_map: Dict[str, List[int]]) -> Set[str]:
    out: Set[str] = set()
    for f, lines in (cov_map or {}).items():
        for ln in lines:
            out.add(f"{f}:{int(ln)}")
    return out


def build_cov_witness_slices(
    lang: str,
    sample: Dict[str, Any],
    lost_role_lines: List[Tuple[str, str]],
    max_items: int = 6,
) -> List[Dict[str, Any]]:
    """Build +-3 line focal source slices for lost covered lines."""
    out: List[Dict[str, Any]] = []
    fmap = sample.get("fqcn_to_path") or {}
    pdir = sample.get("proj_dir", "")
    # Deduplicate nearby lines within +-2 in same file so witnesses cover diverse locations
    chosen: List[Tuple[str, str, int]] = []
    for role, item in lost_role_lines:
        if ":" not in item:
            continue
        cls_or_file, ln_s = item.rsplit(":", 1)
        ln_i = int(ln_s)
        if any(r == role and f == cls_or_file and abs(l - ln_i) <= 3 for r, f, l in chosen):
            continue
        chosen.append((role, cls_or_file, ln_i))
        if len(chosen) >= max_items:
            break

    for role, cls_or_file, ln_i in chosen:
        if lang == "java":
            outer = cls_or_file.split("$")[0]
            fpath = fmap.get(cls_or_file) or fmap.get(outer)
            sl = rc.get_source_slice(fpath, ln_i, radius=3)
        else:
            fpath = os.path.join(pdir, cls_or_file)
            sl = rc.get_source_slice(fpath, ln_i, radius=3)
        out.append({
            "role": role,
            "location": f"{cls_or_file}:L{ln_i}",
            "file_or_class": cls_or_file,
            "line": ln_i,
            "source_slice": sl,
        })
    return out


def compute_baseline_cov(sample: Dict[str, Any]) -> Dict[str, Any]:
    lang = sample.get("language", "java")
    t0 = time.time()
    if lang == "java":
        pdir = sample["proj_dir"]
        mod_rel = sample["module"]
        jdk = sample["jdk"]
        uses_j5 = sample["uses_junit5"]
        # Use focal classes + exact classes present in m_det
        f_set = list(sample.get("focal_classes") or [])
        for m in (sample.get("m_det") or []):
            mc = m.get("mutatedClass")
            if mc and mc not in f_set:
                f_set.append(mc)
        ok_t, cov_t, sec_t, diag_t = run_java_jacoco_single_test(
            pdir, mod_rel, jdk, uses_j5, sample["victim_fqcn"], sample["victim_method"], f_set
        )
        cov_p: Dict[str, List[int]] = {}
        ok_p = True
        sec_p = 0.0
        if sample["test_type"] == "OD" and sample.get("polluter_fqcn") and sample.get("polluter_method"):
            pf_set = list(sample.get("polluter_focal_classes") or f_set)
            for m in (sample.get("polluter_m_det") or []):
                mc = m.get("mutatedClass")
                if mc and mc not in pf_set:
                    pf_set.append(mc)
            ok_p, cov_p, sec_p, _ = run_java_jacoco_single_test(
                pdir, mod_rel, jdk, uses_j5, sample["polluter_fqcn"], sample["polluter_method"], pf_set
            )
        return {
            "sample_id": sample["sample_id"],
            "language": "java",
            "ok_target": ok_t,
            "target_cov": cov_t,
            "target_cov_count": sum(len(v) for v in cov_t.values()),
            "target_cov_sec": sec_t,
            "ok_partner": ok_p,
            "partner_cov": cov_p,
            "partner_cov_count": sum(len(v) for v in cov_p.values()),
            "partner_cov_sec": sec_p,
            "diag": diag_t,
            "wall_sec": round(time.time() - t0, 3),
        }
    else:
        pdir = sample["proj_dir"]
        od_sub = sample.get("od_subtype") or "OD-victim"
        # Clean baseline coverage for target (for OD-brittle, e_0 runs setter + target!)
        t1 = time.time()
        cov_t = rpc.trace_covered_focal_lines(pdir, od_sub, sample["test_full_name"], sample.get("polluter_full_name") or "")
        sec_t = round(time.time() - t1, 3)
        cov_p: Dict[str, List[int]] = {}
        sec_p = 0.0
        if sample.get("polluter_full_name"):
            t2 = time.time()
            cov_p = rpc.trace_covered_focal_lines(pdir, "OD-victim", sample["polluter_full_name"], "")
            sec_p = round(time.time() - t2, 3)
        return {
            "sample_id": sample["sample_id"],
            "language": "python",
            "ok_target": bool(cov_t),
            "target_cov": cov_t,
            "target_cov_count": sum(len(v) for v in cov_t.values()),
            "target_cov_sec": sec_t,
            "ok_partner": bool(cov_p) if sample.get("polluter_full_name") else True,
            "partner_cov": cov_p,
            "partner_cov_count": sum(len(v) for v in cov_p.values()),
            "partner_cov_sec": sec_p,
            "diag": "",
            "wall_sec": round(time.time() - t0, 3),
        }


def eval_candidate_cov(sample: Dict[str, Any], candidate: Dict[str, Any], baseline_cov: Dict[str, Any], skip_wplus: bool = False) -> Dict[str, Any]:
    """
    Apply candidate patch, compile, optionally verify W+, then run ONLY fast focal coverage
    probing (JaCoCo on Java, coverage.py on Python) and compare against `baseline_cov`.
    Restores repository files cleanly in `finally:`.
    """
    lang = sample.get("language", "java")
    t0 = time.time()
    base_t_set = cov_map_to_set(baseline_cov.get("target_cov") or {})
    base_p_set = cov_map_to_set(baseline_cov.get("partner_cov") or {})

    if lang == "java":
        pdir = sample["proj_dir"]
        mod_rel = sample["module"]
        jdk = sample["jdk"]
        uses_j5 = sample["uses_junit5"]
        ttype = sample["test_type"]
        mod_dir = pdir if mod_rel in ("", ".") else os.path.join(pdir, mod_rel)
        vic_file = sample["victim_file_path"]
        pol_file = sample.get("polluter_file_path") or vic_file
        same_file = (os.path.abspath(vic_file) == os.path.abspath(pol_file))

        orig_vic_src = re.sub(r"/\*FT1_ISOLATED\s*(.*?)\s*\*/", r"\1", open(vic_file, encoding="utf-8", errors="replace").read())
        orig_pol_src = orig_vic_src if same_file else re.sub(r"/\*FT1_ISOLATED\s*(.*?)\s*\*/", r"\1", open(pol_file, encoding="utf-8", errors="replace").read())
        vbak = rc.backup_class_files(mod_dir, sample["victim_fqcn"])
        pbak = rc.backup_class_files(mod_dir, sample["polluter_fqcn"]) if (sample.get("polluter_fqcn") and sample["polluter_fqcn"] != sample["victim_fqcn"]) else {}

        try:
            new_vic_src = orig_vic_src
            new_pol_src = orig_pol_src
            if ttype == "ID":
                tcode = candidate.get("test_code") or candidate.get("victim_code") or ""
                if not tcode:
                    return {"compiled": False, "cov_ran": False, "cov_safe": False, "error": "empty_code"}
                new_vic_src = rc.replace_java_method(orig_vic_src, sample["victim_method"], tcode)
                new_vic_src = rc.inject_java_imports(new_vic_src, candidate.get("imports") or [])
                open(vic_file, "w", encoding="utf-8").write(new_vic_src)
            else:
                vcode = candidate.get("victim_code") or candidate.get("test_code") or ""
                pcode = candidate.get("polluter_code") or ""
                p_changed = is_code_semantically_changed("java", sample.get("orig_polluter_code", ""), pcode)
                if same_file:
                    ns = orig_vic_src
                    if vcode:
                        ns = rc.replace_java_method(ns, sample["victim_method"], vcode)
                    if p_changed and sample["polluter_method"] != sample["victim_method"]:
                        ns = rc.replace_java_method(ns, sample["polluter_method"], pcode)
                    ns = rc.inject_java_imports(ns, candidate.get("imports") or [])
                    new_vic_src = new_pol_src = ns
                    open(vic_file, "w", encoding="utf-8").write(ns)
                else:
                    if vcode:
                        new_vic_src = rc.replace_java_method(orig_vic_src, sample["victim_method"], vcode)
                        new_vic_src = rc.inject_java_imports(new_vic_src, candidate.get("imports") or [])
                        open(vic_file, "w", encoding="utf-8").write(new_vic_src)
                    if p_changed:
                        new_pol_src = rc.replace_java_method(orig_pol_src, sample["polluter_method"], pcode)
                        open(pol_file, "w", encoding="utf-8").write(new_pol_src)

            tcode_used = candidate.get("test_code") or candidate.get("victim_code") or sample.get("orig_test_code", "")
            pcode_used = candidate.get("polluter_code") or sample.get("orig_polluter_code", "")
            scope_info = analyze_affected_test_scope(
                language="java",
                test_type=ttype,
                orig_vic_file_src=orig_vic_src,
                new_vic_file_src=new_vic_src,
                orig_pol_file_src=orig_pol_src,
                new_pol_file_src=new_pol_src,
                same_file=same_file,
                orig_target_method_src=sample.get("orig_test_code", ""),
                new_target_method_src=tcode_used,
                orig_partner_method_src=sample.get("orig_polluter_code", ""),
                new_partner_method_src=pcode_used,
            )
            affects_shared = bool(scope_info.get("affects_partner_or_shared"))

            # Compile candidate test (backup_class_files already invalidated stale .class files)
            pl = f"-pl {mod_rel}" if mod_rel not in ("", ".") else ""
            c_rc, c_out, c_sec = rc.docker_mvn(pdir, jdk, f"test-compile {pl}", timeout=120)
            if c_rc != 0:
                return {"compiled": False, "cov_ran": False, "cov_safe": False, "compile_sec": round(c_sec, 3), "compile_error": c_out[-500:]}

            # Run JaCoCo on target (and partner if shared-scope OD)
            f_filter = sorted({x.split(":")[0] for x in base_t_set})
            ok_t, cov_t, sec_t, diag_t = run_java_jacoco_single_test(
                pdir, mod_rel, jdk, uses_j5, sample["victim_fqcn"], sample["victim_method"], f_filter
            )
            if not ok_t:
                return {"compiled": True, "cov_ran": False, "cov_safe": False, "target_cov_sec": sec_t, "error": f"jacoco_target_fail: {diag_t[:200]}"}

            cand_t_set = cov_map_to_set(cov_t)
            lost_t = sorted(base_t_set - cand_t_set)
            lost_p: List[str] = []
            sec_p = 0.0
            if ttype == "OD" and affects_shared and base_p_set:
                pf_filter = sorted({x.split(":")[0] for x in base_p_set})
                ok_p, cov_p, sec_p, diag_p = run_java_jacoco_single_test(
                    pdir, mod_rel, jdk, uses_j5, sample["polluter_fqcn"], sample["polluter_method"], pf_filter
                )
                if not ok_p:
                    return {"compiled": True, "cov_ran": False, "cov_safe": False, "target_cov_sec": sec_t, "partner_cov_sec": sec_p, "error": f"jacoco_partner_fail: {diag_p[:200]}"}
                cand_p_set = cov_map_to_set(cov_p)
                lost_p = sorted(base_p_set - cand_p_set)

            lost_roles = [("target", x) for x in lost_t] + [("partner", x) for x in lost_p]
            tot_base = len(base_t_set) + (len(base_p_set) if (ttype == "OD" and affects_shared) else 0)
            tot_lost = len(lost_roles)
            cov_ret = (tot_base - tot_lost) / tot_base if tot_base > 0 else 1.0
            witnesses = build_cov_witness_slices("java", sample, lost_roles, max_items=6) if lost_roles else []
            return {
                "compiled": True,
                "cov_ran": True,
                "cov_safe": (tot_lost == 0),
                "cov_eroded": (tot_lost > 0),
                "scope_kind": scope_info.get("scope_kind", "TARGET_ONLY"),
                "affects_partner_or_shared": affects_shared,
                "base_lines_total": tot_base,
                "lost_lines_count": tot_lost,
                "cov_retention": round(cov_ret, 6),
                "lost_target_lines": lost_t[:30],
                "lost_partner_lines": lost_p[:30],
                "cov_witnesses": witnesses,
                "compile_sec": round(c_sec, 3),
                "target_cov_sec": sec_t,
                "partner_cov_sec": sec_p,
                "cov_sec": round(sec_t + sec_p, 3),
                "wall_sec": round(time.time() - t0, 3),
            }
        finally:
            open(vic_file, "w", encoding="utf-8").write(orig_vic_src)
            if not same_file:
                open(pol_file, "w", encoding="utf-8").write(orig_pol_src)
            rc.restore_class_files(mod_dir, sample["victim_fqcn"], vbak)
            if pbak and sample.get("polluter_fqcn"):
                rc.restore_class_files(mod_dir, sample["polluter_fqcn"], pbak)
    else:
        # Python coverage.py probe!
        pdir = sample["proj_dir"]
        vic_file = os.path.join(pdir, sample["victim_file_rel"])
        pol_rel = sample.get("polluter_file_rel") or sample["victim_file_rel"]
        pol_file = os.path.join(pdir, pol_rel)
        same_file = (os.path.abspath(vic_file) == os.path.abspath(pol_file))
        orig_vic_src = open(vic_file, encoding="utf-8", errors="replace").read()
        orig_pol_src = orig_vic_src if same_file else open(pol_file, encoding="utf-8", errors="replace").read()
        try:
            vcode = candidate.get("victim_code") or candidate.get("test_code") or ""
            pcode = candidate.get("polluter_code") or ""
            p_changed = is_code_semantically_changed("python", sample.get("orig_polluter_code", ""), pcode)
            new_vic_src = orig_vic_src
            new_pol_src = orig_pol_src
            if same_file:
                ns = orig_vic_src
                if vcode:
                    ns = rpc.replace_py_func(ns, sample.get("victim_class"), sample["victim_method"], vcode)
                if p_changed and sample.get("polluter_method"):
                    ns = rpc.replace_py_func(ns, sample.get("polluter_class"), sample["polluter_method"], pcode)
                ns = rpc.inject_py_imports(ns, candidate.get("imports") or [])
                new_vic_src = new_pol_src = ns
                open(vic_file, "w", encoding="utf-8").write(ns)
            else:
                if vcode:
                    new_vic_src = rpc.replace_py_func(orig_vic_src, sample.get("victim_class"), sample["victim_method"], vcode)
                    new_vic_src = rpc.inject_py_imports(new_vic_src, candidate.get("imports") or [])
                    open(vic_file, "w", encoding="utf-8").write(new_vic_src)
                if p_changed and sample.get("polluter_method"):
                    new_pol_src = rpc.replace_py_func(orig_pol_src, sample.get("polluter_class"), sample["polluter_method"], pcode)
                    open(pol_file, "w", encoding="utf-8").write(new_pol_src)

            scope_info = analyze_affected_test_scope(
                language="python",
                test_type="OD",
                orig_vic_file_src=orig_vic_src,
                new_vic_file_src=new_vic_src,
                orig_pol_file_src=orig_pol_src,
                new_pol_file_src=new_pol_src,
                same_file=same_file,
                orig_target_method_src=sample.get("orig_test_code", ""),
                new_target_method_src=vcode or sample.get("orig_test_code", ""),
                orig_partner_method_src=sample.get("orig_polluter_code", ""),
                new_partner_method_src=pcode or sample.get("orig_polluter_code", ""),
            )
            affects_shared = bool(scope_info.get("affects_partner_or_shared"))

            # Run standalone coverage.py under e_iso (target alone!)
            t1 = time.time()
            cov_t = rpc.trace_covered_focal_lines(pdir, "OD-victim", sample["test_full_name"], "")
            sec_t = round(time.time() - t1, 3)
            if not cov_t:
                return {"compiled": True, "cov_ran": False, "cov_safe": False, "target_cov_sec": sec_t, "error": "py_cov_empty"}
            cand_t_set = cov_map_to_set(cov_t)
            lost_t = sorted(base_t_set - cand_t_set)
            lost_p: List[str] = []
            sec_p = 0.0
            if affects_shared and base_p_set and sample.get("polluter_full_name"):
                t2 = time.time()
                cov_p = rpc.trace_covered_focal_lines(pdir, "OD-victim", sample["polluter_full_name"], "")
                sec_p = round(time.time() - t2, 3)
                if not cov_p:
                    return {"compiled": True, "cov_ran": False, "cov_safe": False, "target_cov_sec": sec_t, "partner_cov_sec": sec_p, "error": "py_partner_cov_empty"}
                cand_p_set = cov_map_to_set(cov_p)
                lost_p = sorted(base_p_set - cand_p_set)

            lost_roles = [("target", x) for x in lost_t] + [("partner", x) for x in lost_p]
            tot_base = len(base_t_set) + (len(base_p_set) if affects_shared else 0)
            tot_lost = len(lost_roles)
            cov_ret = (tot_base - tot_lost) / tot_base if tot_base > 0 else 1.0
            witnesses = build_cov_witness_slices("python", sample, lost_roles, max_items=6) if lost_roles else []
            return {
                "compiled": True,
                "cov_ran": True,
                "cov_safe": (tot_lost == 0),
                "cov_eroded": (tot_lost > 0),
                "scope_kind": scope_info.get("scope_kind", "TARGET_ONLY"),
                "affects_partner_or_shared": affects_shared,
                "base_lines_total": tot_base,
                "lost_lines_count": tot_lost,
                "cov_retention": round(cov_ret, 6),
                "lost_target_lines": lost_t[:30],
                "lost_partner_lines": lost_p[:30],
                "cov_witnesses": witnesses,
                "compile_sec": 0.0,
                "target_cov_sec": sec_t,
                "partner_cov_sec": sec_p,
                "cov_sec": round(sec_t + sec_p, 3),
                "wall_sec": round(time.time() - t0, 3),
            }
        finally:
            open(vic_file, "w", encoding="utf-8").write(orig_vic_src)
            if not same_file:
                open(pol_file, "w", encoding="utf-8").write(orig_pol_src)


def run_batch_offline(spec_path: str, state_path: str, workers: int = 2) -> None:
    """
    Execute baseline coverage freeze, PR-43 evaluation, and Eroded-400 evaluation entirely
    inside ONE persistent remote Python process (zero SSH MaxStartups overhead!), with
    per-project locking and incremental JSON checkpointing.
    """
    import threading
    from collections import defaultdict
    from concurrent.futures import ThreadPoolExecutor, as_completed

    spec = json.load(open(spec_path, encoding="utf-8"))
    state: Dict[str, Any] = {"baselines": {}, "pr_evals": {}, "eroded_evals": {}}
    if os.path.exists(state_path):
        try:
            state = json.load(open(state_path, encoding="utf-8"))
        except Exception:
            pass
    # Seed any already-known baselines from spec
    for sid, b in (spec.get("seed_baselines") or {}).items():
        if b.get("ok_target") and sid not in state["baselines"]:
            state["baselines"][sid] = b

    lock = threading.Lock()
    proj_locks: Dict[str, threading.Lock] = defaultdict(threading.Lock)

    def _save() -> None:
        tmp = state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
        os.replace(tmp, state_path)

    samples_by_id: Dict[str, Dict[str, Any]] = {s["sample_id"]: s for s in spec["samples"]}

    # 1. Baseline coverages
    todo_b = [s for s in spec["samples"] if s["sample_id"] not in state["baselines"] or not state["baselines"][s["sample_id"]].get("ok_target")]
    print(f"[BATCH COV] Phase 1 Baselines: {len(spec['samples']) - len(todo_b)}/{len(spec['samples'])} cached; running {len(todo_b)}...", flush=True)

    def _do_base(s: Dict[str, Any]) -> None:
        with proj_locks[s["project_name"]]:
            r = compute_baseline_cov(s)
        with lock:
            state["baselines"][s["sample_id"]] = r
            if len(state["baselines"]) % 10 == 0:
                _save()
        print(f"  [BASE {s['language'][:2]}] {s['sample_id']}: ok={r['ok_target']} lines={r['target_cov_count']} ({r['target_cov_sec']}s)", flush=True)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(_do_base, s) for s in todo_b]
        for f in as_completed(futs):
            try:
                f.result()
            except Exception as e:
                print(f"  [ERR BASE]: {e}", flush=True)
    _save()

    # 2. PR-43 evaluations
    todo_pr = [
        j for j in spec["pr_jobs"]
        if j["sample_id"] not in state["pr_evals"]
        and state["baselines"].get(j["sample_id"], {}).get("ok_target")
    ]
    print(f"[BATCH COV] Phase 2 PR-43: {len(spec['pr_jobs']) - len(todo_pr)}/{len(spec['pr_jobs'])} cached; running {len(todo_pr)}...", flush=True)

    def _do_pr(j: Dict[str, Any]) -> None:
        s = samples_by_id[j["sample_id"]]
        with proj_locks[s["project_name"]]:
            r = eval_candidate_cov(s, j["candidate"], state["baselines"][j["sample_id"]])
        with lock:
            state["pr_evals"][j["sample_id"]] = r
            if len(state["pr_evals"]) % 8 == 0:
                _save()
        print(f"  [PR] {j['sample_id']}: ran={r.get('cov_ran')} safe={r.get('cov_safe')} lost={r.get('lost_lines_count')} ({r.get('cov_sec',0)}s)", flush=True)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(_do_pr, j) for j in todo_pr]
        for f in as_completed(futs):
            try:
                f.result()
            except Exception as e:
                print(f"  [ERR PR]: {e}", flush=True)
    _save()

    # 3. Eroded-400 evaluations (re-run any previously failed cov_ran=False now that imports are supplied!)
    todo_er = [
        j for j in spec["eroded_jobs"]
        if (j["uk"] not in state["eroded_evals"] or not state["eroded_evals"][j["uk"]].get("cov_ran"))
        and state["baselines"].get(j["sample_id"], {}).get("ok_target")
    ]
    print(f"[BATCH COV] Phase 3 Eroded-400: {len(spec['eroded_jobs']) - len(todo_er)}/{len(spec['eroded_jobs'])} cached; running {len(todo_er)}...", flush=True)

    def _do_er(j: Dict[str, Any]) -> None:
        s = samples_by_id[j["sample_id"]]
        with proj_locks[s["project_name"]]:
            r = eval_candidate_cov(s, j["candidate"], state["baselines"][j["sample_id"]])
        with lock:
            state["eroded_evals"][j["uk"]] = r
            n_d = len(state["eroded_evals"])
            if n_d % 15 == 0:
                _save()
                print(f"  [ERODED] progress {n_d}/{len(spec['eroded_jobs'])}", flush=True)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(_do_er, j) for j in todo_er]
        for f in as_completed(futs):
            try:
                f.result()
            except Exception as e:
                print(f"  [ERR ER]: {e}", flush=True)
    _save()
    print(
        f"[BATCH COV DONE] baselines_ok={sum(1 for v in state['baselines'].values() if v.get('ok_target'))}/{len(spec['samples'])} "
        f"pr={len(state['pr_evals'])} eroded={len(state['eroded_evals'])}",
        flush=True,
    )


def main() -> None:
    mode = sys.argv[1]
    if mode == "batch_offline":
        spec_p = sys.argv[2]
        state_p = sys.argv[3]
        workers = int(sys.argv[4]) if len(sys.argv) > 4 else 2
        run_batch_offline(spec_p, state_p, workers=workers)
        return
    payload = json.loads(sys.stdin.read())
    if mode == "baseline_cov":
        res = compute_baseline_cov(payload["sample"])
    elif mode == "eval_cand_cov":
        res = eval_candidate_cov(payload["sample"], payload["candidate"], payload["baseline_cov"])
    else:
        raise ValueError(f"Unknown mode: {mode}")
    print("FT1_V3_COV_RESULT=" + json.dumps(res, ensure_ascii=False))


if __name__ == "__main__":
    main()
