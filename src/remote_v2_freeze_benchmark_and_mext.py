"""
Remote Phase A (FT-1 v2.0.0-frozen):
  1) Re-verify `M_det` & `polluter_m_det` (`3/3` clean-`e_0` node/phase attributed) across all 208 samples,
     recording exact attrition (`208 -> N_valid`) without silent replacement.
  2) Freeze disjoint Held-Out External Fault Suites (`M_ext_op_value_flow`, `M_ext_op_call`, `M_ext_operator`,
     `M_ext_instance`) BEFORE any candidate patch is evaluated on `M_ext`.
  3) Build authoritative `PR` provenance manifest (`Accepted` vs `Opened` vs `DeveloperWontFix`, `TEST_ONLY_PR`
     vs `TEST_AND_PROD_PR`, `patch_sha256`, `3/3` repeat mutation verification).
  4) Build authoritative `B1` provenance manifest (`FlakyDoctor` official CSV `:P` vs unmatched archive;
     `iPFlakies` official `Patches.csv` target-partner verification; strict verbatim vs import-salvaged).

Strictly respects `<= 3` concurrent Docker containers (`<= 7.5 vCPU`, `<= 12 GB RAM`) and per-repo locks.
"""
import base64
import csv
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Set, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import remote_core as rc
import remote_python_core as rpc
from v2_guards_and_attribution import (
    HARNESS_VERSION,
    analyze_affected_test_scope,
    compute_role_tagged_retention,
    compute_unified_diff,
    evaluate_java_repeated_pollution_report,
    evaluate_py_repeated_pollution_report,
    extract_declared_java_method_name,
    extract_pit_xml_indexes_and_blocks,
    java_pit_mutant_exact_id,
    match_java_pit_killing_test,
    resolve_pit_mutants_by_exact_id,
)
from v2_pytest_attribution_runner import (
    PY_V2_ATTRIBUTED_MUT_RUNNER,
    _src_sha256,
    generate_py_m_ext_operator_mutants,
)

FULL_DIR = "/root/sqj_work/FT1/full_runs"
CKPT_JSON = os.path.join(FULL_DIR, "v2_freeze_checkpoint.json")
OUT_EXEC_JSON = os.path.join(FULL_DIR, "v2_frozen_benchmark_exec.json")
OUT_LIGHT_JSON = os.path.join(FULL_DIR, "v2_frozen_benchmark_light.json")
OUT_PR_MANIFEST = os.path.join(FULL_DIR, "pr_reference_manifest.json")
OUT_B1_MANIFEST = os.path.join(FULL_DIR, "b1_provenance_manifest.json")
PR_DIFF_CACHE_DIR = os.path.join(FULL_DIR, "pr_diff_cache")

DOCKER_SEM = threading.Semaphore(3)
PROJ_LOCKS: Dict[str, threading.Lock] = defaultdict(threading.Lock)
SAVE_LOCK = threading.Lock()

PIT_DEFAULT_MUTATOR_CLASSES = {
    "ConditionalsBoundaryMutator",
    "IncrementsMutator",
    "InvertNegsMutator",
    "MathMutator",
    "NegateConditionalsMutator",
    "VoidMethodCallMutator",
    "EmptyObjectReturnValsMutator",
    "BooleanFalseReturnValsMutator",
    "BooleanTrueReturnValsMutator",
    "NullReturnValsMutator",
    "PrimitiveReturnsMutator",
}

PIT_CALL_CRASH_PRONE_CLASSES = {
    "NonVoidMethodCallMutator",
    "ConstructorCallMutator",
}


def log(msg: str) -> None:
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ============================================================================
# Authoritative Upstream Index Loaders (FlakyDoctor CSV, IDoFT, iPFlakies)
# ============================================================================

def build_upstream_indices() -> Dict[str, Any]:
    fd = rc.FD_ROOT
    ipf = rpc.IPFLAKIES_ROOT

    fd_csv: Dict[str, Dict[Tuple[str, str], Dict[str, Any]]] = {"gpt": {}, "magicoder": {}}
    for key, rel in (("gpt", "results/gpt_fixes_tests.csv"), ("magicoder", "results/magicoder_fixes_tests.csv")):
        p = os.path.join(fd, rel)
        if not os.path.exists(p):
            continue
        for row_idx, r in enumerate(csv.reader(open(p, encoding="utf-8", errors="replace"))):
            if len(r) < 5 or r[0].startswith("Project URL"):
                continue
            sha = r[1].strip().lower()
            tname = r[4].strip()
            status_cols = [c.strip() for c in r[5:] if ":" in c and not c.strip().endswith(".patch")]
            has_p = any(c.endswith(":P") for c in status_cols)
            patches = [c.strip() for c in r[5:] if c.strip().endswith(".patch")]
            p_rel = patches[0] if patches else ""
            p_abs = os.path.join(fd, p_rel) if p_rel else ""
            fd_csv[key][(sha, tname)] = {
                "csv_file": rel,
                "csv_row": row_idx,
                "status_trace": status_cols,
                "has_official_P": has_p,
                "csv_patch_rel": p_rel,
                "csv_patch_abs": p_abs if (p_abs and os.path.exists(p_abs)) else "",
            }

    ipf_csv: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    p_csv = os.path.join(ipf, "summary/Patches.csv")
    if os.path.exists(p_csv):
        for row_idx, row in enumerate(csv.DictReader(open(p_csv, encoding="utf-8", errors="replace"))):
            pname = row["Project_Name"].strip()
            sha = row["Project_Hash"].strip().lower()
            tid = row["Test_id"].strip()
            p_rel = row.get("Path", "").strip()
            p_abs = os.path.join(ipf, "ipflakies_result", p_rel) if p_rel else ""
            ipf_csv.setdefault((pname, sha, tid), {
                "csv_file": "summary/Patches.csv",
                "csv_row": row_idx,
                "od_type": row["OD_Type"].strip(),
                "partner": row["Polluter_or_Setter"].strip(),
                "csv_patch_rel": p_rel,
                "csv_patch_abs": p_abs if (p_abs and os.path.exists(p_abs)) else "",
            })

    return {"fd_csv": fd_csv, "ipf_csv": ipf_csv}


# ============================================================================
# Java PIT Helpers (`ALL` mutators + `<killingTest>` attribution + Heldout W+)
# ============================================================================

def run_attributed_java_pit(
    proj_dir: str,
    mod_rel: str,
    jdk: str,
    test_file_path: str,
    test_fqcn: str,
    test_method: str,
    focal_classes: List[str],
    pit_pair: Tuple[str, Optional[str]],
    fqcn_to_path: Optional[Dict[str, str]] = None,
    mutators_arg: str = "DEFAULTS",
    timeout: int = 135,
) -> Tuple[bool, List[Dict[str, Any]], float, str]:
    mod_dir = proj_dir if mod_rel in ("", ".") else os.path.join(proj_dir, mod_rel)
    pom_path = os.path.join(mod_dir, "pom.xml")
    orig_test_src = open(test_file_path, encoding="utf-8", errors="replace").read()
    orig_pom_src = open(pom_path, encoding="utf-8", errors="replace").read()
    cls_backups = rc.backup_class_files(mod_dir, test_fqcn)

    pit_ver, j5_ver = pit_pair
    pl = f"-pl {mod_rel}" if mod_rel not in ("", ".") else ""
    target_cls_arg = ",".join(focal_classes)
    mxml_path = os.path.join(mod_dir, "target/pit-reports/mutations.xml")
    mut_flag = f"-Dmutators={mutators_arg} " if mutators_arg != "DEFAULTS" else ""

    try:
        isolated_src = rc.isolate_single_test_method(orig_test_src, test_method)
        open(test_file_path, "w", encoding="utf-8").write(isolated_src)
        if os.path.exists(mxml_path):
            os.remove(mxml_path)
        open(pom_path, "w", encoding="utf-8").write(rc.inject_pit_plugin_xml(orig_pom_src, pit_ver, j5_ver))
        with DOCKER_SEM:
            rc_c, out, dt = rc.docker_mvn(
                proj_dir,
                jdk,
                f"test-compile org.pitest:pitest-maven:{pit_ver}:mutationCoverage {pl} "
                f"-DtargetClasses={target_cls_arg} -DtargetTests={test_fqcn} "
                f"{mut_flag}-DtimeoutConst=3000 -DoutputFormats=XML -DtimestampedReports=false "
                f"-Dthreads=3 -DfailWhenNoMutations=false",
                timeout=timeout,
            )
        if not os.path.exists(mxml_path) or os.path.getsize(mxml_path) == 0:
            return False, [], round(dt, 2), out[-800:]
        try:
            root = ET.parse(mxml_path).getroot()
        except ET.ParseError:
            return False, [], round(dt, 2), out[-800:]
        muts: List[Dict[str, Any]] = []
        for m_el in root.findall("mutation"):
            st = m_el.attrib.get("status", "")
            kt = (m_el.findtext("killingTest") or "").strip()
            m_cls = m_el.findtext("mutatedClass", "")
            outer_cls = m_cls.split("$")[0]
            line_no = int(m_el.findtext("lineNumber", "0") or "0")
            mut_full = m_el.findtext("mutator", "")
            mut_short = mut_full.split(".")[-1]
            # Exact node attribution (`METHOD_EXACT` or `CLASS_ISOLATED_EXACT`); zero substring matching
            attr = match_java_pit_killing_test(st, kt, test_fqcn, test_method)
            idx_str, blk_str = extract_pit_xml_indexes_and_blocks(m_el)
            m_obj: Dict[str, Any] = {
                "status": st,
                "status_norm": attr["status_norm"],
                "attributed_kill": attr["attributed_kill"],
                "is_unknown": attr["is_unknown"],
                "attribution_granularity": attr["attribution_granularity"],
                "killingTest": kt,
                "mutatedClass": m_cls,
                "mutatedMethod": m_el.findtext("mutatedMethod", ""),
                "methodDescription": m_el.findtext("methodDescription", ""),
                "lineNumber": line_no,
                "mutator": mut_full,
                "mutator_short": mut_short,
                "index": idx_str,
                "block": blk_str,
                "description": m_el.findtext("description", ""),
            }
            m_obj["id"] = rc.mutant_canonical_id(m_obj)
            m_obj["exact_id"] = java_pit_mutant_exact_id(m_obj)
            if fqcn_to_path and outer_cls in fqcn_to_path:
                m_obj["source_slice"] = rc.get_source_slice(fqcn_to_path[outer_cls], line_no)
            muts.append(m_obj)
        return True, muts, round(dt, 2), out[-400:]
    finally:
        open(test_file_path, "w", encoding="utf-8").write(orig_test_src)
        open(pom_path, "w", encoding="utf-8").write(orig_pom_src)
        rc.restore_class_files(mod_dir, test_fqcn, cls_backups)


