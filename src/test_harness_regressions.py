"""
Regression Test Suite for FT-1 Harness v2.0.0-frozen (`codex_round2_reply.md` Line 331).
Verifies real failure modes across all 10 required vulnerability classes BEFORE batch execution:
  1. Multi-method patch & declared method-name mismatch detection
  2. Class-level fixture / field / helper modification triggering affected-scope partner protection
  3. Partner mutation evaluation failure (`not partner_eval_ok`) -> NEVER `SAFE_PASS`
  4. Empty partner obligations (`|M_det(partner)| == 0`) with partner/fixture edit -> `ABSTAIN_PARTNER_UNCOVERED`
  5. Multiple mutants on the same line + cross-file identical line numbers in Top-k dispersion
  6. Partner-only erosion (`10/10` target, `4/5` partner) -> `joint_retention = 14/15` (not `10/11`), exact Top-k behavior
  7. Zero tests executed (`pytest.skip` / unmatched node) & `setup` failure -> NEVER counted as `call`-phase kill (real `pytest` execution!)
  8. Expected exception NOT raised (`pytest.raises` block passes on mutant) vs `AssertionError` vs unexpected `ZeroDivisionError` (real `pytest` execution!)
  9. `B3` bypasses caught by `B3+` (`2 assertEquals -> 1 assertEquals + 1 assertNotNull`, `size()/len()` projection downgrade, dual-SUT tautology `assertEquals(f(x), f(y))`, SUT call removal, post-act `.clear()` clobber) while accepting legitimate multi-field / pre-act cleanup fixes
 10. Matched witness payload count/structure (`A1 / A2 / A3`) and `M_ext_operator` SHA-256 non-overlap
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ast_guard_and_prompts import check_static_ast_guard
from python_ast_guard_and_prompts import check_py_static_ast_guard
from v2_guards_and_attribution import (
    analyze_affected_test_scope,
    build_matched_ablation_feedback,
    check_java_b3_plus,
    check_py_b3_plus,
    compute_role_tagged_retention,
    compute_v2_topk_sensitivity,
    extract_declared_java_method_name,
    is_code_semantically_changed,
    match_java_pit_killing_test,
)
from remote_core import isolate_single_test_method
from v2_pytest_attribution_runner import (
    PY_V2_ATTRIBUTED_MUT_RUNNER,
    _src_sha256,
    generate_py_m_ext_operator_mutants,
)


class TestFT1V2HarnessRegressions(unittest.TestCase):

    def test_01_declared_method_mismatch_and_multimethod(self):
        # Simulates visualee DescriptionTest bug where B1 patch contained `testIsOkIncludeTrue`
        vic_patch = """
        public void testIsOkIncludeTrue() {
            FilterContainer.getInstance().clear();
            assertEquals(true, res);
        }
        """
        decl = extract_declared_java_method_name(vic_patch)
        self.assertEqual(decl, "testIsOkIncludeTrue")
        self.assertNotEqual(decl, "testGenerateDescription2")

    def test_02_class_level_fixture_change_triggers_affected_scope(self):
        orig_cls = """
        import org.junit.Before;
        import org.junit.Test;
        public class SampleTest {
            private int sharedCount = 1;
            @Before public void setUp() { sharedCount = 1; }
            @Test public void testVictim() { assertEquals(1, sharedCount); }
            @Test public void testPolluter() { sharedCount = 2; assertEquals(2, sharedCount); }
        }
        """
        # Candidate leaves `testPolluter` method untouched, but modifies `@Before setUp()`!
        new_cls = orig_cls.replace("sharedCount = 1; }", "sharedCount = 0; }")
        sc = analyze_affected_test_scope(
            language="java",
            test_type="OD",
            orig_vic_file_src=orig_cls,
            new_vic_file_src=new_cls,
            orig_pol_file_src=orig_cls,
            new_pol_file_src=new_cls,
            same_file=True,
            orig_target_method_src="@Test public void testVictim() { assertEquals(1, sharedCount); }",
            new_target_method_src="@Test public void testVictim() { assertEquals(1, sharedCount); }",
            orig_partner_method_src="@Test public void testPolluter() { sharedCount = 2; assertEquals(2, sharedCount); }",
            new_partner_method_src="@Test public void testPolluter() { sharedCount = 2; assertEquals(2, sharedCount); }",
        )
        self.assertEqual(sc["scope_kind"], "SHARED_FIXTURE_OR_HELPER_MODIFIED")
        self.assertTrue(sc["affects_partner_or_shared"])

        # Pure import + target-only edit should NOT flag partner/shared scope
        target_only_cls = "import java.util.TreeMap;\n" + orig_cls.replace(
            "assertEquals(1, sharedCount);", "assertEquals(1, sharedCount + 0);"
        )
        sc2 = analyze_affected_test_scope(
            language="java",
            test_type="OD",
            orig_vic_file_src=orig_cls,
            new_vic_file_src=target_only_cls,
            orig_pol_file_src=orig_cls,
            new_pol_file_src=target_only_cls,
            same_file=True,
            orig_target_method_src="@Test public void testVictim() { assertEquals(1, sharedCount); }",
            new_target_method_src="@Test public void testVictim() { assertEquals(1, sharedCount + 0); }",
            orig_partner_method_src="@Test public void testPolluter() { sharedCount = 2; assertEquals(2, sharedCount); }",
            new_partner_method_src="@Test public void testPolluter() { sharedCount = 2; assertEquals(2, sharedCount); }",
        )
        self.assertEqual(sc2["scope_kind"], "TARGET_ONLY")
        self.assertFalse(sc2["affects_partner_or_shared"])

    def test_03_partner_eval_error_never_safe_passes(self):
        t_muts = [{"id": f"m{i}"} for i in range(5)]
        p_muts = [{"id": f"p{i}"} for i in range(3)]
        out = compute_role_tagged_retention(
            target_m_det=t_muts,
            target_killed_ids={f"m{i}" for i in range(5)},
            test_type="OD",
            partner_m_det=p_muts,
            affects_partner=True,
            partner_eval_ran=True,
            partner_eval_ok=False,  # Partner PIT/mut runner crashed or timed out!
            partner_killed_ids=None,
        )
        self.assertFalse(out["gate_safe"])
        self.assertEqual(out["gate_status_override"], "PARTNER_MUT_EVAL_ERROR")
        self.assertIsNone(out["joint_retention"])

    def test_04_empty_partner_obligations_with_partner_edit_abstains(self):
        t_muts = [{"id": f"m{i}"} for i in range(4)]
        out = compute_role_tagged_retention(
            target_m_det=t_muts,
            target_killed_ids={f"m{i}" for i in range(4)},
            test_type="OD",
            partner_m_det=[],  # Empty partner obligations!
            affects_partner=True,  # Candidate modified partner/shared scope!
            partner_eval_ran=False,
            partner_eval_ok=False,
            partner_killed_ids=None,
        )
        self.assertFalse(out["gate_safe"])
        self.assertEqual(out["gate_status_override"], "ABSTAIN_PARTNER_UNCOVERED")

    def test_05_and_06_partner_only_erosion_and_cross_file_same_line_topk(self):
        # Target kills 10/10, Partner kills 4/5 (even when m0 shares mutant id with target!)
        t_muts = [{"id": f"m{i}", "rel_file": "pkg/FileA.py", "lineNumber": 42, "mutator": "BinOp", "description": "+"} for i in range(10)]
        p_muts = [
            {"id": "m0", "rel_file": "pkg/FileB.py", "lineNumber": 42, "mutator": "BinOp", "description": "+"},
            {"id": "p1", "rel_file": "pkg/FileB.py", "lineNumber": 42, "mutator": "Compare", "description": "=="},
            {"id": "p2", "rel_file": "pkg/FileB.py", "lineNumber": 50, "mutator": "Compare", "description": "=="},
            {"id": "p3", "rel_file": "pkg/FileB.py", "lineNumber": 55, "mutator": "Return", "description": "ret"},
            {"id": "p4", "rel_file": "pkg/FileB.py", "lineNumber": 60, "mutator": "Return", "description": "ret"},
        ]
        ret = compute_role_tagged_retention(
            target_m_det=t_muts,
            target_killed_ids={f"m{i}" for i in range(10)},
            test_type="OD",
            partner_m_det=p_muts,
            affects_partner=True,
            partner_eval_ran=True,
            partner_eval_ok=True,
            partner_killed_ids={"m0", "p2", "p3", "p4"},  # `p1` survived on FileB.py:L42!
        )
        self.assertFalse(ret["gate_safe"])
        self.assertTrue(ret["eroded"])
        self.assertEqual(ret["target_retention"], 1.0)
        self.assertEqual(ret["partner_retention"], 0.8)
        # Joint retention MUST be 14/15 = 0.9333 (NOT old buggy 10/11 = 0.9091, and `m0` counted for both roles!)
        self.assertEqual(ret["joint_m_det_total"], 15)
        self.assertEqual(ret["joint_m_det_killed"], 14)
        self.assertAlmostEqual(ret["joint_retention"], round(14 / 15, 4))

        # Top-k check: when `include_partner_in_pool=False` (target-only pool), recall MUST be 0.0% even at `All`!
        patch_rec = {
            "target_m_det": t_muts,
            "partner_m_det": p_muts,
            "survived_role_ids": ["partner::p1"],
        }
        tk_target_only = compute_v2_topk_sensitivity([patch_rec], k_budgets=(2, 5), include_partner_in_pool=False)
        self.assertEqual(tk_target_only["rows"][-1]["budget_label"], "All")
        self.assertEqual(tk_target_only["rows"][-1]["uniform_random_recall_pct"], 0.0)

        # When `include_partner_in_pool=True`, FileA:L42 and FileB:L42 are distinct `(role, file, line)` keys,
        # and `All` achieves 100.0% without hardcoding!
        tk_joint = compute_v2_topk_sensitivity([patch_rec], k_budgets=(2, 6), include_partner_in_pool=True)
        self.assertEqual(tk_joint["rows"][-1]["uniform_random_recall_pct"], 100.0)
        # At k=6, dispersion visits all 5 distinct (role, file, line) buckets + 2nd item on FileB:L42 (`p1`) -> 100% hit!
        k6_row = [r for r in tk_joint["rows"] if r["k"] == 6][0]
        self.assertEqual(k6_row["file_line_dispersion_recall_pct"], 100.0)

    def test_07_and_08_real_pytest_zero_tests_setup_fail_and_expected_exc(self):
        # Execute `PY_V2_ATTRIBUTED_MUT_RUNNER` in a real temp directory with `pytest`!
        with tempfile.TemporaryDirectory() as tmpd:
            sut_p = os.path.join(tmpd, "sut.py")
            test_p = os.path.join(tmpd, "test_sut.py")
            runner_p = os.path.join(tmpd, "run_v2.py")
            spec_p = os.path.join(tmpd, "spec.json")

            orig_sut = (
                "def compute(x):\n"
                "    if x < 0:\n"
                "        raise ValueError('neg')\n"
                "    return x + 10\n"
            )
            open(sut_p, "w", encoding="utf-8").write(orig_sut)
            open(test_p, "w", encoding="utf-8").write(
                "import pytest\n"
                "from sut import compute\n\n"
                "def test_val():\n"
                "    assert compute(5) == 15\n\n"
                "def test_expected_exc():\n"
                "    with pytest.raises(ValueError):\n"
                "        compute(-1)\n\n"
                "@pytest.mark.skip(reason='skipped')\n"
                "def test_skipped():\n"
                "    assert compute(5) == 15\n\n"
                "@pytest.fixture\n"
                "def broken_fix():\n"
                "    raise RuntimeError('setup boom')\n\n"
                "def test_setup_broken(broken_fix):\n"
                "    assert compute(5) == 15\n"
            )
            open(runner_p, "w", encoding="utf-8").write(PY_V2_ATTRIBUTED_MUT_RUNNER)

            mut_val = "def compute(x):\n    if x < 0:\n        raise ValueError('neg')\n    return x + 99\n"
            mut_no_raise = "def compute(x):\n    return x + 10\n"
            mut_crash = "def compute(x):\n    return 1 / 0\n"

            # Case A: `test_skipped` -> MUST be `INVALID_ZERO_TESTS_OR_SKIPPED` (killed=False)
            # Case B: `test_setup_broken` -> MUST be `INVALID_SETUP_FAIL` (killed=False, even though pytest rc==1!)
            for bad_node, exp_status in [
                ("test_sut.py::test_skipped", "INVALID_ZERO_TESTS_OR_SKIPPED"),
                ("test_sut.py::test_setup_broken", "INVALID_SETUP_FAIL"),
            ]:
                open(spec_p, "w", encoding="utf-8").write(json.dumps({
                    "pytest_args": [bad_node],
                    "target_node_hint": bad_node,
                    "mutants": [{"id": "m_val", "rel_file": "sut.py", "mutated_file_src": mut_val}],
                }))
                p = subprocess.run([sys.executable, "run_v2.py", "spec.json"], cwd=tmpd, capture_output=True, text=True, timeout=30)
                self.assertEqual(p.returncode, 0, msg=p.stderr)
                payload = json.loads(p.stdout.split("===FT1_PY_V2_MUT_START===")[1].split("===FT1_PY_V2_MUT_END===")[0].strip())
                self.assertEqual(payload["killed_ids"], [])
                self.assertEqual(payload["details"]["m_val"]["status"], exp_status)

            # Case C: `test_expected_exc` when expected ValueError is NOT raised -> `KILLED_CALL_ASSERT`!
            open(spec_p, "w", encoding="utf-8").write(json.dumps({
                "pytest_args": ["test_sut.py::test_expected_exc"],
                "target_node_hint": "test_sut.py::test_expected_exc",
                "mutants": [{"id": "m_noraise", "rel_file": "sut.py", "mutated_file_src": mut_no_raise}],
            }))
            p2 = subprocess.run([sys.executable, "run_v2.py", "spec.json"], cwd=tmpd, capture_output=True, text=True, timeout=30)
            pay2 = json.loads(p2.stdout.split("===FT1_PY_V2_MUT_START===")[1].split("===FT1_PY_V2_MUT_END===")[0].strip())
            self.assertEqual(pay2["killed_ids"], ["m_noraise"])
            self.assertEqual(pay2["details"]["m_noraise"]["status"], "KILLED_CALL_ASSERT")

            # Case D: `test_val` on assertion fail vs runtime ZeroDivisionError -> distinguishes `KILLED_CALL_ASSERT` vs `KILLED_CALL_EXC`!
            open(spec_p, "w", encoding="utf-8").write(json.dumps({
                "pytest_args": ["test_sut.py::test_val"],
                "target_node_hint": "test_sut.py::test_val",
                "mutants": [
                    {"id": "m_val", "rel_file": "sut.py", "mutated_file_src": mut_val},
                    {"id": "m_crash", "rel_file": "sut.py", "mutated_file_src": mut_crash},
                ],
            }))
            p3 = subprocess.run([sys.executable, "run_v2.py", "spec.json"], cwd=tmpd, capture_output=True, text=True, timeout=30)
            pay3 = json.loads(p3.stdout.split("===FT1_PY_V2_MUT_START===")[1].split("===FT1_PY_V2_MUT_END===")[0].strip())
            self.assertEqual(pay3["details"]["m_val"]["status"], "KILLED_CALL_ASSERT")
            self.assertEqual(pay3["details"]["m_crash"]["status"], "KILLED_CALL_EXC")

    def test_09_b3_bypasses_caught_by_b3_plus_and_valid_fixes_allowed(self):
        # 9a: `2 assertEquals -> 1 assertEquals + 1 assertNotNull` bypassed old B3, MUST be caught by B3+!
        orig_j = {"victim": "public void t() { Res r = svc.compute(x); assertEquals(10, r.a()); assertEquals(20, r.b()); }"}
        bypass_j = {"victim": "public void t() { Res r = svc.compute(x); assertEquals(10, r.a()); assertNotNull(r.b()); }"}
        b3_ok, _ = check_static_ast_guard(orig_j, bypass_j)
        self.assertTrue(b3_ok, "Confirming old B3 had this exact blind spot")
        b3p_ok, reasons = check_java_b3_plus(orig_j, bypass_j)
        self.assertFalse(b3p_ok)
        self.assertTrue(any(r["code"] in ("R_PROJECTION_OR_WEAK_DOWNGRADE", "R_STRONG_PREDICATE_DROP") for r in reasons))

        # 9b: Object equality downgraded to `.size()` equality bypassed old B3, caught by B3+!
        orig_j2 = {"victim": "public void t() { List<String> r = svc.items(); assertEquals(expected, r); }"}
        size_j2 = {"victim": "public void t() { List<String> r = svc.items(); assertEquals(expected.size(), r.size()); }"}
        self.assertTrue(check_static_ast_guard(orig_j2, size_j2)[0])
        b3p_ok2, reasons2 = check_java_b3_plus(orig_j2, size_j2)
        self.assertFalse(b3p_ok2)
        self.assertEqual(reasons2[0]["code"], "R_PROJECTION_OR_WEAK_DOWNGRADE")

        # 9c: hippo4j dual-SUT-call tautology `assertEquals(ContentUtil.getPoolContent(a), ContentUtil.getPoolContent(b))` caught!
        orig_hippo = {"victim": 'public void t() { assertEquals("{\\"k\\":1}", ContentUtil.getPoolContent(p)); }'}
        dual_hippo = {"victim": "public void t() { assertEquals(ContentUtil.getPoolContent(expectedP), ContentUtil.getPoolContent(p)); }"}
        b3p_ok3, reasons3 = check_java_b3_plus(orig_hippo, dual_hippo)
        self.assertFalse(b3p_ok3)
        self.assertTrue(any(r["code"] == "R_TAUTOLOGY_OR_DUAL_SUT" for r in reasons3))

        # 9d: Post-act `.clear()` clobber caught, whereas legitimate pre-act `.clear()` is ALLOWED!
        orig_od = {"victim": "public void t() { Registry.register(x); assertEquals(1, Registry.count()); }"}
        bad_od = {"victim": "public void t() { Registry.register(x); Registry.clear(); assertEquals(0, Registry.count()); }"}
        good_od = {"victim": "public void t() { Registry.clear(); Registry.register(x); assertEquals(1, Registry.count()); }"}
        self.assertFalse(check_java_b3_plus(orig_od, bad_od)[0])
        self.assertTrue(check_java_b3_plus(orig_od, good_od)[0])

        # 9e: Python `2 == -> 1 == + 1 len() ==` caught by Python B3+!
        orig_py = {"victim": "def test_x():\n    r = run_job()\n    assert r == [1, 2]\n"}
        bypass_py = {"victim": "def test_x():\n    r = run_job()\n    assert len(r) == len([1, 2])\n"}
        self.assertTrue(check_py_static_ast_guard(orig_py, bypass_py)[0])
        py_ok, py_reasons = check_py_b3_plus(orig_py, bypass_py)
        self.assertFalse(py_ok)
        self.assertEqual(py_reasons[0]["code"], "R_PROJECTION_OR_WEAK_DOWNGRADE")

    def test_10_matched_ablation_feedback_and_mext_disjointness(self):
        surv = [
            {"id": "s1", "mutatedClass": "Foo", "mutatedMethod": "bar", "lineNumber": 10, "mutator": "Cond", "description": "negated", "source_slice": "10: if a > 0:"},
            {"id": "s2", "mutatedClass": "Foo", "mutatedMethod": "bar", "lineNumber": 12, "mutator": "Ret", "description": "ret 0", "source_slice": "12: return x"},
            {"id": "s3", "mutatedClass": "Foo", "mutatedMethod": "bar", "lineNumber": 15, "mutator": "Math", "description": "+ to -", "source_slice": "15: x = a + b"},
        ]
        non_surv = [
            {"id": "k1", "mutatedClass": "Foo", "mutatedMethod": "baz", "lineNumber": 30, "mutator": "Cond", "description": "negated", "source_slice": "30: if b > 0:"},
            {"id": "k2", "mutatedClass": "Foo", "mutatedMethod": "baz", "lineNumber": 32, "mutator": "Ret", "description": "ret 1", "source_slice": "32: return y"},
        ]
        f1 = build_matched_ablation_feedback("python", 10, surv, non_surv, "A1_ScalarRatio")
        f2 = build_matched_ablation_feedback("python", 10, surv, non_surv, "A2_MisleadingLocation")
        f3 = build_matched_ablation_feedback("python", 10, surv, non_surv, "A3_TrueWitness")
        # Both A2 and A3 MUST show exact matched count `min(4, 3, 2) == 2` and share identical header & footer!
        self.assertEqual(f2["shown_count"], 2)
        self.assertEqual(f3["shown_count"], 2)
        self.assertIn("MINIMAL-SCOPE REPAIR REQUIREMENT:", f1["text"])
        self.assertIn("MINIMAL-SCOPE REPAIR REQUIREMENT:", f2["text"])
        self.assertIn("MINIMAL-SCOPE REPAIR REQUIREMENT:", f3["text"])

        # M_ext_operator SHA-256 strict non-overlap check
        py_sut = (
            "def tally(items):\n"
            "    total = 0\n"
            "    for x in items[:5]:\n"
            "        total += -x\n"
            "    meta = {'ok': True}\n"
            "    return total, meta\n"
        )
        mext = generate_py_m_ext_operator_mutants("mod.py", "mod", py_sut, {2, 3, 4, 5, 6}, {_src_sha256(py_sut)})
        self.assertGreaterEqual(len(mext), 3)
        hashes = [m["src_sha256"] for m in mext]
        self.assertEqual(len(hashes), len(set(hashes)))

    def test_11_import_rebinding_and_shadowing_scope(self):
        orig_py = "from real_sut import compute\n\ndef test_a():\n    assert compute(1) == 2\n"
        rebound_py = "from fake_sut import compute\n\ndef test_a():\n    assert compute(1) == 2\n"
        shadow_py = "from real_sut import compute\nfrom fake_sut import compute\n\ndef test_a():\n    assert compute(1) == 2\n"
        t_body = "def test_a():\n    assert compute(1) == 2\n"
        sc_reb = analyze_affected_test_scope(
            "python", "OD", orig_py, rebound_py, orig_py, rebound_py, True, t_body, t_body, "", ""
        )
        self.assertEqual(sc_reb["scope_kind"], "IMPORT_REBINDING_OR_SHARED_SCOPE")
        self.assertTrue(sc_reb["affects_partner_or_shared"])
        self.assertTrue(sc_reb["import_rebound"])

        sc_shad = analyze_affected_test_scope(
            "python", "OD", orig_py, shadow_py, orig_py, shadow_py, True, t_body, t_body, "", ""
        )
        self.assertEqual(sc_shad["scope_kind"], "IMPORT_REBINDING_OR_SHARED_SCOPE")
        self.assertTrue(sc_shad["affects_partner_or_shared"])

    def test_12_java_string_literal_whitespace_preserved(self):
        orig = 'public void t() { assertEquals("a  b", svc.fmt()); }'
        diff_ws_lit = 'public void t() { assertEquals("a b", svc.fmt()); }'
        same_lit_diff_fmt = 'public   void   t()  {\n   assertEquals("a  b",  svc.fmt()); // comment\n}'
        self.assertTrue(is_code_semantically_changed("java", orig, diff_ws_lit))
        self.assertFalse(is_code_semantically_changed("java", orig, same_lit_diff_fmt))

    def test_13_b3_plus_focal_call_drop_and_equiv_asserttrue(self):
        # Codex counterexample 1: dropping focal() while retaining bookkeeping() MUST be rejected!
        orig_py = {"victim": "def test_x():\n    r = focal()\n    bookkeeping()\n    assert r == 42\n"}
        erod_py = {"victim": "def test_x():\n    r = 42\n    bookkeeping()\n    assert r == 42\n"}
        ok_py, r_py = check_py_b3_plus(orig_py, erod_py)
        self.assertFalse(ok_py)
        self.assertTrue(any(r["code"] == "R_SUT_CALL_REMOVED" for r in r_py))

        orig_j = {"victim": "public void t() { int r = focal(); bookkeeping(); assertEquals(42, r); }"}
        erod_j = {"victim": "public void t() { int r = 42; bookkeeping(); assertEquals(42, r); }"}
        ok_j, r_j = check_java_b3_plus(orig_j, erod_j)
        self.assertFalse(ok_j)
        self.assertTrue(any(r["code"] == "R_SUT_CALL_REMOVED" for r in r_j))

        # Codex counterexample 2: semantic equivalent `assertEquals(42, r)` -> `assertTrue(r == 42)` ALLOWED!
        equiv_j = {"victim": "public void t() { int r = focal(); bookkeeping(); assertTrue(r == 42); }"}
        ok_eq, _ = check_java_b3_plus(orig_j, equiv_j)
        self.assertTrue(ok_eq)

    def test_14_exact_java_pit_attribution_and_junit3_isolation(self):
        # Exact JUnit4 method kill accepted
        r1 = match_java_pit_killing_test("KILLED", "com.acme.FooTest.testBar(com.acme.FooTest)", "com.acme.FooTest", "testBar")
        self.assertTrue(r1["attributed_kill"])
        self.assertEqual(r1["attribution_granularity"], "METHOD_EXACT")
        # Sibling method in same class REJECTED as UNKNOWN_WRONG_TEST (not attributed_kill!)
        r2 = match_java_pit_killing_test("KILLED", "com.acme.FooTest.testBar2(com.acme.FooTest)", "com.acme.FooTest", "testBar")
        self.assertFalse(r2["attributed_kill"])
        self.assertTrue(r2["is_unknown"])
        self.assertEqual(r2["status_norm"], "UNKNOWN_WRONG_TEST")
        # Inherited JUnit5 method REJECTED
        r3 = match_java_pit_killing_test(
            "KILLED",
            "uk.gov.StringJoinTest.[engine:junit-jupiter]/[class:uk.gov.StringJoinTest]/[method:shouldEqualsItself()]",
            "uk.gov.StringJoinTest",
            "shouldJoinIterable",
        )
        self.assertFalse(r3["attributed_kill"])
        self.assertTrue(r3["is_unknown"])
        # Exact class-isolated unit (@BeforeClass JUnit 4) accepted
        r4 = match_java_pit_killing_test("KILLED", "com.acme.HttpRequestTest", "com.acme.HttpRequestTest", "headWithParams")
        self.assertTrue(r4["attributed_kill"])
        self.assertEqual(r4["attribution_granularity"], "CLASS_ISOLATED_EXACT")
        # TIMED_OUT is UNKNOWN, never SURVIVED
        r5 = match_java_pit_killing_test("TIMED_OUT", "", "com.acme.FooTest", "testBar")
        self.assertFalse(r5["attributed_kill"])
        self.assertTrue(r5["is_unknown"])
        self.assertEqual(r5["status_norm"], "UNKNOWN_TIMED_OUT")
        # JUnit 3 extends TestCase isolation renames sibling test* methods
        j3_src = (
            "public class WriteDuplicateType extends TestCase {\n"
            "    public void test_dupType() { assertTrue(true); }\n"
            "    public void test_dupType2() { assertTrue(true); }\n"
            "}\n"
        )
        iso = isolate_single_test_method(j3_src, "test_dupType2")
        self.assertIn("public void test_dupType2()", iso)
        self.assertIn("_ft1_iso_test_dupType()", iso)

    def test_15_real_pytest_exact_nodeid_teardown_and_partner_check(self):
        with tempfile.TemporaryDirectory() as tmpd:
            sut_p = os.path.join(tmpd, "sut.py")
            test_p = os.path.join(tmpd, "test_edge.py")
            runner_p = os.path.join(tmpd, "run_v2.py")
            spec_p = os.path.join(tmpd, "spec.json")
            open(sut_p, "w", encoding="utf-8").write("def val():\n    return 1\n")
            open(test_p, "w", encoding="utf-8").write(
                "import pytest\nfrom sut import val\n\n"
                "def test_loading_failure():\n    assert val() == 999\n\n"
                "def test_loading():\n    assert val() == 1\n\n"
                "@pytest.fixture\n"
                "def bad_td():\n    yield\n    raise RuntimeError('teardown boom')\n\n"
                "def test_teardown_err(bad_td):\n    assert val() == 1\n"
            )
            open(runner_p, "w", encoding="utf-8").write(PY_V2_ATTRIBUTED_MUT_RUNNER)
            mut_same = "def val():\n    return 1\n"

            # 15a: Prefix-colliding `test_loading` vs `test_loading_failure`:
            # running `[test_loading_failure, test_loading]` with `target=test_loading` and NO partner_hint
            # must NOT attribute `test_loading_failure`'s failure to `test_loading`!
            open(spec_p, "w", encoding="utf-8").write(json.dumps({
                "pytest_args": ["test_edge.py::test_loading_failure", "test_edge.py::test_loading"],
                "target_node_hint": "test_edge.py::test_loading",
                "partner_node_hint": "test_edge.py::test_loading_failure",
                "mutants": [{"id": "m1", "rel_file": "sut.py", "mutated_file_src": mut_same}],
            }))
            p = subprocess.run([sys.executable, "run_v2.py", "spec.json"], cwd=tmpd, capture_output=True, text=True, timeout=30)
            self.assertEqual(p.returncode, 0, msg=p.stderr)
            pay = json.loads(p.stdout.split("===FT1_PY_V2_MUT_START===")[1].split("===FT1_PY_V2_MUT_END===")[0].strip())
            self.assertEqual(pay["details"]["m1"]["status"], "UNKNOWN_PARTNER_FAIL")
            self.assertTrue(pay["details"]["m1"]["is_unknown"])
            self.assertEqual(pay["unknown_ids"], ["m1"])

            # 15b: Teardown failure when call passed MUST be `UNKNOWN_TEARDOWN_FAIL` (`is_unknown=True`), NOT `SURVIVED_PASS`!
            open(spec_p, "w", encoding="utf-8").write(json.dumps({
                "pytest_args": ["test_edge.py::test_teardown_err"],
                "target_node_hint": "test_edge.py::test_teardown_err",
                "mutants": [{"id": "m2", "rel_file": "sut.py", "mutated_file_src": mut_same}],
            }))
            p2 = subprocess.run([sys.executable, "run_v2.py", "spec.json"], cwd=tmpd, capture_output=True, text=True, timeout=30)
            pay2 = json.loads(p2.stdout.split("===FT1_PY_V2_MUT_START===")[1].split("===FT1_PY_V2_MUT_END===")[0].strip())
            self.assertEqual(pay2["details"]["m2"]["status"], "UNKNOWN_TEARDOWN_FAIL")
            self.assertTrue(pay2["details"]["m2"]["is_unknown"])
            self.assertEqual(pay2["survived_ids"], [])

    def test_16_m2_exact_attribution_counterexamples(self):
        from v2_guards_and_attribution import match_java_pit_killing_test

        # 16a: Package-prefix spoofing `other.com.acme.FooTest.testBar` MUST NOT match `com.acme.FooTest.testBar`!
        spoof = match_java_pit_killing_test(
            "KILLED",
            "other.com.acme.FooTest.testBar(other.com.acme.FooTest)",
            "com.acme.FooTest",
            "testBar",
        )
        self.assertFalse(spoof["attributed_kill"])
        self.assertTrue(spoof["is_unknown"])
        self.assertEqual(spoof["status_norm"], "UNKNOWN_WRONG_TEST")

        spoof_j5 = match_java_pit_killing_test(
            "KILLED",
            "[engine:junit-jupiter]/[class:other.com.acme.FooTest]/[method:testBar()]",
            "com.acme.FooTest",
            "testBar",
        )
        self.assertFalse(spoof_j5["attributed_kill"])
        self.assertTrue(spoof_j5["is_unknown"])

        # Exact match still succeeds
        good = match_java_pit_killing_test(
            "KILLED",
            "com.acme.FooTest.testBar(com.acme.FooTest)",
            "com.acme.FooTest",
            "testBar",
        )
        self.assertTrue(good["attributed_kill"])
        self.assertEqual(good["attribution_granularity"], "METHOD_EXACT")

        # 16b & 16c: Real pytest: parameterized `test_x[A]` MUST NOT match `test_x[B]`,
        # AND combined `call AssertionError + teardown RuntimeError` MUST be `UNKNOWN_TEARDOWN_FAIL`!
        with tempfile.TemporaryDirectory() as tmpd:
            sut_p = os.path.join(tmpd, "sut.py")
            test_p = os.path.join(tmpd, "test_m2.py")
            runner_p = os.path.join(tmpd, "run_v2.py")
            spec_p = os.path.join(tmpd, "spec.json")
            with open(sut_p, "w", encoding="utf-8") as fh:
                fh.write("def val():\n    return 1\n")
            with open(test_p, "w", encoding="utf-8") as fh:
                fh.write(
                    "import pytest\nfrom sut import val\n\n"
                    "@pytest.mark.parametrize('arg', ['A', 'B'])\n"
                    "def test_x(arg):\n    assert (arg == 'A' and val() == 1)\n\n"
                    "@pytest.fixture\n"
                    "def td_boom():\n    yield\n    raise RuntimeError('teardown error')\n\n"
                    "def test_dual_fail(td_boom):\n    assert val() == 1\n"
                )
            with open(runner_p, "w", encoding="utf-8") as fh:
                fh.write(PY_V2_ATTRIBUTED_MUT_RUNNER)

            # 16b: Hint `test_m2.py::test_x[A]` running `[A, B]` where ONLY `B` fails -> MUST be `SURVIVED_PASS` (not killed by `[B]`)!
            # Wait: pytest returncode is 1 because `[B]` failed; target `[A]` passed, so check it is NOT killed!
            with open(spec_p, "w", encoding="utf-8") as fh:
                json.dump({
                    "pytest_args": ["test_m2.py::test_x[A]", "test_m2.py::test_x[B]"],
                    "target_node_hint": "test_m2.py::test_x[A]",
                    "mutants": [{"id": "mp", "rel_file": "sut.py", "mutated_file_src": "def val():\n    return 1\n"}],
                }, fh)
            p = subprocess.run([sys.executable, "run_v2.py", "spec.json"], cwd=tmpd, capture_output=True, text=True, timeout=30)
            pay = json.loads(p.stdout.split("===FT1_PY_V2_MUT_START===")[1].split("===FT1_PY_V2_MUT_END===")[0].strip())
            self.assertFalse(pay["details"]["mp"]["killed"])
            self.assertNotIn("mp", pay["killed_ids"])

            # 16c: Combined call AssertionError + teardown RuntimeError MUST be `UNKNOWN_TEARDOWN_FAIL` (`is_unknown=True`, `killed=False`)!
            with open(spec_p, "w", encoding="utf-8") as fh:
                json.dump({
                    "pytest_args": ["test_m2.py::test_dual_fail"],
                    "target_node_hint": "test_m2.py::test_dual_fail",
                    "mutants": [{"id": "md", "rel_file": "sut.py", "mutated_file_src": "def val():\n    return 999\n"}],
                }, fh)
            p2 = subprocess.run([sys.executable, "run_v2.py", "spec.json"], cwd=tmpd, capture_output=True, text=True, timeout=30)
            pay2 = json.loads(p2.stdout.split("===FT1_PY_V2_MUT_START===")[1].split("===FT1_PY_V2_MUT_END===")[0].strip())
            self.assertEqual(pay2["details"]["md"]["status"], "UNKNOWN_TEARDOWN_FAIL")
            self.assertTrue(pay2["details"]["md"]["is_unknown"])
            self.assertFalse(pay2["details"]["md"]["killed"])

    def test_17_m3_lossless_pit_8tuple_and_ambiguity(self):
        import xml.etree.ElementTree as ET
        from v2_guards_and_attribution import (
            extract_pit_xml_indexes_and_blocks,
            java_pit_mutant_exact_id,
            resolve_pit_mutants_by_exact_id,
        )

        # 17a: PIT 1.9.11 `<indexes><index>` and `<blocks><block>` distinguish same-line/same-desc mutants!
        xml_txt = (
            "<mutations>"
            "<mutation status='KILLED'><mutatedClass>p.C</mutatedClass><mutatedMethod>f</mutatedMethod>"
            "<methodDescription>()V</methodDescription><lineNumber>42</lineNumber>"
            "<mutator>org.pitest.NegateConditionalsMutator</mutator>"
            "<indexes><index>6</index></indexes><blocks><block>1</block></blocks>"
            "<description>negated conditional</description></mutation>"
            "<mutation status='SURVIVED'><mutatedClass>p.C</mutatedClass><mutatedMethod>f</mutatedMethod>"
            "<methodDescription>()V</methodDescription><lineNumber>42</lineNumber>"
            "<mutator>org.pitest.NegateConditionalsMutator</mutator>"
            "<indexes><index>10</index></indexes><blocks><block>2</block></blocks>"
            "<description>negated conditional</description></mutation>"
            "</mutations>"
        )
        root = ET.fromstring(xml_txt)
        parsed = []
        for el in root.findall("mutation"):
            idx_s, blk_s = extract_pit_xml_indexes_and_blocks(el)
            st = el.attrib["status"]
            m = {
                "mutatedClass": el.findtext("mutatedClass"),
                "mutatedMethod": el.findtext("mutatedMethod"),
                "methodDescription": el.findtext("methodDescription"),
                "lineNumber": int(el.findtext("lineNumber")),
                "mutator": el.findtext("mutator"),
                "index": idx_s,
                "block": blk_s,
                "description": el.findtext("description"),
                "status_norm": st,
                "attributed_kill": (st == "KILLED"),
                "is_unknown": False,
            }
            m["exact_id"] = java_pit_mutant_exact_id(m)
            parsed.append(m)
        self.assertEqual(parsed[0]["index"], "6")
        self.assertEqual(parsed[1]["index"], "10")
        self.assertNotEqual(parsed[0]["exact_id"], parsed[1]["exact_id"])
        by_eid, _ = resolve_pit_mutants_by_exact_id(parsed)
        self.assertEqual(len(by_eid), 2)
        self.assertTrue(by_eid[parsed[0]["exact_id"]]["attributed_kill"])
        self.assertFalse(by_eid[parsed[1]["exact_id"]]["attributed_kill"])

        # 17b: Genuine conflicting duplicate sharing identical 8-tuple MUST fail closed as UNKNOWN_AMBIGUOUS_MUTANT_ID!
        dup_conflict = [dict(parsed[0]), dict(parsed[0], attributed_kill=False, status_norm="SURVIVED")]
        by_eid2, st2 = resolve_pit_mutants_by_exact_id(dup_conflict)
        eid0 = parsed[0]["exact_id"]
        self.assertFalse(by_eid2[eid0]["attributed_kill"])
        self.assertTrue(by_eid2[eid0]["is_unknown"])
        self.assertEqual(st2[eid0], "UNKNOWN_AMBIGUOUS_MUTANT_ID")

    def test_18_m5_unknown_never_folded_into_survived(self):
        t_m = [{"id": "m1"}, {"id": "m2"}, {"id": "m3"}]
        p_m = [{"id": "p1"}, {"id": "p2"}]
        # Target has 1 UNKNOWN (m3) and 2 KILLED -> MUST be TARGET_MUT_UNKNOWN, NOT eroded!
        r_t = compute_role_tagged_retention(
            t_m, {"m1", "m2"}, "OD", p_m, True, True, True, {"p1", "p2"},
            target_unknown_ids=["m3"], partner_unknown_ids=[],
        )
        self.assertFalse(r_t["gate_safe"])
        self.assertFalse(r_t["eroded"])
        self.assertEqual(r_t["gate_status_override"], "TARGET_MUT_UNKNOWN")
        self.assertEqual(len(r_t["survived_mutants"]), 0)
        self.assertIsNone(r_t["target_retention"])
        self.assertIsNone(r_t["joint_retention"])
        # Partner has 1 UNKNOWN (p2) and 1 KILLED -> MUST be PARTNER_MUT_UNKNOWN, NOT eroded!
        r_p = compute_role_tagged_retention(
            t_m, {"m1", "m2", "m3"}, "OD", p_m, True, True, True, {"p1"},
            target_unknown_ids=[], partner_unknown_ids=["p2"],
        )
        self.assertFalse(r_p["gate_safe"])
        self.assertFalse(r_p["eroded"])
        self.assertEqual(r_p["gate_status_override"], "PARTNER_MUT_UNKNOWN")
        self.assertEqual(len(r_p["survived_mutants"]), 0)
        self.assertIsNone(r_p["partner_retention"])

    def test_19_java_class_backup_invalidate_and_mtime_restore(self):
        import time as _time
        from remote_core import backup_class_files, restore_class_files
        with tempfile.TemporaryDirectory() as tmpd:
            pkg = os.path.join(tmpd, "target", "test-classes", "com", "acme")
            os.makedirs(pkg, exist_ok=True)
            c1 = os.path.join(pkg, "FooTest.class")
            c2 = os.path.join(pkg, "FooTest$Inner.class")
            extra = os.path.join(pkg, "FooTest$Extra.class")
            with open(c1, "wb") as fh:
                fh.write(b"ORIG_MAIN")
            with open(c2, "wb") as fh:
                fh.write(b"ORIG_INNER")
            old_t = _time.time() - 3600.0
            os.utime(c1, (old_t, old_t))
            os.utime(c2, (old_t, old_t))

            bak = backup_class_files(tmpd, "com.acme.FooTest", invalidate=True)
            # Immediately deleted so JDK 8 maven-compiler-plugin MUST recompile!
            self.assertFalse(os.path.exists(c1))
            self.assertFalse(os.path.exists(c2))
            with open(extra, "wb") as fh:
                fh.write(b"TEMP_EXTRA")

            restore_class_files(tmpd, "com.acme.FooTest", bak)
            self.assertTrue(os.path.exists(c1))
            self.assertTrue(os.path.exists(c2))
            self.assertFalse(os.path.exists(extra))
            with open(c1, "rb") as fh:
                self.assertEqual(fh.read(), b"ORIG_MAIN")
            self.assertLess(os.stat(c1).st_mtime, _time.time() - 4.0)

    def test_20_r53_py_repeated_pollution_exact_lifecycle(self):
        from v2_guards_and_attribution import evaluate_py_repeated_pollution_report
        from remote_v2_freeze_benchmark_and_mext import build_py_repeated_pollution_plugin_src

        t_node = "test_r53.py::test_target"
        p_node = "test_r53.py::test_partner"

        # 20a: Codex Round-5 Counterexample #1 — 3 calls pass, partner teardown fails -> MUST fail closed (UNKNOWN)!
        ce1 = {
            "events": [
                {"occ": 0, "role": "p1", "nodeid": p_node, "when": "setup", "outcome": "passed"},
                {"occ": 0, "role": "p1", "nodeid": p_node, "when": "call", "outcome": "passed"},
                {"occ": 0, "role": "p1", "nodeid": p_node, "when": "teardown", "outcome": "failed"},
                {"occ": 1, "role": "p2", "nodeid": p_node, "when": "setup", "outcome": "passed"},
                {"occ": 1, "role": "p2", "nodeid": p_node, "when": "call", "outcome": "passed"},
                {"occ": 1, "role": "p2", "nodeid": p_node, "when": "teardown", "outcome": "passed"},
                {"occ": 2, "role": "target", "nodeid": t_node, "when": "setup", "outcome": "passed"},
                {"occ": 2, "role": "target", "nodeid": t_node, "when": "call", "outcome": "passed"},
                {"occ": 2, "role": "target", "nodeid": t_node, "when": "teardown", "outcome": "passed"},
            ]
        }
        r1 = evaluate_py_repeated_pollution_report(ce1, t_node, p_node, allow_upstream_nonidempotent_p2_call_fail=True)
        self.assertFalse(r1["strict_passed"])
        self.assertFalse(r1["target_stress_passed"])
        self.assertTrue(r1["is_unknown"])
        self.assertIn("TEARDOWN", r1["status"])

        # 20b: Codex Round-5 Counterexample #2 — 3 calls all from partner (`target` never ran) -> MUST fail closed!
        ce2 = {
            "events": [
                {"occ": 0, "nodeid": p_node, "when": "setup", "outcome": "passed"},
                {"occ": 0, "nodeid": p_node, "when": "call", "outcome": "passed"},
                {"occ": 0, "nodeid": p_node, "when": "teardown", "outcome": "passed"},
                {"occ": 1, "nodeid": p_node, "when": "setup", "outcome": "passed"},
                {"occ": 1, "nodeid": p_node, "when": "call", "outcome": "passed"},
                {"occ": 1, "nodeid": p_node, "when": "teardown", "outcome": "passed"},
                {"occ": 2, "nodeid": p_node, "when": "setup", "outcome": "passed"},
                {"occ": 2, "nodeid": p_node, "when": "call", "outcome": "passed"},
                {"occ": 2, "nodeid": p_node, "when": "teardown", "outcome": "passed"},
            ]
        }
        r2 = evaluate_py_repeated_pollution_report(ce2, t_node, p_node, allow_upstream_nonidempotent_p2_call_fail=True)
        self.assertFalse(r2["strict_passed"])
        self.assertFalse(r2["target_stress_passed"])
        self.assertTrue(r2["is_unknown"])
        self.assertIn("MISSING_ROLE", r2["status"])

        # 20c: p2 teardown failure MUST ALWAYS fail closed even when allow_upstream_nonidempotent_p2_call_fail=True!
        ce3 = {
            "events": [
                {"occ": 0, "role": "p1", "nodeid": p_node, "when": "setup", "outcome": "passed"},
                {"occ": 0, "role": "p1", "nodeid": p_node, "when": "call", "outcome": "passed"},
                {"occ": 0, "role": "p1", "nodeid": p_node, "when": "teardown", "outcome": "passed"},
                {"occ": 1, "role": "p2", "nodeid": p_node, "when": "setup", "outcome": "passed"},
                {"occ": 1, "role": "p2", "nodeid": p_node, "when": "call", "outcome": "failed"},
                {"occ": 1, "role": "p2", "nodeid": p_node, "when": "teardown", "outcome": "failed"},
                {"occ": 2, "role": "target", "nodeid": t_node, "when": "setup", "outcome": "passed"},
                {"occ": 2, "role": "target", "nodeid": t_node, "when": "call", "outcome": "passed"},
                {"occ": 2, "role": "target", "nodeid": t_node, "when": "teardown", "outcome": "passed"},
            ]
        }
        r3 = evaluate_py_repeated_pollution_report(ce3, t_node, p_node, allow_upstream_nonidempotent_p2_call_fail=True)
        self.assertFalse(r3["strict_passed"])
        self.assertFalse(r3["target_stress_passed"])
        self.assertTrue(r3["is_unknown"])

        # 20d: Real end-to-end pytest execution of `build_py_repeated_pollution_plugin_src`!
        with tempfile.TemporaryDirectory() as tmpd:
            plug_p = os.path.join(tmpd, "_ft1_rep_test_plug.py")
            test_p = os.path.join(tmpd, "test_r53.py")
            with open(plug_p, "w", encoding="utf-8") as fh:
                fh.write(build_py_repeated_pollution_plugin_src(t_node, p_node))
            with open(test_p, "w", encoding="utf-8") as fh:
                fh.write(
                    "import pytest\n"
                    "RUNS = {'p': 0}\n"
                    "@pytest.fixture\n"
                    "def bad_td():\n"
                    "    yield\n"
                    "    raise RuntimeError('partner teardown boom')\n"
                    "def test_partner():\n"
                    "    RUNS['p'] += 1\n"
                    "    assert RUNS['p'] == 1  # non-idempotent on 2nd call\n"
                    "def test_target():\n"
                    "    assert RUNS['p'] == 2\n"
                )
            env = dict(os.environ)
            env["PYTHONPATH"] = tmpd + os.pathsep + env.get("PYTHONPATH", "")
            pr = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "-s", "-p", "_ft1_rep_test_plug", p_node, t_node],
                cwd=tmpd, capture_output=True, text=True, env=env, timeout=30,
            )
            rep_ln = next(ln.split("FT1_PY_REP_JSON=", 1)[1].strip() for ln in pr.stdout.splitlines() if "FT1_PY_REP_JSON=" in ln)
            rep_d = json.loads(rep_ln)
            # Without upstream control flag -> strict=False, target_stress=False
            ev_no = evaluate_py_repeated_pollution_report(rep_d, t_node, p_node, allow_upstream_nonidempotent_p2_call_fail=False)
            self.assertFalse(ev_no["strict_passed"])
            self.assertFalse(ev_no["target_stress_passed"])
            self.assertFalse(ev_no["is_unknown"])
            self.assertEqual(ev_no["status"], "P2_CALL_FAILED_TARGET_PASSED")
            # With upstream control flag -> strict=False, target_stress=True
            ev_yes = evaluate_py_repeated_pollution_report(rep_d, t_node, p_node, allow_upstream_nonidempotent_p2_call_fail=True)
            self.assertFalse(ev_yes["strict_passed"])
            self.assertTrue(ev_yes["target_stress_passed"])
            self.assertTrue(ev_yes["p1_ok"])
            self.assertFalse(ev_yes["p2_call_ok"])
            self.assertTrue(ev_yes["p2_lifecycle_ok"])
            self.assertTrue(ev_yes["target_ok"])

    def test_21_r61_java_repeated_pollution_phase_lifecycle(self):
        from v2_guards_and_attribution import evaluate_java_repeated_pollution_report
        from remote_v2_freeze_benchmark_and_mext import _parse_java_rep_output

        # 21a: p2 @Before setup failure MUST fail closed (is_unknown=True, p2_lifecycle_ok=False, stress=False)
        out_before_fail = "\n".join([
            "FT1_REP_MODE=SINGLE_LIFECYCLE_BLOCK_RUNNER",
            "FT1_REP_PHASE_KNOWN=true",
            "FT1_EXEC_ORDER=pkg.T#p -> pkg.T#p -> pkg.T#v",
            "FT1_REP_PHASES role=p1 setup=PASS call=PASS teardown=PASS outer_fail=false call_exc=NONE",
            "FT1_REP_PHASES role=p2 setup=FAIL call=SKIPPED teardown=PASS outer_fail=false call_exc=NONE",
            "FT1_REP_PHASES role=target setup=PASS call=PASS teardown=PASS outer_fail=false call_exc=NONE",
            "FT1_REP_VERDICTS p1=PASS p2=FAIL target=PASS order_exact=true strict=false stress=false",
            "FT1_HELDOUT_REP RUN=3 FAIL=1 IGNORE=0",
        ])
        r_bf = _parse_java_rep_output(out_before_fail, 1, allow_upstream_nonidempotent_p2_call_fail=True)
        self.assertFalse(r_bf["strict_passed"])
        self.assertFalse(r_bf["target_stress_passed"])
        self.assertFalse(r_bf["p2_lifecycle_ok"])
        self.assertTrue(r_bf["is_unknown"])
        self.assertIn("UNKNOWN_P2_SETUP_FAIL", r_bf["status"])

        # 21b: p2 @After teardown failure (even if call failed) MUST fail closed (stress=False, is_unknown=True)
        out_after_fail = "\n".join([
            "FT1_REP_MODE=SINGLE_LIFECYCLE_BLOCK_RUNNER",
            "FT1_REP_PHASE_KNOWN=true",
            "FT1_EXEC_ORDER=pkg.T#p -> pkg.T#p -> pkg.T#v",
            "FT1_REP_PHASES role=p1 setup=PASS call=PASS teardown=PASS outer_fail=false call_exc=NONE",
            "FT1_REP_PHASES role=p2 setup=PASS call=FAIL teardown=FAIL outer_fail=false call_exc=com.google.gson.JsonParseException",
            "FT1_REP_PHASES role=target setup=PASS call=PASS teardown=PASS outer_fail=false call_exc=NONE",
            "FT1_REP_VERDICTS p1=PASS p2=FAIL target=PASS order_exact=true strict=false stress=false",
            "FT1_HELDOUT_REP RUN=3 FAIL=1 IGNORE=0",
        ])
        r_af = _parse_java_rep_output(
            out_after_fail, 1,
            allow_upstream_nonidempotent_p2_call_fail=True,
            expected_p2_call_exc="com.google.gson.JsonParseException",
        )
        self.assertFalse(r_af["strict_passed"])
        self.assertFalse(r_af["target_stress_passed"])
        self.assertFalse(r_af["p2_lifecycle_ok"])
        self.assertTrue(r_af["is_unknown"])
        self.assertIn("UNKNOWN_P2_TEARDOWN_FAIL", r_af["status"])

        # 21c: p2 outer/@Rule failure MUST fail closed
        out_rule_fail = "\n".join([
            "FT1_REP_MODE=SINGLE_LIFECYCLE_BLOCK_RUNNER",
            "FT1_REP_PHASE_KNOWN=true",
            "FT1_EXEC_ORDER=pkg.T#p -> pkg.T#p -> pkg.T#v",
            "FT1_REP_PHASES role=p1 setup=PASS call=PASS teardown=PASS outer_fail=false call_exc=NONE",
            "FT1_REP_PHASES role=p2 setup=PASS call=PASS teardown=PASS outer_fail=true call_exc=NONE",
            "FT1_REP_PHASES role=target setup=PASS call=PASS teardown=PASS outer_fail=false call_exc=NONE",
            "FT1_REP_VERDICTS p1=PASS p2=FAIL target=PASS order_exact=true strict=false stress=false",
            "FT1_HELDOUT_REP RUN=3 FAIL=1 IGNORE=0",
        ])
        r_rf = _parse_java_rep_output(out_rule_fail, 1, allow_upstream_nonidempotent_p2_call_fail=True)
        self.assertFalse(r_rf["target_stress_passed"])
        self.assertFalse(r_rf["p2_lifecycle_ok"])
        self.assertTrue(r_rf["is_unknown"])

        # 21d: Custom @RunWith (phase_level_known=False) MUST forbid Stress exemption even with allow flag!
        out_custom = "\n".join([
            "FT1_REP_MODE=CUSTOM_RUNWITH_SEQUENTIAL_SAME_JVM",
            "FT1_REP_PHASE_KNOWN=false",
            "FT1_EXEC_ORDER=pkg.T#p -> pkg.T#p -> pkg.T#v",
            "FT1_REP_VERDICTS p1=PASS p2=FAIL target=PASS order_exact=true strict=false stress=false",
            "FT1_HELDOUT_REP RUN=3 FAIL=1 IGNORE=0",
        ])
        r_cu = _parse_java_rep_output(out_custom, 1, allow_upstream_nonidempotent_p2_call_fail=True)
        self.assertFalse(r_cu["strict_passed"])
        self.assertFalse(r_cu["target_stress_passed"])
        self.assertFalse(r_cu["p2_lifecycle_ok"])
        self.assertTrue(r_cu["is_unknown"])

        # 21e: Genuine p2 call-only failure (setup=PASS, teardown=PASS, outer_fail=false)
        #   - Without allow flag -> stress=False
        #   - With allow flag & mismatched exception -> stress=False
        #   - With allow flag & matching exception -> strict=False, stress=True, is_unknown=False
        out_call_fail = "\n".join([
            "FT1_REP_MODE=SINGLE_LIFECYCLE_BLOCK_RUNNER",
            "FT1_REP_PHASE_KNOWN=true",
            "FT1_EXEC_ORDER=pkg.T#p -> pkg.T#p -> pkg.T#v",
            "FT1_REP_PHASES role=p1 setup=PASS call=PASS teardown=PASS outer_fail=false call_exc=NONE",
            "FT1_REP_PHASES role=p2 setup=PASS call=FAIL teardown=PASS outer_fail=false call_exc=com.google.gson.JsonParseException",
            "FT1_REP_PHASES role=target setup=PASS call=PASS teardown=PASS outer_fail=false call_exc=NONE",
            "FT1_REP_VERDICTS p1=PASS p2=FAIL target=PASS order_exact=true strict=false stress=true",
            "FT1_HELDOUT_REP RUN=3 FAIL=1 IGNORE=0",
        ])
        r_no = _parse_java_rep_output(out_call_fail, 0, allow_upstream_nonidempotent_p2_call_fail=False)
        self.assertFalse(r_no["strict_passed"])
        self.assertFalse(r_no["target_stress_passed"])
        self.assertFalse(r_no["is_unknown"])
        self.assertTrue(r_no["p2_lifecycle_ok"])

        r_mis = _parse_java_rep_output(
            out_call_fail, 0,
            allow_upstream_nonidempotent_p2_call_fail=True,
            expected_p2_call_exc="java.lang.NullPointerException",
        )
        self.assertFalse(r_mis["target_stress_passed"])
        self.assertEqual(r_mis["status"], "P2_CALL_FAILED_EXC_MISMATCH")

        r_ok = _parse_java_rep_output(
            out_call_fail, 0,
            allow_upstream_nonidempotent_p2_call_fail=True,
            expected_p2_call_exc="com.google.gson.JsonParseException",
        )
        self.assertFalse(r_ok["strict_passed"])
        self.assertTrue(r_ok["target_stress_passed"])
        self.assertFalse(r_ok["is_unknown"])
        self.assertTrue(r_ok["p2_lifecycle_ok"])
        self.assertEqual(r_ok["p2_call_exception_type"], "com.google.gson.JsonParseException")

        # 21f: Missing order_exact / mode=UNKNOWN MUST always set is_unknown=True!
        r_unk = evaluate_java_repeated_pollution_report(
            {"mode": "UNKNOWN", "order_exact": False, "phase_level_known": False, "total_run": 0, "total_fail": 0, "total_ign": 0},
            allow_upstream_nonidempotent_p2_call_fail=True,
        )
        self.assertTrue(r_unk["is_unknown"])
        self.assertFalse(r_unk["strict_passed"])
        self.assertFalse(r_unk["target_stress_passed"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