JUNIT4_HELDOUT_REPEATED_RUNNER_SRC = r"""
import java.util.*;
import org.junit.After;
import org.junit.runner.*;
import org.junit.runner.notification.*;
import org.junit.runners.BlockJUnit4ClassRunner;
import org.junit.runners.Suite;
import org.junit.runners.model.FrameworkMethod;
import org.junit.runners.model.Statement;

public class FT1HeldoutRepeatedRunner {
    static final int MAX_SLOTS = 16;
    static final int[] CUR_IDX = new int[]{-1};
    static final String[] SETUP_ST = new String[MAX_SLOTS];
    static final String[] CALL_ST = new String[MAX_SLOTS];
    static final String[] TEARDOWN_ST = new String[MAX_SLOTS];
    static final String[] CALL_EXC = new String[MAX_SLOTS];
    static final String[] SETUP_EXC = new String[MAX_SLOTS];
    static final String[] TEARDOWN_EXC = new String[MAX_SLOTS];

    static {
        for (int i = 0; i < MAX_SLOTS; i++) {
            SETUP_ST[i] = "PENDING";
            CALL_ST[i] = "PENDING";
            TEARDOWN_ST[i] = "PENDING";
            CALL_EXC[i] = "NONE";
            SETUP_EXC[i] = "NONE";
            TEARDOWN_EXC[i] = "NONE";
        }
    }

    static class SingleClassRepeatedRunner extends BlockJUnit4ClassRunner {
        private static final ThreadLocal<List<String>> INIT_METHODS = new ThreadLocal<List<String>>();
        private final List<String> orderedMethods;
        static SingleClassRepeatedRunner create(Class<?> klass, List<String> orderedMethods) throws Exception {
            INIT_METHODS.set(orderedMethods);
            try {
                return new SingleClassRepeatedRunner(klass, orderedMethods);
            } finally {
                INIT_METHODS.remove();
            }
        }
        SingleClassRepeatedRunner(Class<?> klass, List<String> orderedMethods) throws Exception {
            super(klass);
            this.orderedMethods = orderedMethods;
        }
        @Override
        protected List<FrameworkMethod> computeTestMethods() {
            List<String> names = (this.orderedMethods != null) ? this.orderedMethods : INIT_METHODS.get();
            List<FrameworkMethod> all = super.computeTestMethods();
            if (names == null) return all;
            Map<String, FrameworkMethod> byName = new HashMap<String, FrameworkMethod>();
            for (FrameworkMethod fm : all) {
                byName.put(fm.getName(), fm);
            }
            List<FrameworkMethod> seq = new ArrayList<FrameworkMethod>();
            for (String mname : names) {
                FrameworkMethod fm = byName.get(mname);
                if (fm != null) seq.add(fm);
            }
            return seq;
        }
        @Override
        protected void validateTestMethods(List<Throwable> errors) {
            // Allow custom repeated method lists without failing on empty default scan
        }
        @Override
        protected Statement possiblyExpectingExceptions(FrameworkMethod method, Object test, Statement next) {
            final Statement inner = super.possiblyExpectingExceptions(method, test, next);
            return new Statement() {
                @Override
                public void evaluate() throws Throwable {
                    int idx = CUR_IDX[0];
                    if (idx >= 0 && idx < MAX_SLOTS) {
                        SETUP_ST[idx] = "PASS";
                    }
                    try {
                        inner.evaluate();
                        if (idx >= 0 && idx < MAX_SLOTS) {
                            CALL_ST[idx] = "PASS";
                        }
                    } catch (Throwable t) {
                        if (idx >= 0 && idx < MAX_SLOTS) {
                            CALL_ST[idx] = "FAIL";
                            CALL_EXC[idx] = t.getClass().getName();
                        }
                        throw t;
                    }
                }
            };
        }
        @Override
        protected Statement withBefores(FrameworkMethod method, Object target, Statement statement) {
            final Statement wrapped = super.withBefores(method, target, statement);
            return new Statement() {
                @Override
                public void evaluate() throws Throwable {
                    int idx = CUR_IDX[0];
                    try {
                        wrapped.evaluate();
                    } catch (Throwable t) {
                        if (idx >= 0 && idx < MAX_SLOTS && "PENDING".equals(SETUP_ST[idx])) {
                            SETUP_ST[idx] = "FAIL";
                            SETUP_EXC[idx] = t.getClass().getName();
                            CALL_ST[idx] = "SKIPPED";
                        }
                        throw t;
                    }
                }
            };
        }
        @Override
        protected Statement withAfters(FrameworkMethod method, final Object target, final Statement statement) {
            final List<FrameworkMethod> afters = getTestClass().getAnnotatedMethods(After.class);
            return new Statement() {
                @Override
                public void evaluate() throws Throwable {
                    int idx = CUR_IDX[0];
                    List<Throwable> errors = new ArrayList<Throwable>();
                    try {
                        statement.evaluate();
                    } catch (Throwable e) {
                        errors.add(e);
                    } finally {
                        boolean tdFailed = false;
                        for (FrameworkMethod each : afters) {
                            try {
                                each.invokeExplosively(target);
                            } catch (Throwable e) {
                                tdFailed = true;
                                if (idx >= 0 && idx < MAX_SLOTS) {
                                    TEARDOWN_EXC[idx] = e.getClass().getName();
                                }
                                errors.add(e);
                            }
                        }
                        if (idx >= 0 && idx < MAX_SLOTS) {
                            TEARDOWN_ST[idx] = tdFailed ? "FAIL" : "PASS";
                        }
                    }
                    if (!errors.isEmpty()) {
                        throw errors.get(0);
                    }
                }
            };
        }
    }

    static class OrderedCompositeSuite extends Suite {
        OrderedCompositeSuite(List<Runner> runners) throws Exception {
            super(null, runners);
        }
    }

    private static boolean hasCustomRunWith(Class<?> cls) {
        org.junit.runner.RunWith rw = cls.getAnnotation(org.junit.runner.RunWith.class);
        return rw != null && !BlockJUnit4ClassRunner.class.equals(rw.value());
    }

    public static void main(String[] args) throws Exception {
        // Executes `partner -> partner -> target` inside ONE single JVM lifecycle with
        // explicit per-role phase tracking (`@Before` setup, `@Test` call, `@After` teardown).
        // Custom `@RunWith(...)` classes fall back to same-JVM `Request.method(...)` with
        // `phaseKnown=false`, which strictly forbids Stress exemption (fail-closed).
        String[] pParts = args[0].split("#", 2);
        String[] vParts = args[1].split("#", 2);
        Class<?> pCls = Class.forName(pParts[0]);
        Class<?> vCls = Class.forName(vParts[0]);
        final List<String> execOrder = new ArrayList<String>();
        final Map<Integer, Integer> failCountByIdx = new HashMap<Integer, Integer>();
        JUnitCore core = new JUnitCore();
        core.addListener(new RunListener() {
            @Override
            public void testStarted(Description d) {
                CUR_IDX[0] += 1;
                execOrder.add(d.getClassName() + "#" + d.getMethodName());
            }
            @Override
            public void testFailure(Failure f) {
                int idx = CUR_IDX[0];
                Integer prev = failCountByIdx.get(idx);
                failCountByIdx.put(idx, (prev == null ? 0 : prev) + 1);
            }
        });
        int totalRun = 0, totalFail = 0, totalIgn = 0;
        List<Failure> allFails = new ArrayList<Failure>();
        String mode;
        boolean phaseKnown;
        if (!hasCustomRunWith(pCls) && !hasCustomRunWith(vCls)) {
            mode = "SINGLE_LIFECYCLE_BLOCK_RUNNER";
            phaseKnown = true;
            Runner rootRunner;
            if (pCls.equals(vCls)) {
                rootRunner = SingleClassRepeatedRunner.create(pCls, Arrays.asList(pParts[1], pParts[1], vParts[1]));
            } else {
                List<Runner> children = new ArrayList<Runner>();
                children.add(SingleClassRepeatedRunner.create(pCls, Arrays.asList(pParts[1], pParts[1])));
                children.add(SingleClassRepeatedRunner.create(vCls, Collections.singletonList(vParts[1])));
                rootRunner = new OrderedCompositeSuite(children);
            }
            Result r = core.run(Request.runner(rootRunner));
            totalRun = r.getRunCount();
            totalFail = r.getFailureCount();
            totalIgn = r.getIgnoreCount();
            allFails.addAll(r.getFailures());
        } else {
            mode = "CUSTOM_RUNWITH_SEQUENTIAL_SAME_JVM";
            phaseKnown = false;
            Result r1 = core.run(Request.method(pCls, pParts[1]));
            Result r2 = core.run(Request.method(pCls, pParts[1]));
            Result r3 = core.run(Request.method(vCls, vParts[1]));
            for (Result rx : new Result[]{r1, r2, r3}) {
                totalRun += rx.getRunCount();
                totalFail += rx.getFailureCount();
                totalIgn += rx.getIgnoreCount();
                allFails.addAll(rx.getFailures());
            }
        }
        String pSpecExact = pParts[0] + "#" + pParts[1];
        String vSpecExact = vParts[0] + "#" + vParts[1];
        boolean orderExact = (execOrder.size() == 3)
                && pSpecExact.equals(execOrder.get(0))
                && pSpecExact.equals(execOrder.get(1))
                && vSpecExact.equals(execOrder.get(2));
        boolean[] outerFail = new boolean[3];
        if (phaseKnown && orderExact) {
            for (int i = 0; i < 3; i++) {
                int fc = failCountByIdx.containsKey(i) ? failCountByIdx.get(i) : 0;
                boolean innerFailed = !"PASS".equals(SETUP_ST[i]) || !"PASS".equals(CALL_ST[i]) || !"PASS".equals(TEARDOWN_ST[i]);
                outerFail[i] = (fc > 0 && !innerFailed) || (fc > 1 && "PASS".equals(SETUP_ST[i]) && "PASS".equals(TEARDOWN_ST[i]));
            }
        }
        boolean p1Ok = orderExact && !failCountByIdx.containsKey(0) && (!phaseKnown || ("PASS".equals(SETUP_ST[0]) && "PASS".equals(CALL_ST[0]) && "PASS".equals(TEARDOWN_ST[0]) && !outerFail[0]));
        boolean p2Ok = orderExact && !failCountByIdx.containsKey(1) && (!phaseKnown || ("PASS".equals(SETUP_ST[1]) && "PASS".equals(CALL_ST[1]) && "PASS".equals(TEARDOWN_ST[1]) && !outerFail[1]));
        boolean tgtOk = orderExact && !failCountByIdx.containsKey(2) && (!phaseKnown || ("PASS".equals(SETUP_ST[2]) && "PASS".equals(CALL_ST[2]) && "PASS".equals(TEARDOWN_ST[2]) && !outerFail[2]));
        boolean strictOk = (orderExact && totalIgn == 0 && totalRun == 3 && totalFail == 0 && p1Ok && p2Ok && tgtOk);
        boolean p2LifecycleOk = (phaseKnown && orderExact && "PASS".equals(SETUP_ST[1]) && "PASS".equals(TEARDOWN_ST[1]) && !outerFail[1] && "FAIL".equals(CALL_ST[1]));
        boolean nonIdempotentPartnerCallOnly = (p2LifecycleOk && p1Ok && tgtOk && totalIgn == 0 && totalRun == 3 && totalFail == 1);
        boolean stressOk = strictOk || nonIdempotentPartnerCallOnly;
        System.out.println("FT1_REP_MODE=" + mode);
        System.out.println("FT1_REP_PHASE_KNOWN=" + phaseKnown);
        System.out.println("FT1_EXEC_ORDER=" + String.join(" -> ", execOrder));
        String[] roleNames = new String[]{"p1", "p2", "target"};
        if (phaseKnown && orderExact) {
            for (int i = 0; i < 3; i++) {
                System.out.println(
                        "FT1_REP_PHASES role=" + roleNames[i]
                        + " setup=" + SETUP_ST[i]
                        + " call=" + CALL_ST[i]
                        + " teardown=" + TEARDOWN_ST[i]
                        + " outer_fail=" + outerFail[i]
                        + " call_exc=" + CALL_EXC[i]
                );
            }
        }
        System.out.println(
                "FT1_REP_VERDICTS p1=" + (p1Ok ? "PASS" : "FAIL")
                + " p2=" + (p2Ok ? "PASS" : "FAIL")
                + " target=" + (tgtOk ? "PASS" : "FAIL")
                + " order_exact=" + orderExact
                + " strict=" + strictOk
                + " stress=" + stressOk
        );
        System.out.println("FT1_HELDOUT_REP RUN=" + totalRun + " FAIL=" + totalFail + " IGNORE=" + totalIgn);
        for (Failure f : allFails) {
            String tr = f.getTrace() != null ? f.getTrace().replace('\n', ' ').replace('\r', ' ') : "";
            if (tr.length() > 350) tr = tr.substring(0, 350);
            System.out.println("FT1_REP_FAIL: " + f.getDescription() + " :: " + f.getException() + " :: " + tr);
        }
        System.exit(stressOk ? 0 : 1);
    }
}
""".strip()


def run_java_repeated_pollution_raw(
    sample: Dict[str, Any],
    allow_upstream_nonidempotent_p2_call_fail: bool = False,
    expected_p2_call_exc: Optional[str] = None,
    ensure_compile: bool = False,
) -> Dict[str, Any]:
    """
    Execute ONLY the single-JVM `partner -> partner -> target` repeated-pollution schedule
    on the CURRENT working tree of a Java OD sample (used for unmodified upstream controls
    and diagnostics). Enforces phase-level evaluation via `evaluate_java_repeated_pollution_report`.
    """
    t0 = time.time()
    proj_dir = sample["proj_dir"]
    mod_rel = sample["module"]
    jdk = sample["jdk"]
    pl = f"-pl {mod_rel}" if mod_rel not in ("", ".") else ""
    v_spec = f"{sample['victim_fqcn']}#{sample['victim_method']}"
    p_spec = f"{sample.get('polluter_fqcn', '')}#{sample.get('polluter_method', '')}"
    has_partner = bool(sample.get("polluter_fqcn") and sample.get("polluter_method") and p_spec != v_spec)
    if not has_partner:
        return {"has_partner": False, "strict_passed": True, "target_stress_passed": True, "is_unknown": False, "sec": 0.0}

    if ensure_compile:
        with DOCKER_SEM:
            rc.docker_mvn(proj_dir, jdk, f"test-compile {pl}", timeout=115)

    if not sample.get("uses_junit5") and rc.ensure_order_runner_ready(proj_dir, mod_rel, jdk):
        mod_dir = proj_dir if mod_rel in ("", ".") else os.path.join(proj_dir, mod_rel)
        tclasses = os.path.join(mod_dir, "target/test-classes")
        os.makedirs(tclasses, exist_ok=True)
        r2_path = os.path.join(tclasses, "FT1HeldoutRepeatedRunner.java")
        try:
            open(r2_path, "w", encoding="utf-8").write(JUNIT4_HELDOUT_REPEATED_RUNNER_SRC)
            bash_cmd = (
                "CP=target/test-classes:target/classes:$(cat target/ft1_cp.txt) && "
                "javac -cp \"$CP\" target/test-classes/FT1HeldoutRepeatedRunner.java && "
                f"java -cp \"$CP\" FT1HeldoutRepeatedRunner '{p_spec}' '{v_spec}' 2>&1"
            )
            with DOCKER_SEM:
                rc_c, out, _ = rc.run_docker_cmd(proj_dir, jdk, bash_cmd, workdir_rel=mod_rel, timeout=110)
            rep = _parse_java_rep_output(
                out, rc_c,
                allow_upstream_nonidempotent_p2_call_fail=allow_upstream_nonidempotent_p2_call_fail,
                expected_p2_call_exc=expected_p2_call_exc,
            )
            rep["has_partner"] = True
            rep["sec"] = round(time.time() - t0, 2)
            return rep
        finally:
            for fn in os.listdir(tclasses):
                if fn.startswith("FT1HeldoutRepeatedRunner"):
                    try:
                        os.remove(os.path.join(tclasses, fn))
                    except OSError:
                        pass
    else:
        with DOCKER_SEM:
            rc_c, out, _ = rc.docker_mvn(
                proj_dir, jdk, f"test {pl} -Dsurefire.runOrder=testorder -Dtest={p_spec},{p_spec},{v_spec}", timeout=95
            )
        ok_o = (rc_c == 0 and "BUILD SUCCESS" in out and not re.search(r"Failures:\s*[1-9]|Errors:\s*[1-9]|Skipped:\s*[1-9]", out))
        rep = evaluate_java_repeated_pollution_report(
            {
                "mode": "SUREFIRE_FALLBACK",
                "exec_order": f"{p_spec} -> {p_spec} -> {v_spec}" if ok_o else "",
                "order_exact": ok_o,
                "phase_level_known": False,
                "total_run": 3 if ok_o else 0,
                "total_fail": 0 if ok_o else 1,
                "total_ign": 0,
                "p1_ok": ok_o,
                "p2_ok": ok_o,
                "target_ok": ok_o,
                "rc_c": 0 if ok_o else 1,
                "fail_lines": [],
            },
            allow_upstream_nonidempotent_p2_call_fail=False,
        )
        rep["has_partner"] = True
        rep["sec"] = round(time.time() - t0, 2)
        return rep


def _parse_java_rep_output(
    out: str,
    rc_c: int,
    allow_upstream_nonidempotent_p2_call_fail: bool = False,
    expected_p2_call_exc: Optional[str] = None,
) -> Dict[str, Any]:
    mode = ""
    exec_order = ""
    phase_known = False
    p1_ok = p2_ok = target_ok = order_exact = False
    total_run = total_fail = total_ign = 0
    phases: Dict[str, Dict[str, Any]] = {}
    fail_lines: List[str] = []
    for ln in out.splitlines():
        if "FT1_REP_MODE=" in ln:
            mode = ln.split("FT1_REP_MODE=", 1)[1].strip()
        elif "FT1_REP_PHASE_KNOWN=" in ln:
            phase_known = (ln.split("FT1_REP_PHASE_KNOWN=", 1)[1].strip().lower() == "true")
        elif "FT1_EXEC_ORDER=" in ln:
            exec_order = ln.split("FT1_EXEC_ORDER=", 1)[1].strip()
        elif "FT1_REP_PHASES " in ln:
            mp = re.search(
                r"role=(p1|p2|target)\s+setup=(\S+)\s+call=(\S+)\s+teardown=(\S+)\s+outer_fail=(true|false)\s+call_exc=(\S+)",
                ln,
            )
            if mp:
                phases[mp.group(1)] = {
                    "setup": mp.group(2),
                    "call": mp.group(3),
                    "teardown": mp.group(4),
                    "outer_fail": (mp.group(5) == "true"),
                    "call_exc": None if mp.group(6) == "NONE" else mp.group(6),
                }
        elif "FT1_REP_VERDICTS " in ln:
            m = re.search(
                r"p1=(PASS|FAIL)\s+p2=(PASS|FAIL)\s+target=(PASS|FAIL)\s+order_exact=(true|false)\s+strict=(true|false)\s+stress=(true|false)",
                ln,
            )
            if m:
                p1_ok = (m.group(1) == "PASS")
                p2_ok = (m.group(2) == "PASS")
                target_ok = (m.group(3) == "PASS")
                order_exact = (m.group(4) == "true")
        elif "FT1_HELDOUT_REP " in ln:
            mr = re.search(r"RUN=(\d+)\s+FAIL=(\d+)\s+IGNORE=(\d+)", ln)
            if mr:
                total_run = int(mr.group(1))
                total_fail = int(mr.group(2))
                total_ign = int(mr.group(3))
        elif "FT1_REP_FAIL:" in ln:
            fail_lines.append(ln.strip())
    raw_rep = {
        "mode": mode or "UNKNOWN",
        "exec_order": exec_order,
        "order_exact": order_exact,
        "phase_level_known": phase_known,
        "total_run": total_run,
        "total_fail": total_fail,
        "total_ign": total_ign,
        "phases": phases,
        "p1_ok": p1_ok,
        "p2_ok": p2_ok,
        "target_ok": target_ok,
        "rc_c": rc_c,
        "fail_lines": fail_lines[:3],
    }
    return evaluate_java_repeated_pollution_report(
        raw_rep,
        allow_upstream_nonidempotent_p2_call_fail=allow_upstream_nonidempotent_p2_call_fail,
        expected_p2_call_exc=expected_p2_call_exc,
    )


def check_java_w_plus_heldout_detailed(
    sample: Dict[str, Any],
    allow_upstream_nonidempotent_p2_call_fail: bool = False,
    expected_p2_call_exc: Optional[str] = None,
) -> Tuple[bool, str, float, Dict[str, Any]]:
    """
    Held-out Java stability verification:
      - ID: 5 fresh held-out NonDex seeds (`910001..910005`).
      - OD: Exact protocol schedule:
            standalone 2x (`standalone_1`, `standalone_2`),
            partner->target 2x (`partner_target_1`, `partner_target_2`),
            target->partner 2x (`target_partner_1`, `target_partner_2`),
            repeated-pollution same-JVM `partner->partner->target` 1x (`partner_partner_target_1`).
    """
    proj_dir = sample["proj_dir"]
    mod_rel = sample["module"]
    jdk = sample["jdk"]
    pl = f"-pl {mod_rel}" if mod_rel not in ("", ".") else ""
    t0 = time.time()
    sched_report: Dict[str, Any] = {}
    if sample["test_type"] == "ID":
        spec = f"{sample['victim_fqcn']}#{sample['victim_method']}"
        for seed in (910001, 910002, 910003, 910004, 910005):
            with DOCKER_SEM:
                rc_c, out, _ = rc.docker_mvn(
                    proj_dir,
                    jdk,
                    f"edu.illinois:nondex-maven-plugin:2.1.7:nondex {pl} -Dtest={spec} "
                    f"-DnondexSeed={seed} -DnondexRuns=1 -Dstyle.color=never",
                    timeout=95,
                )
            has_fail = (
                ("BUILD FAILURE" in out)
                or bool(re.search(r"Failures:\s*[1-9]|Errors:\s*[1-9]|Skipped:\s*[1-9]", out))
                or ("There are test failures" in out)
            )
            ok_seed = (rc_c == 0 and "BUILD SUCCESS" in out and not has_fail)
            sched_report[f"nondex_{seed}"] = ok_seed
            if not ok_seed:
                sched_report["w_plus_heldout_passed_strict"] = False
                sched_report["w_plus_heldout_passed_target_stress"] = False
                return False, f"Heldout NonDex seed {seed} failed", round(time.time() - t0, 2), sched_report
        sched_report["w_plus_heldout_passed_strict"] = True
        sched_report["w_plus_heldout_passed_target_stress"] = True
        return True, "Passed 5 held-out NonDex seeds (910001..910005)", round(time.time() - t0, 2), sched_report
    else:
        v_spec = f"{sample['victim_fqcn']}#{sample['victim_method']}"
        p_spec = f"{sample.get('polluter_fqcn', '')}#{sample.get('polluter_method', '')}"
        has_partner = bool(sample.get("polluter_fqcn") and sample.get("polluter_method") and p_spec != v_spec)
        if not sample.get("uses_junit5") and rc.ensure_order_runner_ready(proj_dir, mod_rel, jdk):
            mod_dir = proj_dir if mod_rel in ("", ".") else os.path.join(proj_dir, mod_rel)
            tclasses = os.path.join(mod_dir, "target/test-classes")
            os.makedirs(tclasses, exist_ok=True)
            r1_path = os.path.join(tclasses, "FT1OrderRunner.java")
            r2_path = os.path.join(tclasses, "FT1HeldoutRepeatedRunner.java")
            try:
                open(r1_path, "w", encoding="utf-8").write(rc.JUNIT4_ORDER_RUNNER_SRC)
                open(r2_path, "w", encoding="utf-8").write(JUNIT4_HELDOUT_REPEATED_RUNNER_SRC)
                cmds = [
                    "CP=target/test-classes:target/classes:$(cat target/ft1_cp.txt)",
                    "javac -cp \"$CP\" target/test-classes/FT1OrderRunner.java target/test-classes/FT1HeldoutRepeatedRunner.java",
                ]
                for i in (1, 2):
                    cmds.append(
                        f"out=$(java -cp \"$CP\" FT1OrderRunner '{v_spec}' 2>&1); rc=$?; "
                        f"if [ $rc -ne 0 ] || ! echo \"$out\" | grep -q 'FT1_SUMMARY RUN=1 FAIL=0 IGNORE=0'; then "
                        f"echo 'HELDOUT_JAVA_FAIL::standalone_{i}'; echo \"$out\"; exit 1; fi; echo 'HELDOUT_JAVA_OK::standalone_{i}'"
                    )
                if has_partner:
                    for i in (1, 2):
                        cmds.append(
                            f"out=$(java -cp \"$CP\" FT1OrderRunner '{p_spec}' '{v_spec}' 2>&1); rc=$?; "
                            f"if [ $rc -ne 0 ] || ! echo \"$out\" | grep -q 'FT1_SUMMARY RUN=2 FAIL=0 IGNORE=0'; then "
                            f"echo 'HELDOUT_JAVA_FAIL::partner_target_{i}'; echo \"$out\"; exit 1; fi; echo 'HELDOUT_JAVA_OK::partner_target_{i}'"
                        )
                        cmds.append(
                            f"out=$(java -cp \"$CP\" FT1OrderRunner '{v_spec}' '{p_spec}' 2>&1); rc=$?; "
                            f"if [ $rc -ne 0 ] || ! echo \"$out\" | grep -q 'FT1_SUMMARY RUN=2 FAIL=0 IGNORE=0'; then "
                            f"echo 'HELDOUT_JAVA_FAIL::target_partner_{i}'; echo \"$out\"; exit 1; fi; echo 'HELDOUT_JAVA_OK::target_partner_{i}'"
                        )
                    cmds.append(
                        f"out=$(java -cp \"$CP\" FT1HeldoutRepeatedRunner '{p_spec}' '{v_spec}' 2>&1); rc=$?; "
                        f"echo \"$out\" | grep -E 'FT1_REP_|FT1_EXEC_ORDER|FT1_HELDOUT_REP'; "
                        f"if [ $rc -ne 0 ] || ! echo \"$out\" | grep -q 'FT1_REP_VERDICTS p1=PASS.*target=PASS.*order_exact=true'; then "
                        f"echo 'HELDOUT_JAVA_FAIL::partner_partner_target_1'; echo \"$out\"; exit 1; fi; echo 'HELDOUT_JAVA_OK::partner_partner_target_1'"
                    )
                cmds.append("echo 'HELDOUT_JAVA_ALL_OK'")
                bash_cmd = " && ".join(cmds)
                with DOCKER_SEM:
                    rc_c, out, _ = rc.run_docker_cmd(proj_dir, jdk, bash_cmd, workdir_rel=mod_rel, timeout=135)
                exec_order_trace = ""
                rep_mode_trace = ""
                fail_detail_lines = []
                for ln in out.splitlines():
                    if "HELDOUT_JAVA_OK::" in ln:
                        sched_report[ln.split("HELDOUT_JAVA_OK::", 1)[1].strip()] = True
                    elif "HELDOUT_JAVA_FAIL::" in ln:
                        sched_report[ln.split("HELDOUT_JAVA_FAIL::", 1)[1].strip()] = False
                    elif "FT1_REP_MODE=" in ln:
                        rep_mode_trace = ln.split("FT1_REP_MODE=", 1)[1].strip()
                    elif "FT1_EXEC_ORDER=" in ln:
                        exec_order_trace = ln.split("FT1_EXEC_ORDER=", 1)[1].strip()
                    elif "FT1_REP_FAIL:" in ln or "FT1_FAIL:" in ln or "Exception" in ln or "Error" in ln:
                        fail_detail_lines.append(ln.strip())
                rep_parsed = _parse_java_rep_output(
                    out,
                    0 if (rc_c == 0 and "HELDOUT_JAVA_ALL_OK" in out) else 1,
                    allow_upstream_nonidempotent_p2_call_fail=allow_upstream_nonidempotent_p2_call_fail,
                    expected_p2_call_exc=expected_p2_call_exc,
                ) if has_partner else {
                    "strict_passed": True, "target_stress_passed": True, "is_unknown": False,
                    "status": "NO_PARTNER", "phase_level_known": True,
                    "p1_ok": True, "p2_ok": True, "p2_call_ok": True, "p2_lifecycle_ok": True,
                    "target_ok": True, "order_exact": True, "p2_call_exception_type": None,
                }
                if rep_mode_trace:
                    sched_report["repeated_runner_mode"] = rep_mode_trace
                if exec_order_trace:
                    sched_report["repeated_exec_order"] = exec_order_trace
                sched_report["repeated_report"] = rep_parsed
                sched_report["w_plus_heldout_passed_strict"] = bool(rc_c == 0 and "HELDOUT_JAVA_ALL_OK" in out and rep_parsed["strict_passed"])
                sched_report["w_plus_heldout_passed_target_stress"] = bool(rc_c == 0 and "HELDOUT_JAVA_ALL_OK" in out and rep_parsed["target_stress_passed"])
                if rc_c == 0 and "HELDOUT_JAVA_ALL_OK" in out:
                    return (
                        bool(rep_parsed["strict_passed"]),
                        "Passed held-out Java OD schedules (standalone 2x, orders 2x, single-lifecycle partner->partner->target 1x"
                        + f" | {rep_parsed.get('status', 'OK')})",
                        round(time.time() - t0, 2),
                        sched_report,
                    )
                fail_hdr = next((ln for ln in out.splitlines() if "HELDOUT_JAVA_FAIL::" in ln), "HELDOUT_JAVA_FAIL")
                diag = fail_hdr + (" | " + " ; ".join(fail_detail_lines[:2]) if fail_detail_lines else " | " + out[-260:].replace("\n", " "))
                return False, diag[:500], round(time.time() - t0, 2), sched_report
            finally:
                for fn in os.listdir(tclasses):
                    if fn.startswith(("FT1OrderRunner", "FT1HeldoutRepeatedRunner")):
                        try:
                            os.remove(os.path.join(tclasses, fn))
                        except OSError:
                            pass
        else:
            # Surefire fallback implementing full 4-schedule protocol
            orders = [(f"standalone_{i}", v_spec) for i in (1, 2)]
            if has_partner:
                for i in (1, 2):
                    orders.append((f"partner_target_{i}", f"{p_spec},{v_spec}"))
                    orders.append((f"target_partner_{i}", f"{v_spec},{p_spec}"))
                orders.append(("partner_partner_target_1", f"{p_spec},{p_spec},{v_spec}"))
            for lbl, order_arg in orders:
                with DOCKER_SEM:
                    rc_c, out, _ = rc.docker_mvn(
                        proj_dir, jdk, f"test {pl} -Dsurefire.runOrder=testorder -Dtest={order_arg}", timeout=95
                    )
                ok_o = (rc_c == 0 and "BUILD SUCCESS" in out and not re.search(r"Failures:\s*[1-9]|Errors:\s*[1-9]|Skipped:\s*[1-9]", out))
                sched_report[lbl] = ok_o
                if not ok_o:
                    sched_report["w_plus_heldout_passed_strict"] = False
                    sched_report["w_plus_heldout_passed_target_stress"] = False
                    return False, f"Surefire heldout `{lbl}` failed", round(time.time() - t0, 2), sched_report
            sched_report["w_plus_heldout_passed_strict"] = True
            sched_report["w_plus_heldout_passed_target_stress"] = True
            return True, "Passed held-out Java OD Surefire schedules", round(time.time() - t0, 2), sched_report


def check_java_w_plus_heldout(sample: Dict[str, Any]) -> Tuple[bool, str, float]:
    ok, diag, dt, _ = check_java_w_plus_heldout_detailed(sample)
    return ok, diag, dt


# ============================================================================
# Python Attributed Mutation Runner Wrapper
# ============================================================================

def run_attributed_py_mut(
    proj_dir: str,
    pytest_args: List[str],
    target_node_hint: str,
    mutants: List[Dict[str, Any]],
    timeout: int = 120,
    partner_node_hint: str = "",
) -> Tuple[bool, Dict[str, Dict[str, Any]], float]:
    if not mutants:
        return True, {}, 0.0
    runner_p = os.path.join(proj_dir, ".ft1_v2_mut_runner.py")
    spec_p = os.path.join(proj_dir, ".ft1_v2_mut_spec.json")
    orig_files = {m["rel_file"]: open(os.path.join(proj_dir, m["rel_file"]), "rb").read() for m in mutants}
    try:
        open(runner_p, "w", encoding="utf-8").write(PY_V2_ATTRIBUTED_MUT_RUNNER)
        open(spec_p, "w", encoding="utf-8").write(json.dumps({
            "pytest_args": pytest_args,
            "target_node_hint": target_node_hint,
            "partner_node_hint": partner_node_hint,
            "mutants": mutants,
            "per_mutant_timeout": 8,
        }, ensure_ascii=False))
        with DOCKER_SEM:
            rc_c, out, dt = rpc.run_py_docker(
                proj_dir,
                "python3 .ft1_v2_mut_runner.py .ft1_v2_mut_spec.json",
                timeout=timeout,
            )
        s_tag, e_tag = "===FT1_PY_V2_MUT_START===", "===FT1_PY_V2_MUT_END==="
        if rc_c == 0 and s_tag in out and e_tag in out:
            pay = json.loads(out.split(s_tag, 1)[1].split(e_tag, 1)[0].strip())
            return True, pay.get("details", {}), round(dt, 2)
        return False, {}, round(dt, 2)
    finally:
        for rf, raw in orig_files.items():
            open(os.path.join(proj_dir, rf), "wb").write(raw)
        for fp in (runner_p, spec_p):
            if os.path.exists(fp):
                try:
                    os.remove(fp)
                except OSError:
                    pass


def build_py_repeated_pollution_plugin_src(target_node: str, partner_node: str) -> str:
    """
    Build the inline pytest plugin source for single-session `partner -> partner -> target`
    execution with exact full-nodeid item selection, explicit role tagging (`p1`, `p2`, `target`),
    and per-occurrence lifecycle (`setup`, `call`, `teardown`) recording (`R5-3` / `R5-4`).
    """
    return (
        "import json, pytest\n"
        f"_PARTNER_HINT = {partner_node!r}\n"
        f"_TARGET_HINT = {target_node!r}\n"
        "_CUR_OCC = -1\n"
        "_CUR_ROLE = None\n"
        "_EVENTS = []\n"
        "def _match_node(nid, hint):\n"
        "    n = str(nid or '').replace('\\\\', '/').lstrip('./')\n"
        "    h = str(hint or '').replace('\\\\', '/').lstrip('./')\n"
        "    if not n or not h:\n"
        "        return False\n"
        "    if n == h:\n"
        "        return True\n"
        "    if '[' in h:\n"
        "        return False\n"
        "    return n.startswith(h + '[')\n"
        "def _clone_item(it):\n"
        "    cls = type(it)\n"
        "    if hasattr(cls, 'from_parent'):\n"
        "        if isinstance(it, pytest.Function):\n"
        "            kw = {'name': it.name, 'callobj': it.obj}\n"
        "            if getattr(it, 'callspec', None) is not None:\n"
        "                kw['callspec'] = it.callspec\n"
        "            if getattr(it, 'fixtureinfo', None) is not None:\n"
        "                kw['fixtureinfo'] = it.fixtureinfo\n"
        "            if getattr(it, 'originalname', None) is not None:\n"
        "                kw['originalname'] = it.originalname\n"
        "            try:\n"
        "                return cls.from_parent(it.parent, **kw)\n"
        "            except TypeError:\n"
        "                return cls.from_parent(it.parent, name=it.name)\n"
        "        return cls.from_parent(it.parent, name=it.name)\n"
        "    return cls(it.name, it.parent)\n"
        "def pytest_collection_modifyitems(session, config, items):\n"
        "    p_items = [it for it in items if _match_node(it.nodeid, _PARTNER_HINT)]\n"
        "    t_items = [it for it in items if _match_node(it.nodeid, _TARGET_HINT)]\n"
        "    p1_list = list(p_items)\n"
        "    for it in p1_list:\n"
        "        it._ft1_role = 'p1'\n"
        "    p2_list = [_clone_item(it) for it in p_items]\n"
        "    for it in p2_list:\n"
        "        it._ft1_role = 'p2'\n"
        "    same_pt = any(it in p_items for it in t_items)\n"
        "    t_list = ([_clone_item(it) for it in t_items] if same_pt else list(t_items))\n"
        "    for it in t_list:\n"
        "        it._ft1_role = 'target'\n"
        "    items[:] = p1_list + p2_list + t_list\n"
        "@pytest.hookimpl(tryfirst=True)\n"
        "def pytest_runtest_protocol(item, nextitem):\n"
        "    global _CUR_OCC, _CUR_ROLE\n"
        "    _CUR_OCC += 1\n"
        "    _CUR_ROLE = getattr(item, '_ft1_role', None)\n"
        "    return None\n"
        "def pytest_runtest_logreport(report):\n"
        "    out = report.outcome\n"
        "    if getattr(report, 'wasxfail', None) is not None:\n"
        "        out = 'xfailed' if out == 'skipped' else 'xpassed'\n"
        "    _EVENTS.append({\n"
        "        'occ': _CUR_OCC,\n"
        "        'role': _CUR_ROLE,\n"
        "        'nodeid': report.nodeid,\n"
        "        'when': report.when,\n"
        "        'outcome': out,\n"
        "    })\n"
        "def pytest_sessionfinish(session, exitstatus):\n"
        "    print('FT1_PY_REP_JSON=' + json.dumps({'events': _EVENTS, 'exitstatus': int(exitstatus)}))\n"
    )


def run_py_repeated_pollution_raw(
    proj_dir: str,
    target_node: str,
    partner_node: str,
    allow_upstream_nonidempotent_p2_call_fail: bool = False,
) -> Dict[str, Any]:
    """
    Execute ONLY the single-session Python `partner -> partner -> target` repeated-pollution schedule
    in Docker on the CURRENT working tree of `proj_dir`, and evaluate it strictly via
    `evaluate_py_repeated_pollution_report`.
    """
    t0 = time.time()
    has_partner = bool(partner_node and partner_node != target_node)
    if not has_partner:
        return {
            "has_partner": False,
            "strict_passed": True,
            "target_stress_passed": True,
            "is_unknown": False,
            "status": "NO_PARTNER",
            "sec": 0.0,
        }
    plug_src = build_py_repeated_pollution_plugin_src(target_node, partner_node)
    py_run = (
        "import os, subprocess, sys\n"
        f"t = {target_node!r}\n"
        f"p = {partner_node!r}\n"
        f"PLUG_SRC = {plug_src!r}\n"
        "plug = '_ft1_rep_heldout_plug.py'\n"
        "open(plug, 'w', encoding='utf-8').write(PLUG_SRC)\n"
        "env = dict(os.environ)\n"
        "env['PYTHONPATH'] = os.getcwd() + os.pathsep + env.get('PYTHONPATH', '')\n"
        "try:\n"
        "    r = subprocess.run(\n"
        "        [sys.executable, '-m', 'pytest', '-q', '-s', '--tb=short', '-p', 'no:randomly', '-p', 'no:random_order', '-p', '_ft1_rep_heldout_plug', p, t],\n"
        "        capture_output=True, text=True, env=env,\n"
        "    )\n"
        "    print(r.stdout or '')\n"
        "    if r.stderr:\n"
        "        print(r.stderr)\n"
        "finally:\n"
        "    if os.path.exists(plug):\n"
        "        os.remove(plug)\n"
    )
    b64_sc = base64.b64encode(py_run.encode("utf-8")).decode("ascii")
    with DOCKER_SEM:
        _, out, _ = rpc.run_py_docker(
            proj_dir,
            f"python3 -c \"import base64; exec(base64.b64decode('{b64_sc}').decode('utf-8'))\"",
            timeout=95,
        )
    rep_line = next((ln.split("FT1_PY_REP_JSON=", 1)[1].strip() for ln in out.splitlines() if "FT1_PY_REP_JSON=" in ln), None)
    if not rep_line:
        return {
            "has_partner": True,
            "strict_passed": False,
            "target_stress_passed": False,
            "is_unknown": True,
            "status": "UNKNOWN_NO_REP_JSON",
            "p1_ok": False,
            "p2_call_ok": False,
            "p2_lifecycle_ok": False,
            "target_ok": False,
            "raw_tail": out[-350:].replace("\n", " "),
            "sec": round(time.time() - t0, 2),
        }
    rep_d = json.loads(rep_line)
    ev = evaluate_py_repeated_pollution_report(
        rep_d,
        target_node=target_node,
        partner_node=partner_node,
        allow_upstream_nonidempotent_p2_call_fail=allow_upstream_nonidempotent_p2_call_fail,
    )
    ev["has_partner"] = True
    ev["events"] = rep_d.get("events", [])
    ev["sec"] = round(time.time() - t0, 2)
    return ev


def check_py_w_plus_heldout_detailed(
    proj_dir: str,
    target_node: str,
    partner_node: str,
    allow_upstream_nonidempotent_p2_call_fail: bool = False,
) -> Tuple[bool, str, float, Dict[str, Any]]:
    """
    Held-out Python OD schedules (`v2.3.0-certified`, closing `R5-3` and `R5-4`):
      - standalone 2x, partner->target 2x, target->partner 2x,
      - single-session repeated pollution `partner->partner->target` 1x with exact nodeid matching,
        role verification, and full setup/call/teardown lifecycle checking via
        `evaluate_py_repeated_pollution_report`.
    Returns `(strict_passed, diag, sec, sched_report)` where `sched_report` explicitly records
    BOTH `w_plus_heldout_passed_strict` (primary) AND `w_plus_heldout_passed_target_stress` (matched-control sensitivity).
    """
    t0 = time.time()
    has_partner = bool(partner_node and partner_node != target_node)
    rep_plug_src = build_py_repeated_pollution_plugin_src(target_node, partner_node)
    py_check = (
        "import json, os, re, subprocess, sys\n"
        f"t = {target_node!r}\n"
        f"p = {partner_node!r}\n"
        f"has_p = {has_partner!r}\n"
        f"PLUG_SRC = {rep_plug_src!r}\n"
        "def run_sched(args, label):\n"
        "    r = subprocess.run([sys.executable, '-m', 'pytest', '-q', '--tb=short', '-p', 'no:randomly', '-p', 'no:random_order'] + args, capture_output=True, text=True)\n"
        "    out = (r.stdout or '') + '\\n' + (r.stderr or '')\n"
        "    if r.returncode != 0 or re.search(r'[1-9][0-9]* (failed|error|errors|skipped|xfailed)', out):\n"
        "        print('HELDOUT_PY_FAIL::' + label + '::' + out[-400:].replace('\\n', ' '))\n"
        "        sys.exit(1)\n"
        "    print('HELDOUT_PY_OK::' + label)\n"
        "for i in range(2):\n"
        "    run_sched([t], f'standalone_{i+1}')\n"
        "if has_p:\n"
        "    for i in range(2):\n"
        "        run_sched([p, t], f'partner_target_{i+1}')\n"
        "        run_sched([t, p], f'target_partner_{i+1}')\n"
        "    plug = '_ft1_rep_heldout_plug.py'\n"
        "    open(plug, 'w', encoding='utf-8').write(PLUG_SRC)\n"
        "    env = dict(os.environ)\n"
        "    env['PYTHONPATH'] = os.getcwd() + os.pathsep + env.get('PYTHONPATH', '')\n"
        "    try:\n"
        "        r_rep = subprocess.run(\n"
        "            [sys.executable, '-m', 'pytest', '-q', '-s', '--tb=short', '-p', 'no:randomly', '-p', 'no:random_order', '-p', '_ft1_rep_heldout_plug', p, t],\n"
        "            capture_output=True, text=True, env=env,\n"
        "        )\n"
        "    finally:\n"
        "        if os.path.exists(plug):\n"
        "            os.remove(plug)\n"
        "    rep_out = (r_rep.stdout or '') + '\\n' + (r_rep.stderr or '')\n"
        "    rep_line = next((ln.split('FT1_PY_REP_JSON=', 1)[1].strip() for ln in rep_out.splitlines() if 'FT1_PY_REP_JSON=' in ln), None)\n"
        "    if not rep_line:\n"
        "        print('HELDOUT_PY_FAIL::partner_partner_target::NO_REP_JSON::' + rep_out[-350:].replace('\\n', ' '))\n"
        "        sys.exit(1)\n"
        "    print('FT1_PY_REP_JSON=' + rep_line)\n"
        "print('HELDOUT_PY_BASE_DONE')\n"
    )
    b64_sc = base64.b64encode(py_check.encode("utf-8")).decode("ascii")
    with DOCKER_SEM:
        rc_c, out, _ = rpc.run_py_docker(
            proj_dir,
            f"python3 -c \"import base64; exec(base64.b64decode('{b64_sc}').decode('utf-8'))\"",
            timeout=95,
        )
    sched_report: Dict[str, Any] = {}
    for ln in out.splitlines():
        if "HELDOUT_PY_OK::" in ln:
            sched_report[ln.split("HELDOUT_PY_OK::", 1)[1].strip()] = True
        elif "HELDOUT_PY_FAIL::" in ln:
            lbl = ln.split("HELDOUT_PY_FAIL::", 1)[1].split("::", 1)[0].strip()
            sched_report[lbl] = False

    if rc_c != 0 or "HELDOUT_PY_BASE_DONE" not in out:
        sched_report["w_plus_heldout_passed_strict"] = False
        sched_report["w_plus_heldout_passed_target_stress"] = False
        diag = next((ln for ln in out.splitlines() if "HELDOUT_PY_FAIL::" in ln), out[-350:].strip())
        return False, diag[:450], round(time.time() - t0, 2), sched_report

    if not has_partner:
        sched_report["w_plus_heldout_passed_strict"] = True
        sched_report["w_plus_heldout_passed_target_stress"] = True
        return True, "Passed held-out Python standalone 2x (no distinct partner)", round(time.time() - t0, 2), sched_report

    rep_line = next((ln.split("FT1_PY_REP_JSON=", 1)[1].strip() for ln in out.splitlines() if "FT1_PY_REP_JSON=" in ln), None)
    if not rep_line:
        sched_report["w_plus_heldout_passed_strict"] = False
        sched_report["w_plus_heldout_passed_target_stress"] = False
        return False, "HELDOUT_PY_FAIL::partner_partner_target::MISSING_REP_JSON", round(time.time() - t0, 2), sched_report

    rep_d = json.loads(rep_line)
    ev = evaluate_py_repeated_pollution_report(
        rep_d,
        target_node=target_node,
        partner_node=partner_node,
        allow_upstream_nonidempotent_p2_call_fail=allow_upstream_nonidempotent_p2_call_fail,
    )
    sched_report["repeated_report"] = ev
    sched_report["partner_partner_target_strict"] = ev["strict_passed"]
    sched_report["partner_partner_target_stress"] = ev["target_stress_passed"]
    sched_report["w_plus_heldout_passed_strict"] = bool(ev["strict_passed"])
    sched_report["w_plus_heldout_passed_target_stress"] = bool(ev["target_stress_passed"])
    summary_str = (
        f"FT1_PY_REP_SUMMARY status={ev['status']} p1={ev['p1_ok']} p2_call={ev['p2_call_ok']} "
        f"p2_lc={ev['p2_lifecycle_ok']} target={ev['target_ok']} strict={ev['strict_passed']} stress={ev['target_stress_passed']}"
    )
    if ev["strict_passed"]:
        return (
            True,
            f"Passed held-out Python OD schedules (strict all-pass) | {summary_str}",
            round(time.time() - t0, 2),
            sched_report,
        )
    return (
        False,
        f"HELDOUT_PY_REP_NOT_STRICT | {summary_str}",
        round(time.time() - t0, 2),
        sched_report,
    )


def check_py_w_plus_heldout(proj_dir: str, target_node: str, partner_node: str) -> Tuple[bool, str, float]:
    ok_strict, diag, dt, _ = check_py_w_plus_heldout_detailed(
        proj_dir, target_node, partner_node, allow_upstream_nonidempotent_p2_call_fail=False
    )
    return ok_strict, diag, dt


# ============================================================================
# Full PR Diff Fetching & Provenance Analysis
# ============================================================================

def get_cached_pr_diff(pr_url: str) -> Optional[str]:
    if not pr_url or "github.com/" not in pr_url or "/pull/" not in pr_url:
        return None
    os.makedirs(PR_DIFF_CACHE_DIR, exist_ok=True)
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", pr_url.strip().rstrip("/"))
    cache_p = os.path.join(PR_DIFF_CACHE_DIR, slug + ".diff")
    if os.path.exists(cache_p) and os.path.getsize(cache_p) > 10:
        return open(cache_p, encoding="utf-8", errors="replace").read()
    diff_url = pr_url.strip().rstrip("/") + ".diff"
    try:
        req = urllib.request.Request(diff_url, headers={"User-Agent": "FT1-Audit/2.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            txt = resp.read().decode("utf-8", errors="replace")
        if txt and "diff --git" in txt:
            open(cache_p, "w", encoding="utf-8").write(txt)
            return txt
    except Exception:
        return None
    return None


def inspect_pr_diff_files(diff_txt: str) -> Dict[str, Any]:
    sections = re.split(r"^diff --git ", diff_txt, flags=re.MULTILINE)[1:]
    all_files, test_files, prod_files, build_files = [], [], [], []
    sec_by_file: Dict[str, str] = {}
    for sec in sections:
        ln0 = sec.splitlines()[0] if sec else ""
        m = re.match(r"a/(.*?)\s+b/(.*)", ln0.strip())
        f_rel = m.group(2).strip() if m else ln0.strip()
        all_files.append(f_rel)
        sec_by_file[f_rel] = "diff --git " + sec
        if "/src/test/" in f"/{f_rel}" or f_rel.endswith("Test.java") or f_rel.endswith("Tests.java"):
            test_files.append(f_rel)
        elif f_rel.endswith("pom.xml") or f_rel.endswith(".gradle") or f_rel.endswith(".xml"):
            build_files.append(f_rel)
        elif "/src/main/" in f"/{f_rel}" or f_rel.endswith(".java"):
            prod_files.append(f_rel)
    return {
        "pr_diff_sha256": sha256_hex(diff_txt.encode("utf-8"))[:24],
        "all_files": all_files,
        "test_files": test_files,
        "prod_files": prod_files,
        "build_files": build_files,
        "pr_scope_type": "TEST_AND_PROD_PR" if prod_files else "TEST_ONLY_PR",
        "sec_by_file": sec_by_file,
    }


def apply_single_file_diff_hunk(orig_src: str, file_diff_sec: str) -> Optional[str]:
    idx = file_diff_sec.find("\n@@ ")
    if idx == -1:
        return None
    with tempfile.TemporaryDirectory() as tmpd:
        tf = os.path.join(tmpd, "Target.java")
        df = os.path.join(tmpd, "hunk.diff")
        open(tf, "w", encoding="utf-8").write(orig_src)
        body = "--- Target.java\n+++ Target.java" + file_diff_sec[idx:]
        if not body.endswith("\n"):
            body += "\n"
        open(df, "w", encoding="utf-8").write(body)
        p = subprocess.run(["patch", "--batch", "--fuzz=2", tf, df], capture_output=True, text=True, timeout=10)
        if p.returncode != 0:
            return None
        return open(tf, encoding="utf-8", errors="replace").read()


# ============================================================================
# Per-Sample V2 Audit & M_ext Freeze Worker
# ============================================================================

def audit_and_freeze_java_sample(sample: Dict[str, Any], upstream: Dict[str, Any]) -> Dict[str, Any]:
    sid = sample["sample_id"]
    pdir = sample["proj_dir"]
    mod_rel = sample["module"]
    jdk = sample["jdk"]
    ttype = sample["test_type"]
    pit_pair = tuple(sample["pit_pair"])
    vic_file = sample["victim_file_path"]
    pol_file = sample.get("polluter_file_path") or vic_file

    # Clean any residual markers first
    for fp in {vic_file, pol_file}:
        if os.path.exists(fp):
            cleaned = re.sub(r"/\*FT1_ISOLATED\s*(.*?)\s*\*/", r"\1", open(fp, encoding="utf-8", errors="replace").read())
            open(fp, "w", encoding="utf-8").write(cleaned)

    # 1. Run 3/3 clean-e_0 attributed PIT with `-Dmutators=ALL`:
    #    - Verifies `M_det` (`PIT_DEFAULT_MUTATOR_CLASSES`, 3/3 attributed kill by `victim_fqcn`)
    #    - Constructs frozen `M_ext_op_value_flow` & `M_ext_op_call` (`non-default`, 3/3 attributed kill)
    all_runs_killed: List[Set[str]] = []
    mut_meta_by_eid: Dict[str, Dict[str, Any]] = {}
    pit_all_secs: List[float] = []
    mext_mut_flag_used = "ALL"
    for _ in range(3):
        ok, muts, dt, _ = run_attributed_java_pit(
            proj_dir=pdir,
            mod_rel=mod_rel,
            jdk=jdk,
            test_file_path=vic_file,
            test_fqcn=sample["victim_fqcn"],
            test_method=sample["victim_method"],
            focal_classes=sample["focal_classes"],
            pit_pair=pit_pair,
            fqcn_to_path=sample.get("fqcn_to_path"),
            mutators_arg="ALL",
            timeout=150,
        )
        pit_all_secs.append(dt)
        if not ok:
            break
        by_eid, _ = resolve_pit_mutants_by_exact_id(muts)
        k_set = set()
        for eid, m in by_eid.items():
            mut_meta_by_eid[eid] = m
            if m["attributed_kill"] and not m.get("is_unknown"):
                k_set.add(eid)
        all_runs_killed.append(k_set)

    # Fallback if `-Dmutators=ALL` timed out on large class due to NonVoidMethodCall explosion:
    # run `STRONGER,EXPERIMENTAL_SWITCH` (covers DEFAULTS + REMOVE_CONDITIONALS_* + EXPERIMENTAL_SWITCH)
    if len(all_runs_killed) < 3:
        all_runs_killed = []
        mut_meta_by_eid = {}
        pit_all_secs = []
        mext_mut_flag_used = "STRONGER,EXPERIMENTAL_SWITCH"
        for _ in range(3):
            ok, muts, dt, _ = run_attributed_java_pit(
                proj_dir=pdir,
                mod_rel=mod_rel,
                jdk=jdk,
                test_file_path=vic_file,
                test_fqcn=sample["victim_fqcn"],
                test_method=sample["victim_method"],
                focal_classes=sample["focal_classes"],
                pit_pair=pit_pair,
                fqcn_to_path=sample.get("fqcn_to_path"),
                mutators_arg=mext_mut_flag_used,
                timeout=150,
            )
            pit_all_secs.append(dt)
            if not ok:
                break
            by_eid, _ = resolve_pit_mutants_by_exact_id(muts)
            k_set = set()
            for eid, m in by_eid.items():
                mut_meta_by_eid[eid] = m
                if m["attributed_kill"] and not m.get("is_unknown"):
                    k_set.add(eid)
            all_runs_killed.append(k_set)

    det_3x_all = (all_runs_killed[0] & all_runs_killed[1] & all_runs_killed[2]) if len(all_runs_killed) == 3 else set()

    # Construct strictly 3/3 attributed default obligations keyed by lossless 8-tuple `exact_id`
    v2_m_det: List[Dict[str, Any]] = []
    for eid in sorted(det_3x_all):
        m = mut_meta_by_eid[eid]
        if m["mutator_short"] in PIT_DEFAULT_MUTATOR_CLASSES:
            m_new = dict(m)
            m_new["v2_attributed_3x"] = True
            v2_m_det.append(m_new)

    # 2. For OD: 3/3 attributed `polluter_m_det` verification (lossless `exact_id`)!
    v2_pol_m_det: List[Dict[str, Any]] = []
    pol_fclasses = sample.get("polluter_focal_classes") or sample["focal_classes"]
    if ttype == "OD" and sample.get("polluter_fqcn") and sample.get("polluter_method") and pol_fclasses:
        p_sets: List[Set[str]] = []
        p_meta: Dict[str, Dict[str, Any]] = {}
        for _ in range(3):
            pok, pmuts, _, _ = run_attributed_java_pit(
                proj_dir=pdir,
                mod_rel=mod_rel,
                jdk=jdk,
                test_file_path=pol_file,
                test_fqcn=sample["polluter_fqcn"],
                test_method=sample["polluter_method"],
                focal_classes=pol_fclasses[:4],
                pit_pair=pit_pair,
                fqcn_to_path=sample.get("fqcn_to_path"),
                mutators_arg="DEFAULTS",
                timeout=120,
            )
            if not pok:
                break
            p_by_eid, _ = resolve_pit_mutants_by_exact_id(pmuts)
            pk = set()
            for peid, pm in p_by_eid.items():
                p_meta[peid] = pm
                if pm["attributed_kill"] and not pm.get("is_unknown"):
                    pk.add(peid)
            p_sets.append(pk)
        if len(p_sets) == 3:
            p_det_eids = sorted(p_sets[0] & p_sets[1] & p_sets[2])
            v2_pol_m_det = [p_meta[peid] for peid in p_det_eids[:15]]

    # 3. Construct Frozen Disjoint `M_ext_op_value_flow` & `M_ext_op_call` (lossless `exact_id` disjointness)
    forb_eids = {m["exact_id"] for m in (v2_m_det + v2_pol_m_det)}
    m_ext_val_flow: List[Dict[str, Any]] = []
    m_ext_call: List[Dict[str, Any]] = []
    for eid in sorted(det_3x_all):
        m = mut_meta_by_eid[eid]
        ms = m["mutator_short"]
        if ms in PIT_DEFAULT_MUTATOR_CLASSES:
            continue
        if eid in forb_eids:
            continue
        forb_eids.add(eid)
        mc = dict(m)
        if ms in PIT_CALL_CRASH_PRONE_CLASSES:
            mc["pool"] = "M_ext_op_call"
            m_ext_call.append(mc)
        else:
            mc["pool"] = "M_ext_op_value_flow"
            m_ext_val_flow.append(mc)

    m_ext_val_flow = m_ext_val_flow[:25]
    m_ext_call = m_ext_call[:15]

    # 4. Authoritative PR Provenance & 3/3 Re-Evaluation (Full Test-File Diffs)
    pr_manifest_entry: Optional[Dict[str, Any]] = None
    v2_m_gold_ids: List[str] = []
    v2_dev_pr_eval: Optional[Dict[str, Any]] = None
    if sample.get("pr_url"):
        diff_txt = get_cached_pr_diff(sample["pr_url"])
        if diff_txt:
            info = inspect_pr_diff_files(diff_txt)
            orig_vic = open(vic_file, encoding="utf-8", errors="replace").read()
            orig_pol = open(pol_file, encoding="utf-8", errors="replace").read() if pol_file != vic_file else orig_vic
            applied_test_files: Dict[str, str] = {}
            norm_vic_rel = sample["victim_file_rel"].replace("\\", "/").lstrip("/")
            norm_pol_rel = os.path.relpath(pol_file, pdir).replace("\\", "/").lstrip("/")

            for f_rel, sec in info["sec_by_file"].items():
                f_abs = os.path.join(pdir, f_rel)
                if f_rel.endswith(norm_vic_rel) or norm_vic_rel.endswith(f_rel):
                    patched = apply_single_file_diff_hunk(orig_vic, sec)
                    if patched is not None:
                        applied_test_files[vic_file] = patched
                elif pol_file != vic_file and (f_rel.endswith(norm_pol_rel) or norm_pol_rel.endswith(f_rel)):
                    patched_p = apply_single_file_diff_hunk(orig_pol, sec)
                    if patched_p is not None:
                        applied_test_files[pol_file] = patched_p
                elif f_rel in info["test_files"] and os.path.exists(f_abs):
                    orig_other = open(f_abs, encoding="utf-8", errors="replace").read()
                    patched_o = apply_single_file_diff_hunk(orig_other, sec)
                    if patched_o is not None:
                        applied_test_files[f_abs] = patched_o

            all_test_files_applied = (
                len(info["test_files"]) > 0
                and len(applied_test_files) == len(info["test_files"])
                and vic_file in applied_test_files
            )
            pr_manifest_entry = {
                "sample_id": sid,
                "project_name": sample["project_name"],
                "benchmark_sha": sample["sha"],
                "pr_url": sample["pr_url"],
                "idoft_status": sample.get("pr_status") or "UNKNOWN",
                "pr_diff_sha256": info["pr_diff_sha256"],
                "pr_scope_type": info["pr_scope_type"],
                "all_files_in_pr": info["all_files"],
                "test_files_in_pr": info["test_files"],
                "prod_files_in_pr": info["prod_files"],
                "build_files_in_pr": info["build_files"],
                "applied_test_files_count": len(applied_test_files),
                "all_test_files_applied": all_test_files_applied,
                "pr_apply_outcome": (
                    "CLEAN_FULL_TEST_APPLY" if all_test_files_applied
                    else ("PARTIAL_TEST_APPLY_FAILED" if len(applied_test_files) > 0 else "ZERO_FILES_APPLIED")
                ),
            }

            if all_test_files_applied:
                backups_extra = {fp: open(fp, "rb").read() for fp in applied_test_files}
                mod_dir = pdir if mod_rel in ("", ".") else os.path.join(pdir, mod_rel)
                vbak = rc.backup_class_files(mod_dir, sample["victim_fqcn"])
                pbak = rc.backup_class_files(mod_dir, sample["polluter_fqcn"]) if sample.get("polluter_fqcn") else {}
                applied_victim_code = rc.extract_java_method(applied_test_files[vic_file], sample["victim_method"]) or ""
                applied_pol_code = (
                    rc.extract_java_method(applied_test_files.get(pol_file, orig_pol), sample["polluter_method"])
                    if (ttype == "OD" and sample.get("polluter_method")) else ""
                ) or ""
                tree_hasher = hashlib.sha256()
                for fp_k in sorted(applied_test_files.keys()):
                    tree_hasher.update(fp_k.encode("utf-8") + b"\0" + applied_test_files[fp_k].encode("utf-8") + b"\0")
                applied_tree_sha = tree_hasher.hexdigest()[:24]
                pr_manifest_entry["applied_tree_sha256"] = applied_tree_sha
                pr_manifest_entry["applied_victim_code"] = applied_victim_code
                pr_manifest_entry["applied_polluter_code"] = applied_pol_code
                try:
                    for fp, new_txt in applied_test_files.items():
                        open(fp, "w", encoding="utf-8").write(new_txt)
                    pl = f"-pl {mod_rel}" if mod_rel not in ("", ".") else ""
                    with DOCKER_SEM:
                        _, out_c, _ = rc.docker_mvn(pdir, jdk, f"test-compile {pl}", timeout=120)
                    comp_ok = "BUILD SUCCESS" in out_c
                    w_ok, _, _ = (False, "", 0.0)
                    w_ho_ok = False
                    pr_3x_sets: List[Set[str]] = []
                    if comp_ok:
                        with DOCKER_SEM:
                            w_ok, _, _ = rc.check_stability_w_plus(
                                pdir, mod_rel, jdk, sample["uses_junit5"], ttype,
                                sample["victim_fqcn"], sample["victim_method"],
                                sample.get("polluter_fqcn", ""), sample.get("polluter_method", ""),
                            )
                        w_ho_ok, _, _ = check_java_w_plus_heldout(sample)
                        for _ in range(3):
                            pok_pr, pmuts_pr, _, _ = run_attributed_java_pit(
                                proj_dir=pdir, mod_rel=mod_rel, jdk=jdk,
                                test_file_path=vic_file, test_fqcn=sample["victim_fqcn"],
                                test_method=sample["victim_method"], focal_classes=sample["focal_classes"],
                                pit_pair=pit_pair, mutators_arg="DEFAULTS", timeout=120,
                            )
                            if pok_pr:
                                pr_3x_sets.append({m["id"] for m in pmuts_pr if m["attributed_kill"]})
                    if len(pr_3x_sets) == 3 and len(v2_m_det) > 0:
                        pr_det_kill = pr_3x_sets[0] & pr_3x_sets[1] & pr_3x_sets[2]
                        pr_union_kill = pr_3x_sets[0] | pr_3x_sets[1] | pr_3x_sets[2]
                        kept_det = [m["id"] for m in v2_m_det if m["id"] in pr_det_kill]
                        lost_det = [m for m in v2_m_det if m["id"] not in pr_det_kill]
                        ret_3x = round(len(kept_det) / len(v2_m_det), 4)
                        ret_union = round(sum(1 for m in v2_m_det if m["id"] in pr_union_kill) / len(v2_m_det), 4)
                        if sample.get("pr_status") == "Accepted" and info["pr_scope_type"] == "TEST_ONLY_PR":
                            v2_m_gold_ids = kept_det
                        v2_dev_pr_eval = {
                            "pr_url": sample["pr_url"],
                            "idoft_status": sample.get("pr_status") or "UNKNOWN",
                            "pr_scope_type": info["pr_scope_type"],
                            "pr_apply_outcome": "CLEAN_FULL_TEST_APPLY",
                            "applied_tree_sha256": applied_tree_sha,
                            "applied_victim_code": applied_victim_code,
                            "applied_polluter_code": applied_pol_code,
                            "prod_files_in_pr": info["prod_files"],
                            "compiled": comp_ok,
                            "w_plus_passed": w_ok,
                            "w_plus_heldout_passed": w_ho_ok,
                            "m_det_total": len(v2_m_det),
                            "m_det_killed_3x": len(kept_det),
                            "retention_m_det_3x": ret_3x,
                            "retention_m_det_union": ret_union,
                            "lost_mutants": [
                                {"id": lm["id"], "line": lm["lineNumber"], "mutator": lm["mutator"].split(".")[-1], "desc": lm["description"]}
                                for lm in lost_det
                            ],
                            "applied_victim_diff": compute_unified_diff(orig_vic, applied_test_files[vic_file], sample["victim_file_rel"]),
                        }
                        pr_manifest_entry.update({
                            "compiled": comp_ok,
                            "w_plus_passed": w_ok,
                            "w_plus_heldout_passed": w_ho_ok,
                            "retention_m_det_3x": ret_3x,
                            "retention_m_det_union": ret_union,
                            "lost_mutant_count_3x": len(lost_det),
                        })
                    else:
                        pr_manifest_entry.update({"compiled": comp_ok, "pit_3x_completed": False})
                finally:
                    for fp, raw_b in backups_extra.items():
                        open(fp, "wb").write(raw_b)
                    rc.restore_class_files(mod_dir, sample["victim_fqcn"], vbak)
                    if pbak:
                        rc.restore_class_files(mod_dir, sample["polluter_fqcn"], pbak)
                    if len(applied_test_files) > 1:
                        # Ensure any extra modified helper/test .class bytecode is cleanly recompiled back to baseline
                        pl_rst = f"-pl {mod_rel}" if mod_rel not in ("", ".") else ""
                        with DOCKER_SEM:
                            rc.docker_mvn(pdir, jdk, f"test-compile {pl_rst}", timeout=120)

    # 5. Authoritative B1 (`FlakyDoctor` GPT-4 & Magicoder) Audit
    b1_entries: Dict[str, Any] = {}
    sample_tmp = dict(sample)
    sample_tmp["m_det"] = v2_m_det
    sample_tmp["polluter_m_det"] = v2_pol_m_det
    sample_tmp["m_gold_ids"] = v2_m_gold_ids

    for tag, p_field in (("gpt", "b1_gpt_patch_path"), ("magicoder", "b1_magicoder_patch_path")):
        csv_rec = upstream["fd_csv"][tag].get((sample["sha"].lower(), sample["test_full_name"]))
        old_ppath = sample.get(p_field) or ""
        auth_ppath = csv_rec["csv_patch_abs"] if (csv_rec and csv_rec["has_official_P"] and csv_rec["csv_patch_abs"]) else ""
        used_ppath = auth_ppath or old_ppath
        if not used_ppath or not os.path.exists(used_ppath):
            continue
        raw_bytes = open(used_ppath, "rb").read()
        p_sha = sha256_hex(raw_bytes)[:24]
        is_official_p = bool(csv_rec and csv_rec["has_official_P"] and auth_ppath and os.path.abspath(used_ppath) == os.path.abspath(auth_ppath))

        parsed = rc.parse_flakydoctor_patch_file(used_ppath, ttype, sample["victim_method"], sample.get("polluter_method", ""))
        b1_rec: Dict[str, Any] = {
            "sample_id": sid,
            "tool": f"FlakyDoctor_{tag}",
            "in_official_csv_P": is_official_p,
            "patch_rel_path": os.path.relpath(used_ppath, rc.FD_ROOT),
            "patch_sha256": p_sha,
            "stage_outcome": "UNPARSED",
        }
        if not parsed:
            b1_rec["stage_outcome"] = "APPLICATOR_FORMAT_UNSUPPORTED"
            b1_entries[tag] = b1_rec
            continue

        # Check declared method name mismatch (fixes `visualee::DescriptionTest` bug!)
        mismatch_err = ""
        if ttype == "ID" and parsed.get("test_code"):
            decl = extract_declared_java_method_name(parsed["test_code"])
            if decl and decl != sample["victim_method"]:
                mismatch_err = f"Declared `{decl}` != target `{sample['victim_method']}`"
        elif ttype == "OD":
            if parsed.get("victim_code"):
                decl_v = extract_declared_java_method_name(parsed["victim_code"])
                if decl_v and decl_v != sample["victim_method"]:
                    if decl_v == sample.get("polluter_method") and not parsed.get("polluter_code"):
                        parsed["polluter_code"] = parsed["victim_code"]
                        parsed["victim_code"] = None
                    else:
                        mismatch_err = f"Declared victim `{decl_v}` != `{sample['victim_method']}` (third-class cleaner)"
            if parsed.get("polluter_code"):
                decl_p = extract_declared_java_method_name(parsed["polluter_code"])
                if decl_p and sample.get("polluter_method") and decl_p != sample["polluter_method"]:
                    mismatch_err = (mismatch_err + "; " if mismatch_err else "") + f"Declared polluter `{decl_p}` != `{sample['polluter_method']}`"

        if mismatch_err:
            b1_rec["stage_outcome"] = "APPLICATOR_THIRD_CLASS_MISMATCH"
            b1_rec["mismatch_detail"] = mismatch_err
            b1_entries[tag] = b1_rec
            continue

        # Evaluate STRICT VERBATIM through unified v2 evaluator (`evaluate_java_v2_in_loop`)!
        from remote_eval_v2 import evaluate_java_v2_in_loop
        cand_verb = dict(parsed)
        cand_verb["allow_static_assert_import_salvage"] = False
        ev_v = evaluate_java_v2_in_loop(sample_tmp, cand_verb)
        if not ev_v["compiled"]:
            # Optional diagnostic: check whether adding missing static JUnit imports alone would allow compilation
            cand_salv = dict(parsed)
            extra_imps = list(cand_salv.get("imports") or [])
            extra_imps.extend(["import static org.junit.Assert.*;", "import static org.junit.jupiter.api.Assertions.*;"])
            cand_salv["imports"] = extra_imps
            ev_s = evaluate_java_v2_in_loop(sample_tmp, cand_salv)
            if ev_s["compiled"]:
                b1_rec["salvage_compile_would_pass"] = True
                b1_rec["salvage_w_plus"] = ev_s["w_plus_passed"]
                b1_rec["salvage_eroded"] = ev_s["eroded"]
                b1_rec["salvage_sound_fix"] = ev_s["sound_fix"]

        if not ev_v["compiled"]:
            b1_rec["stage_outcome"] = "VERBATIM_COMPILE_FAIL"
        elif not ev_v["w_plus_passed"]:
            b1_rec["stage_outcome"] = "W_PLUS_FAIL"
        elif ev_v.get("gate_status_override") in ("PARTNER_MUT_EVAL_ERROR", "TARGET_MUT_UNKNOWN", "PARTNER_MUT_UNKNOWN"):
            b1_rec["stage_outcome"] = ev_v["gate_status_override"]
        elif ev_v.get("gate_status_override") == "ABSTAIN_PARTNER_UNCOVERED":
            b1_rec["stage_outcome"] = "ABSTAIN_PARTNER_UNCOVERED"
        elif not ev_v["pit_ran"]:
            b1_rec["stage_outcome"] = "MUTATION_EVAL_FAIL"
        elif ev_v["eroded"]:
            b1_rec["stage_outcome"] = "ERODED_W_PLUS_PASS"
        else:
            b1_rec["stage_outcome"] = "OPERATIONAL_SOUND_FIX"

        b1_rec.update({
            "evaluator_version": ev_v.get("harness_version", HARNESS_VERSION),
            "compiled": ev_v["compiled"],
            "w_plus_passed": ev_v["w_plus_passed"],
            "pit_ran": ev_v["pit_ran"],
            "scope_kind": ev_v.get("scope_kind", "TARGET_ONLY"),
            "affects_partner_or_shared": ev_v.get("affects_partner_or_shared", False),
            "gate_status_override": ev_v.get("gate_status_override"),
            "gate_safe": ev_v.get("gate_safe", False),
            "eroded": ev_v["eroded"],
            "sound_fix": ev_v["sound_fix"],
            "target_retention": ev_v.get("target_retention"),
            "partner_retention": ev_v.get("partner_retention"),
            "joint_retention": ev_v.get("joint_retention"),
            "retention_m_det": ev_v["retention_m_det"],
            "retention_m_gold": ev_v["retention_m_gold"],
            "survived_count": len(ev_v.get("survived_mutants", [])),
            "target_unknown_count": ev_v.get("target_unknown_count", 0),
            "partner_unknown_count": ev_v.get("partner_unknown_count", 0),
            "eval": ev_v,
        })
        b1_entries[tag] = b1_rec

    out_s = dict(sample)
    out_s["harness_version"] = HARNESS_VERSION
    out_s["v1_m_det_count"] = sample["m_det_count"]
    out_s["m_det"] = v2_m_det
    out_s["m_det_count"] = len(v2_m_det)
    out_s["v2_eligible"] = len(v2_m_det) >= 3
    out_s["polluter_m_det"] = v2_pol_m_det
    out_s["polluter_m_det_count"] = len(v2_pol_m_det)
    out_s["m_ext_op_value_flow"] = m_ext_val_flow
    out_s["m_ext_op_call"] = m_ext_call
    out_s["m_ext_total_count"] = len(m_ext_val_flow) + len(m_ext_call)
    out_s["java_mext_mutators_used"] = mext_mut_flag_used
    out_s["pit_all_3x_secs"] = pit_all_secs
    out_s["m_gold_ids"] = v2_m_gold_ids
    out_s["v2_dev_pr_eval"] = v2_dev_pr_eval
    out_s["v2_pr_manifest"] = pr_manifest_entry
    out_s["v2_b1_audit"] = b1_entries
    log(
        f"[JAVA V2 {sid}] eligible={out_s['v2_eligible']} |M_det|={len(v2_m_det)}/{sample['m_det_count']} "
        f"|P_det|={len(v2_pol_m_det)} |M_ext_vf|={len(m_ext_val_flow)} |M_ext_call|={len(m_ext_call)}"
    )
    return out_s


def audit_and_freeze_py_sample(sample: Dict[str, Any], upstream: Dict[str, Any]) -> Dict[str, Any]:
    sid = sample["sample_id"]
    pdir = sample["proj_dir"]
    od_sub = sample["od_subtype"]
    t_node = sample["test_full_name"]
    p_node = sample["polluter_full_name"]
    e0_args = [p_node, t_node] if od_sub == "OD-brittle" else [t_node]
    p_hint = p_node if od_sub == "OD-brittle" else ""

    # 1. Re-verify `m_det_exec` with 3/3 node-and-phase attribution (`PY_V2_ATTRIBUTED_MUT_RUNNER`)
    m_det_exec_v1 = sample.get("m_det_exec", [])
    det_runs: List[Dict[str, Dict[str, Any]]] = []
    for _ in range(3):
        ok, det_map, _ = run_attributed_py_mut(pdir, e0_args, t_node, m_det_exec_v1, timeout=110, partner_node_hint=p_hint)
        if not ok:
            break
        det_runs.append(det_map)

    valid_ids = set()
    kill_kind_by_id: Dict[str, str] = {}
    if len(det_runs) == 3:
        for m in m_det_exec_v1:
            mid = m["id"]
            r0, r1, r2 = det_runs[0].get(mid, {}), det_runs[1].get(mid, {}), det_runs[2].get(mid, {})
            if r0.get("killed") and r1.get("killed") and r2.get("killed"):
                valid_ids.add(mid)
                kill_kind_by_id[mid] = r0.get("status", "KILLED_CALL_ASSERT")

    # Strict OD-brittle setter check: setter MUST explicitly `SURVIVED_PASS` with `tests_ran >= 1`!
    if od_sub == "OD-brittle" and valid_ids:
        cand_sub = [m for m in m_det_exec_v1 if m["id"] in valid_ids]
        ok_s, set_map, _ = run_attributed_py_mut(pdir, [p_node], p_node, cand_sub, timeout=95)
        if ok_s:
            valid_ids = {
                mid for mid in valid_ids
                if set_map.get(mid, {}).get("status") == "SURVIVED_PASS" and set_map.get(mid, {}).get("tests_ran", 0) >= 1
            }
        else:
            valid_ids = set()

    v2_m_det_exec = []
    for m in m_det_exec_v1:
        if m["id"] in valid_ids:
            mc = dict(m)
            mc["kill_kind"] = kill_kind_by_id.get(m["id"], "KILLED_CALL_ASSERT")
            mc["src_sha256"] = _src_sha256(m["mutated_file_src"])
            v2_m_det_exec.append(mc)

    # 2. 3/3 Attributed `polluter_m_det` verification (backfilling empty ones if partner kills focal mutants!)
    pol_cands = sample.get("polluter_m_det_exec") or []
    if not pol_cands:
        p_cov = rpc.trace_covered_focal_lines(pdir, "OD-victim", p_node, "")
        if p_cov:
            pol_cands = rpc.generate_focal_py_mutants(pdir, p_cov, max_mutants=20)
    v2_pol_exec: List[Dict[str, Any]] = []
    if pol_cands:
        p_runs = []
        for _ in range(3):
            pok, pmap, _ = run_attributed_py_mut(pdir, [p_node], p_node, pol_cands[:18], timeout=90)
            if not pok:
                break
            p_runs.append(pmap)
        if len(p_runs) == 3:
            for pm in pol_cands[:18]:
                pid = pm["id"]
                if all(pr.get(pid, {}).get("killed") for pr in p_runs):
                    pmc = dict(pm)
                    pmc["kill_kind"] = p_runs[0][pid].get("status", "KILLED_CALL_ASSERT")
                    pmc["src_sha256"] = _src_sha256(pm["mutated_file_src"])
                    v2_pol_exec.append(pmc)
            v2_pol_exec = v2_pol_exec[:12]

    # 3. Construct Frozen Disjoint `M_ext_operator` & `M_ext_instance`
    forb_hashes: Set[str] = {m["src_sha256"] for m in (v2_m_det_exec + v2_pol_exec)}
    cov_files = rpc.trace_covered_focal_lines(pdir, od_sub, t_node, p_node) or {}
    for rf in cov_files:
        fp_abs = os.path.join(pdir, rf)
        if os.path.exists(fp_abs):
            forb_hashes.add(_src_sha256(open(fp_abs, encoding="utf-8", errors="replace").read()))

    op_cands: List[Dict[str, Any]] = []
    for rf, lines_set in sorted(cov_files.items()):
        fp_abs = os.path.join(pdir, rf)
        if not os.path.exists(fp_abs):
            continue
        src_txt = open(fp_abs, encoding="utf-8", errors="replace").read()
        mod_fq = rf.replace("/", ".").replace(".py", "")
        new_ops = generate_py_m_ext_operator_mutants(
            rel_file=rf,
            mod_fqcn=mod_fq,
            src=src_txt,
            covered_lines=set(lines_set),
            forbidden_src_hashes=forb_hashes,
            max_candidates=35 - len(op_cands),
        )
        for nm in new_ops:
            forb_hashes.add(nm["src_sha256"])
        op_cands.extend(new_ops)
        if len(op_cands) >= 35:
            break

    inst_cands_raw = rpc.generate_focal_py_mutants(pdir, cov_files, max_mutants=130) if cov_files else []
    inst_cands: List[Dict[str, Any]] = []
    for im in inst_cands_raw:
        h = _src_sha256(im["mutated_file_src"])
        if h in forb_hashes:
            continue
        forb_hashes.add(h)
        imc = dict(im)
        imc["id"] = "MEXT_INST::" + im["id"]
        imc["pool"] = "M_ext_instance"
        imc["src_sha256"] = h
        inst_cands.append(imc)
        if len(inst_cands) >= 30:
            break

    def _filter_3x_mext(cands: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not cands:
            return []
        runs = []
        for _ in range(3):
            ok_x, dmap, _ = run_attributed_py_mut(pdir, e0_args, t_node, cands, timeout=115, partner_node_hint=p_hint)
            if not ok_x:
                return []
            runs.append(dmap)
        surv_ids = {
            c["id"] for c in cands
            if all(r.get(c["id"], {}).get("killed") for r in runs)
        }
        if od_sub == "OD-brittle" and surv_ids:
            sub_c = [c for c in cands if c["id"] in surv_ids]
            ok_bs, bs_map, _ = run_attributed_py_mut(pdir, [p_node], p_node, sub_c, timeout=95)
            if not ok_bs:
                return []
            surv_ids = {
                cid for cid in surv_ids
                if bs_map.get(cid, {}).get("status") == "SURVIVED_PASS" and bs_map.get(cid, {}).get("tests_ran", 0) >= 1
            }
        kept = []
        for c in cands:
            if c["id"] in surv_ids:
                cc = dict(c)
                cc["kill_kind"] = runs[0][c["id"]].get("status", "KILLED_CALL_ASSERT")
                kept.append(cc)
        return kept

    v2_mext_op = _filter_3x_mext(op_cands)[:20]
    v2_mext_inst = _filter_3x_mext(inst_cands)[:20]

    # 4. Authoritative B1 (`iPFlakies`) Provenance & Attributed Evaluation
    b1_ipf_rec: Optional[Dict[str, Any]] = None
    ppath = sample.get("b1_ipflakies_patch_path") or ""
    csv_rec = upstream["ipf_csv"].get((sample["project_name"], sample["sha"].lower(), t_node))
    if ppath and os.path.exists(ppath):
        p_sha = sha256_hex(open(ppath, "rb").read())[:24]
        in_csv = bool(csv_rec and csv_rec["csv_patch_abs"] and os.path.abspath(ppath) == os.path.abspath(csv_rec["csv_patch_abs"]))
        partner_matches = bool(csv_rec and csv_rec["partner"] == p_node)
        vic_abs = os.path.join(pdir, sample["victim_file_rel"])
        orig_vic_b = open(vic_abs, "rb").read()
        orig_vic_s = orig_vic_b.decode("utf-8", errors="replace")
        patched_s = rpc.apply_ipflakies_patch_file(orig_vic_s, ppath)
        b1_ipf_rec = {
            "sample_id": sid,
            "tool": "iPFlakies",
            "in_official_patches_csv": in_csv,
            "csv_partner_matches_benchmark": partner_matches,
            "csv_partner": csv_rec["partner"] if csv_rec else "",
            "benchmark_partner": p_node,
            "patch_rel_path": os.path.relpath(ppath, rpc.IPFLAKIES_ROOT),
            "patch_sha256": p_sha,
        }
        if patched_s is None:
            b1_ipf_rec["stage_outcome"] = "APPLICATOR_APPLY_FAIL"
        else:
            try:
                compile(patched_s, sample["victim_file_rel"], "exec")
                comp_ok = True
            except SyntaxError as se:
                comp_ok = False
                b1_ipf_rec["compile_error"] = str(se)
            if not comp_ok:
                b1_ipf_rec["stage_outcome"] = "VERBATIM_COMPILE_FAIL"
                b1_ipf_rec["compiled"] = False
            else:
                try:
                    open(vic_abs, "w", encoding="utf-8").write(patched_s)
                    with DOCKER_SEM:
                        w_ok, _, _ = rpc.check_py_stability_w_plus(pdir, t_node, p_node)
                    if not w_ok:
                        b1_ipf_rec.update({"compiled": True, "w_plus_passed": False, "stage_outcome": "W_PLUS_FAIL"})
                    else:
                        ok_m, dmap_m, _ = run_attributed_py_mut(pdir, [t_node], t_node, v2_m_det_exec, timeout=110)
                        if not ok_m:
                            b1_ipf_rec.update({
                                "compiled": True,
                                "w_plus_passed": True,
                                "target_unknown_count": len(v2_m_det_exec),
                                "partner_unknown_count": 0,
                                "eroded": False,
                                "gate_safe": False,
                                "gate_status_override": "TARGET_MUT_EVAL_ERROR",
                                "stage_outcome": "TARGET_MUT_EVAL_ERROR",
                            })
                        else:
                            k_ids = {mid for mid, inf in dmap_m.items() if inf.get("killed") and not inf.get("is_unknown")}
                            t_unk_ids = [
                                m["id"] for m in v2_m_det_exec
                                if m["id"] not in dmap_m or dmap_m[m["id"]].get("is_unknown", False)
                            ]
                            t_statuses = {
                                m["id"]: (dmap_m[m["id"]].get("status", "UNKNOWN") if m["id"] in dmap_m else "MISSING_REPORT")
                                for m in v2_m_det_exec
                            }
                            same_f = (sample["victim_file_rel"] == sample["polluter_file_rel"])
                            new_pol_fn = rpc.extract_py_func(patched_s, sample.get("polluter_class"), sample["polluter_method"]) if same_f else sample["orig_polluter_code"]
                            new_vic_fn = rpc.extract_py_func(patched_s, sample.get("victim_class"), sample["victim_method"]) or ""
                            sc = analyze_affected_test_scope(
                                "python", "OD", orig_vic_s, patched_s, orig_vic_s, patched_s, same_f,
                                sample["orig_test_code"], new_vic_fn, sample.get("orig_polluter_code", ""), new_pol_fn or "",
                            )
                            p_ran, p_ok, pk_ids = False, True, set()
                            p_unk_ids: List[str] = []
                            p_statuses: Dict[str, str] = {}
                            if sc["affects_partner_or_shared"] and v2_pol_exec:
                                p_ran = True
                                p_ok, pmap_b1, _ = run_attributed_py_mut(pdir, [p_node], p_node, v2_pol_exec, timeout=90)
                                if p_ok:
                                    pk_ids = {pid for pid, pinf in pmap_b1.items() if pinf.get("killed") and not pinf.get("is_unknown")}
                                    p_unk_ids = [
                                        pm["id"] for pm in v2_pol_exec
                                        if pm["id"] not in pmap_b1 or pmap_b1[pm["id"]].get("is_unknown", False)
                                    ]
                                    p_statuses = {
                                        pm["id"]: (pmap_b1[pm["id"]].get("status", "UNKNOWN") if pm["id"] in pmap_b1 else "MISSING_REPORT")
                                        for pm in v2_pol_exec
                                    }
                                else:
                                    p_unk_ids = [pm["id"] for pm in v2_pol_exec]
                            ret_obj = compute_role_tagged_retention(
                                v2_m_det_exec, k_ids, "OD", v2_pol_exec,
                                sc["affects_partner_or_shared"], p_ran, p_ok, pk_ids,
                                target_unknown_ids=t_unk_ids,
                                partner_unknown_ids=p_unk_ids,
                            )
                            if ret_obj["gate_status_override"] in ("TARGET_MUT_UNKNOWN", "PARTNER_MUT_UNKNOWN", "PARTNER_MUT_EVAL_ERROR"):
                                st_out = ret_obj["gate_status_override"]
                            elif ret_obj["eroded"]:
                                st_out = "ERODED_W_PLUS_PASS"
                            elif ret_obj["gate_safe"]:
                                st_out = "OPERATIONAL_SOUND_FIX"
                            else:
                                st_out = ret_obj["gate_status_override"] or "ABSTAIN_OTHER"
                            b1_ipf_rec.update({
                                "harness_version": HARNESS_VERSION,
                                "compiled": True,
                                "w_plus_passed": True,
                                "scope_kind": sc["scope_kind"],
                                "target_unknown_count": len(t_unk_ids),
                                "partner_unknown_count": len(p_unk_ids),
                                "target_mut_statuses": t_statuses,
                                "partner_mut_statuses": p_statuses,
                                "target_retention": ret_obj["target_retention"],
                                "partner_retention": ret_obj["partner_retention"],
                                "joint_retention": ret_obj["joint_retention"],
                                "eroded": ret_obj["eroded"],
                                "gate_safe": ret_obj["gate_safe"],
                                "gate_status_override": ret_obj["gate_status_override"],
                                "stage_outcome": st_out,
                            })
                finally:
                    open(vic_abs, "wb").write(orig_vic_b)

    def _strip(arr: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [{k: v for k, v in m.items() if k != "mutated_file_src"} for m in arr]

    out_s = dict(sample)
    out_s["harness_version"] = HARNESS_VERSION
    out_s["v1_m_det_count"] = sample["m_det_count"]
    out_s["m_det_exec"] = v2_m_det_exec
    out_s["m_det"] = _strip(v2_m_det_exec)
    out_s["m_det_count"] = len(v2_m_det_exec)
    out_s["v2_eligible"] = len(v2_m_det_exec) >= 3
    out_s["polluter_m_det_exec"] = v2_pol_exec
    out_s["polluter_m_det"] = _strip(v2_pol_exec)
    out_s["polluter_m_det_count"] = len(v2_pol_exec)
    out_s["m_ext_operator_exec"] = v2_mext_op
    out_s["m_ext_operator"] = _strip(v2_mext_op)
    out_s["m_ext_instance_exec"] = v2_mext_inst
    out_s["m_ext_instance"] = _strip(v2_mext_inst)
    out_s["m_ext_total_count"] = len(v2_mext_op) + len(v2_mext_inst)
    out_s["v2_b1_audit"] = {"ipflakies": b1_ipf_rec} if b1_ipf_rec else {}
    log(
        f"[PY V2 {sid}] eligible={out_s['v2_eligible']} |M_det|={len(v2_m_det_exec)}/{sample['m_det_count']} "
        f"|P_det|={len(v2_pol_exec)} |M_ext_op|={len(v2_mext_op)} |M_ext_inst|={len(v2_mext_inst)}"
    )
    return out_s


def strip_all_exec_blobs(s: Dict[str, Any]) -> Dict[str, Any]:
    sl = dict(s)
    for k in ("m_det_exec", "polluter_m_det_exec", "m_ext_operator_exec", "m_ext_instance_exec"):
        sl.pop(k, None)
    return sl


def main() -> None:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    j_bench = json.load(open(os.path.join(FULL_DIR, "java_full_benchmark.json"), encoding="utf-8"))
    p_bench = json.load(open(os.path.join(FULL_DIR, "python_full_benchmark_exec.json"), encoding="utf-8"))

    all_in: List[Dict[str, Any]] = []
    for s in j_bench["samples"]:
        sc = dict(s)
        sc["language"] = "java"
        all_in.append(sc)
    for s in p_bench["samples"]:
        sc = dict(s)
        sc["language"] = "python"
        all_in.append(sc)

    if limit > 0:
        # Smoke mode: first `limit` java + first `limit` python
        j_sub = [x for x in all_in if x["language"] == "java"][:limit]
        p_sub = [x for x in all_in if x["language"] == "python"][:limit]
        all_in = j_sub + p_sub

    upstream = build_upstream_indices()
    CKPT_JSONL = os.path.join(FULL_DIR, "v2_freeze_checkpoint.jsonl")
    completed: Dict[str, Dict[str, Any]] = {}
    if limit == 0:
        if os.path.exists(CKPT_JSON):
            try:
                old_ck = json.load(open(CKPT_JSON, encoding="utf-8"))
                for sid_k, rec_v in old_ck.items():
                    # Re-run Java samples that hit `-Dmutators=ALL` timeout (`m_det_count == 0` and `<3` PIT runs)
                    if rec_v.get("language") == "java" and rec_v.get("m_det_count", 0) == 0:
                        continue
                    completed[sid_k] = rec_v
            except Exception:
                pass
        if os.path.exists(CKPT_JSONL):
            for ln in open(CKPT_JSONL, encoding="utf-8", errors="replace"):
                if ln.strip():
                    try:
                        rv = json.loads(ln)
                        completed[rv["sample_id"]] = rv
                    except Exception:
                        pass

    todo = [s for s in all_in if s["sample_id"] not in completed]
    log(f"=== V2 BENCHMARK & M_EXT FREEZE START: total={len(all_in)}, cached={len(completed)}, todo={len(todo)} ===")

    def _run_one(smp: Dict[str, Any]) -> Dict[str, Any]:
        pkey = f"{smp['language']}:{smp['sha']}:{smp['project_name']}"
        with PROJ_LOCKS[pkey]:
            if smp["language"] == "python":
                return audit_and_freeze_py_sample(smp, upstream)
            return audit_and_freeze_java_sample(smp, upstream)

    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(_run_one, s): s["sample_id"] for s in todo}
        for fut in as_completed(futs):
            sid = futs[fut]
            try:
                res = fut.result()
            except Exception as e:
                log(f"[ERROR V2 FREEZE {sid}]: {e}")
                continue
            with SAVE_LOCK:
                completed[sid] = res
                if limit == 0:
                    with open(CKPT_JSONL, "a", encoding="utf-8") as fa:
                        fa.write(json.dumps(res, ensure_ascii=False) + "\n")

    ordered = [completed[s["sample_id"]] for s in all_in if s["sample_id"] in completed]
    if limit > 0:
        log(f"=== SMOKE DONE ({len(ordered)} samples) ===")
        return

    # Save full exec & light benchmarks + PR & B1 manifests + SHA256 digest!
    frozen_ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    light_samples = [strip_all_exec_blobs(s) for s in ordered]

    pr_manifest = [s["v2_pr_manifest"] for s in ordered if s.get("v2_pr_manifest")]
    b1_manifest = []
    for s in ordered:
        for _, brec in (s.get("v2_b1_audit") or {}).items():
            b1_c = {k: v for k, v in brec.items() if k != "eval"}
            b1_manifest.append(b1_c)

    exec_payload = {
        "frozen_at": frozen_ts,
        "harness_version": HARNESS_VERSION,
        "initial_attempted_count": len(ordered),
        "eligible_count": sum(1 for s in ordered if s.get("v2_eligible")),
        "samples": ordered,
    }
    open(OUT_EXEC_JSON, "w", encoding="utf-8").write(json.dumps(exec_payload, ensure_ascii=False))
    exec_sha = sha256_hex(open(OUT_EXEC_JSON, "rb").read())

    light_payload = {
        "frozen_at": frozen_ts,
        "harness_version": HARNESS_VERSION,
        "exec_benchmark_sha256": exec_sha,
        "initial_attempted_count": len(ordered),
        "eligible_count": sum(1 for s in ordered if s.get("v2_eligible")),
        "attrition_samples": [
            {"sample_id": s["sample_id"], "v1_m_det": s["v1_m_det_count"], "v2_m_det": s["m_det_count"]}
            for s in ordered if not s.get("v2_eligible")
        ],
        "samples": light_samples,
    }
    open(OUT_LIGHT_JSON, "w", encoding="utf-8").write(json.dumps(light_payload, indent=2, ensure_ascii=False))
    open(OUT_PR_MANIFEST, "w", encoding="utf-8").write(json.dumps({"frozen_at": frozen_ts, "entries": pr_manifest}, indent=2, ensure_ascii=False))
    open(OUT_B1_MANIFEST, "w", encoding="utf-8").write(json.dumps({"frozen_at": frozen_ts, "entries": b1_manifest}, indent=2, ensure_ascii=False))
    log(f"=== V2 FREEZE COMPLETE: eligible={light_payload['eligible_count']}/{len(ordered)} sha256={exec_sha[:16]} ===")


if __name__ == "__main__":
    main()
