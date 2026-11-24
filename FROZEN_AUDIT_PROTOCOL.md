# FT-1 Frozen Confirmatory Audit & Evaluation Protocol (`v2.0.0-frozen`)

**Timestamp Locked (Pre-Confirmatory Freeze)**: `2026-11-17T09:38:00+08:00`  
**Target Venue**: *Software Quality Journal* (SQJ, Springer Nature)  
**Harness Version**: `2.0.0-frozen`

---

## 1. Exploratory vs. Confirmatory Separation & Cohort Accounting

1. **Initial Candidate Cohort (`N_init = 208`, `70` open-source repositories)**:
   - `113` Java tests (`64` `ID`, `49` `OD-victim` across `36` Maven repositories) and `95` Python tests (`62` `OD-victim`, `33` `OD-brittle` across `34` `pytest` repositories).
   - Prior runs on `deepseek-flash` and `deepseek-v4-pro` under `v1` served as the **exploratory development baseline** that surfaced implementation bugs audited in Round 1–2.
2. **Strict Eligibility & Attrition Accounting (`N_init = 208 -> N_valid`)**:
   - Under `v2.0.0-frozen`, every baseline obligation in `M_det(T_orig)` must satisfy node-and-phase attribution (`call`-phase test failure of `T_orig`, `tests_ran >= 1`, non-timeout, non-setup error across `3/3` clean-`e_0` runs; for `OD-brittle`, the baseline state-setter must explicitly pass in `call` phase).
   - Any instance in `208` whose valid `|M_det(T_orig)|` drops below `3` after strict attribution is **NOT silently replaced**; we report full CONSORT-style attrition (`208` attempted $\to$ `N_valid` retained, with exact attrition reasons) and report primary analyses on `N_valid` (with intent-to-treat sensitivity on all `208`).
3. **New Confirmatory Runs Locked Under `v2.0.0-frozen`**:
   - **Confirmatory Multi-Round Model (`K = 5`)**: `qwen3.7-plus` across the full cohort (`B0`, `B2`, `B3`, `B3+`, `B4_Scalar_Gate_Loop`, `Ours_MuGuard_Flake`) — never previously run at `K = 5`.
   - **Confirmatory Cross-Family Single-Pass (`K = 1`)**: Complete remaining instances on `kimi-k2.5` and `glm-5` (and report `qwen3.7-plus` Round 1 explicitly noting shared Round-1 data with its `K = 5` run).
   - **Frozen External Validation (`M_ext_operator`, `M_ext_instance`, `W+_heldout`)**: Constructed and frozen **prior** to evaluating any candidate patches on `M_ext`.

---

## 2. Provenance, Applicator, & Semantic Adjudication Rules (Part 1)

1. **PR-Linked Reference Patches (`data/pr_reference_manifest.json`)**:
   - Record for all `47` PR-linked instances (`26` distinct GitHub PR URLs): `sample_id`, `pr_url`, `idoft_status` (`Accepted`, `Opened`, `DeveloperWontFix`), `pr_diff_sha256`, `test_files_touched`, `prod_files_touched`, `build_files_touched`, `applicator_mode`, `applied_tree_sha256`, and `3/3` deterministic mutation evaluation.
   - Stratify strictly into:
     - **Maintainer-Accepted Test-Only Reference Set (`PR_Accepted_TestOnly`)**: `Accepted` PRs that do not modify production `src/main` code.
     - **Maintainer-Accepted Prod+Test Set (`PR_Accepted_WithProd`)**: `Accepted` PRs that also modify production code (analyzed separately for mutant comparability).
     - **Unmerged Proposed PRs (`PR_Opened`)** and **Rejected/WontFix (`PR_WontFix`)**: Never labeled as accepted human gold fixes.
2. **Independent Semantic Adjudication (`data/pr_semantic_adjudication.json`)**:
   - Inspect **all** PR-linked instances with `Retention(M_det) < 1.0` plus a stratified control sample of `Retention(M_det) == 1.0` instances.
   - Each divergent mutant is adjudicated along: `orig_contract_evidence -> patch_behavior_change -> mutant_behavior_change -> label -> confidence`, using five mutually exclusive labels:
     1. `LEGITIMATE_RELAXATION` (valid specification/order relaxation — strict gate incurs **true false rejection**),
     2. `GENUINE_ORACLE_WEAKENING` (patch weakens real fault-detection obligation),
     3. `TEST_RESPONSIBILITY_SHIFT` (test refactored to test a narrower/different unit),
     4. `APPLICATOR_OR_PROD_DRIFT` (cross-file/production change alters mutant applicability),
     5. `INSUFFICIENT_EVIDENCE`.
   - Report two distinct metrics (never conflated):
     - **Accepted-PR Gate Rejection Rate**: fraction of `PR_Accepted_TestOnly` rejected by a gate.
     - **Adjudicated Legitimate-Fix False-Rejection Rate**: fraction of independently adjudicated legitimate & stable reference fixes rejected by a gate (`B3`, `B3+`, `MuGuard-Flake`).
3. **Historical SOTA Artifact Audit (`B1`, `data/b1_provenance_manifest.json`)**:
   - Primary `B1` cohort restricted strictly to **authoritative upstream passing entries** (`:P` in `FlakyDoctor/results/gpt_fixes_tests.csv` and `magicoder_fixes_tests.csv`; verified target–partner patches in `ipflakies_artifact`). Any unmatched archive `.patch` files (`5` GPT, `5` Magicoder) are excluded from primary `B1` tables and logged separately in the manifest.
   - Separate stage outcomes: `format_or_target_mismatch`, ` verbatim_compile_fail`, `salvaged_import_compile_ok` (reported separately from strict verbatim!), `w_plus_fail`, `m_det_eroded`, and `operational_sound_fix`.
   - Characterize `B1` strictly as **historical automated-validator-passing SOTA artifacts**, never as human-vetted patches.

---

## 3. Gate Closure, Affected-Scope Protection, & Trajectory Re-Execution (Part 3)

1. **Full Affected-Scope Diff Check**:
   - Every candidate evaluation computes the full file-level diff on `victim_file`, `polluter_file`, and `pom.xml`.
   - Edits touching `partner_method`, class-level fields, `@Before`/`@After`/`setUp`/`tearDown`/fixtures, or shared helpers classify the candidate as `AFFECTS_PARTNER_OR_SHARED_SCOPE`.
2. **Closed-Fail Gate Transitions (`MuGuard-Flake` & `B4_Scalar_Gate_Loop`)**:
   - If `AFFECTS_PARTNER_OR_SHARED_SCOPE` is true on an `OD` instance:
     - If `len(polluter_m_det) == 0`: gate MUST NOT return `SAFE_PASS`; return `ABSTAIN_PARTNER_UNCOVERED` (or retry requesting target-local cleanup if rounds remain).
     - If partner mutation probe fails/errors (`not p_ok`): gate MUST NOT return `SAFE_PASS`; treat as `EVAL_ERROR_RETRY` / `ABSTAIN_COMPILE`.
   - Role-tagged obligation identity `(role, test_node, mutant_id)` ensures target and partner obligations are never merged; report `target_retention`, `partner_retention`, and `joint_retention` separately.
3. **True Trajectory Integrity**:
   - Re-evaluating `v1` cached rounds under `v2.0.0-frozen`: if `decision` and `feedback_msg` are unchanged up to termination, the trajectory is preserved; **at the earliest round where `decision` or `feedback_msg` changes (or for new strategies `B3+` and `B4_Scalar_Gate_Loop`), all subsequent rounds are genuinely generated and evaluated** — never spliced from stale suffixes.

---

## 4. Frozen External Validation (`M_ext` & `W+_heldout`, Part 2)

1. **Held-Out Stability Check (`W+_heldout`)**:
   - Java `ID`: `NonDex` across **5 held-out deterministic seeds** (`910001, 910002, 910003, 910004, 910005`) disjoint from repair-loop seeds.
   - Java/Python `OD`: standalone `target` (`2x`), `partner -> target` (`2x`), `target -> partner` (`2x`), and repeated pollution `partner -> partner -> target` (`1x`).
2. **Two Disjoint Held-Out Fault Pools (`M_ext`, Frozen Pre-Evaluation)**:
   - **`M_ext_operator` (Unseen Operator Families)**:
     - *Java*: Explicit PIT mutators disjoint from `DEFAULTS`: `INLINE_CONSTS`, `REMOVE_CONDITIONALS_EQ_IF`, `REMOVE_CONDITIONALS_ORD_IF`, `EXPERIMENTAL_MEMBER_VARIABLE`, `EXPERIMENTAL_SWITCH`, `ARGUMENT_PROPAGATION`, `NAKED_RECEIVER`, `CONSTRUCTOR_CALLS`, `NON_VOID_METHOD_CALLS`. Deduplicated against all target/partner `M_det` obligations by `(mutatedClass, mutatedMethod, lineNumber, description)` and split into **Assertion/Comparison Kills** vs **Exception/Crash Kills** in `e_0`.
     - *Python*: Explicit AST mutators absent from `M_det`: `AugAssignMutator`, `UnaryOpMutator`, `DictEntryMutator`, `SliceBoundMutator`, `CallArgDropMutator`. Deduplicated by SHA-256 of `mutated_file_src` against all target/partner `M_det` mutants, `3/3` `call`-phase killed in `e_0`, and split into `AssertionError` vs non-assertion exception kills.
   - **`M_ext_instance` (Same-Family Held-Out Focal Sites, Python >55 Cap)**:
     - Standard-operator focal mutants outside the 55-mutant `M_det` cap, deduplicated by SHA-256 of `mutated_file_src` and `3/3` `call`-phase killed in `e_0`.
3. **Primary External End-to-End Yield Metric ($Y_s$)**:
   - Over the pre-defined cohort $E$ of instances possessing $|M_{\text{ext}}| \ge 1$:
     $$Y_s = \frac{1}{|E|} \sum_{i \in E} \mathbf{1}\{\text{Accepted}_{i,s} \land W^+_{\text{heldout}, i, s} \land R_{\text{ext}, i, s} = 1\}$$
     reported alongside `Accepted%`, conditional holdout loss among accepted patches, and **Assertion-Kill-Only `M_ext` retention** (preventing trivial NPE/crash mutants from inflating external retention). Instances with `|M_ext| = 0` are marked `N/A` (excluded from $E$ prior to inspecting strategy outcomes); any evaluation failure is reported as `UNKNOWN` with worst/best-case bounds.

---

## 5. Controlled Witness Ablation & Statistical Protocol (Parts 4 & 5)

1. **Prefix-Forked Witness Experiment + Full-Cohort `B4_Scalar_Gate_Loop`**:
   - Enrolment rule locked prior to branching: every `(model, sample_id)` across `deepseek-flash`, `deepseek-v4-pro`, and `qwen3.7-plus` (`K = 5`) whose shared Round `r_0 < 5` trajectory first hits `W+ == True` and `eroded == True` enters the conditional recovery experiment (`A1_ScalarRatio`, `A2_MisleadingLocationStress`, `A3_TrueWitness`; instances first eroding at `r_0 == 5` have `0` remaining retries under all branches and are retained in full-cohort `B4_Scalar_Gate_Loop` vs `MuGuard-Flake`).
   - `A1` uses natural concise scalar feedback (same repair header/footer, no junk padding); `A2` and `A3` match exact mutant item count $m = \min(4, |S_{\text{surv}}|, |S_{\text{killed}}|)$, template structure, and recorded tokenizer/character length; branch execution order is interleaved per instance.
   - **Primary Mechanism Test**: `A3_TrueWitness` vs `A1_ScalarRatio` on binary recovery rate (and full-cohort `MuGuard-Flake` vs `B4_Scalar_Gate_Loop`). If the 95% project-clustered paired CI includes `0`, superiority of localized witness over scalar gate retry is declared **inconclusive**.
2. **Clustered Paired Statistical Inference**:
   - Primary estimator: test-weighted paired risk difference $\Delta_{A-B}$ with **20,000-resample whole-project cluster bootstrap 95% CI** (secondary sensitivity: equal-project-weighted mean difference).
   - Minimum practically meaningful effect size: $\delta_{\min} = +5.0\text{ pp}$ for end-to-end yield comparisons.
   - Hypothesis testing: paired project-level sign-flip permutation test under $H_0$ of symmetric project-level paired differences across independent repositories, with Holm-Bonferroni adjustment within pre-specified families:
     - *Family 1 (Operational Yield per model, 3 tests)*: `MuGuard` vs `B0`, `B3`, `B3+`.
     - *Family 2 (Held-Out External Yield $Y_s$ per model, 3 tests)*: `MuGuard` vs `B0`, `B3`, `B3+`.
     - *Family 3 (Witness Mechanism, 1 primary test)*: `A3` vs `A1` (per model and equal-model-weighted cluster bootstrap across the 3 `K=5` models).

---

## 6. Protocol Revision Log (`v2.1.0-audited` — Post-Audit Integrity Hardening)

Following an independent forensic code-and-artifact audit of `v2.0.0-frozen`, the following clarifications and harness fixes were applied without adding new LLM repair queries or altering confirmatory strategy hypotheses:

1. **PR-Linked Cohort Expansion (`47 -> 108` instances, `26 -> 50` distinct PR URLs)**:
   - Section 2.1 originally cited the `47` PR-linked instances present in `v1` IDoFT metadata. During `v2` provenance resolution against the full `idoft` CSV, all `108` Java instances with an upstream GitHub PR URL (`50` distinct PRs) were indexed in `data/pr_reference_manifest.json` (`PR_Accepted_TestOnly`, `PR_Accepted_WithProd`, `PR_Opened`, `PR_DeveloperWontFix`).
   - Strict **all-or-nothing test-file application** is enforced (`applied_test_files_count == len(test_files_in_pr)`); partial multi-file applies are logged as `PARTIAL_TEST_APPLY_FAILED` and excluded from reference evaluation.
   - Post-hoc, author diff-grounded semantic adjudication (`data/pr_independent_adjudication_input.json`) is stored externally and decoupled from any automatic retention-threshold rule in code (auditing both $R_{3\times} < 1.0$ and $R_{3\times} = 1.0$ fixes); `TEST_RESPONSIBILITY_SHIFT` is excluded from the primary legitimate-repair denominator and reported separately in sensitivity.
2. **Unified `B1` Evaluation & `iPFlakies` Partner Verification**:
   - All `FlakyDoctor` (`gpt` and `magicoder`) Java patches are evaluated through the unified `v2.1` evaluator (`evaluate_java_v2_in_loop`) rather than legacy `remote_core.py`, ensuring identical node attribution and closed-fail partner gating.
   - Primary `iPFlakies` `B1` cohort requires BOTH `in_official_patches_csv == True` AND `csv_partner_matches_benchmark == True` (the `3` partner-mismatched CSV rows are excluded from primary `B1` metrics and logged separately).
3. **Exact Node Attribution, JUnit 3 Isolation, & Per-Mutant `UNKNOWN` Preservation**:
   - Java PIT attribution requires exact `METHOD_EXACT` (`test_fqcn` + exact `test_method`) or `CLASS_ISOLATED_EXACT` (`killingTest == test_fqcn` for JUnit 4 `@BeforeClass`/`ClassRule` tests under single-method isolation), plus JUnit 3 `extends TestCase` sibling-method isolation. PIT XML does not separate `@Before` setup failures from `@Test` body failures (explicitly disclosed in Threats to Validity).
   - Python `pytest` attribution requires exact full-nodeid matching (preventing substring collisions such as `test_loading` vs. `test_loading_failure`), explicit `teardown` failure checks, and paired `partner_node_hint` verification on `OD-brittle` runs.
   - Non-kill timeouts, setup/teardown errors, and missing mutant reports are preserved explicitly as per-mutant `UNKNOWN` counts and never collapsed into `SURVIVED`.
4. **Java `OD` Held-Out Schedule & `M_ext` Stratification Boundaries**:
   - Java `OD` held-out stability executes all 4 protocol schedules (`standalone 2x`, `partner->target 2x`, `target->partner 2x`, and same-JVM `partner->partner->target 1x`).
   - Java `M_ext` stratification (`M_ext_op_value_flow` vs. `M_ext_op_call`) is accurately labeled as **mutator-family stratification** (not exception-class assertion-only kill attribution, which is directly measured on Python `M_ext` via `pytest` hooks).
   - `M_ext_instance` (Python) is scoped explicitly as **within-instance held-out** (`24` mutants overlap gate mutant pools of *other* tests in the same repository, never the same test).
5. **Fixed External Cohort $E$, Exact `ITT-208%`, & Full `n = 110` Forked Ablation Recovery**:
   - `ITT-208%` counts successes strictly from eligible instances (`0` for excluded attrition tests).
   - External cohort $E$ is fixed to the frozen eligible set possessing $|M_{\text{ext}}| \ge 1$ across all three `K = 5` models (reporting API-incomplete and per-mutant `UNKNOWN` counts with `[worst, best]` bounds and completer-only sensitivity).
   - All `110` eligible `r_0 < 5` triggers (`38` Flash, `37` Pro, `35` Qwen) are retained in primary `A3 vs A1` (pooled, per-model, and equal-model-weighted cluster bootstrap), with the `3` `deepseek-v4-pro` cases whose `A2` branch hit the API token cap reported separately (`A2` on `n = 107`). Character-length differences between `A2` and `A3` and cached main-trajectory reuse for `A1`/`A3` are explicitly disclosed.

---

## 7. Protocol Revision Log (`v2.2.0-reconciled` — Full Trajectory, Identity, & Gate Reconciliation)

Following Round-4 independent adversarial auditing, the following measurement, identity, and trajectory reconciliations were applied and locked behind automated fail-closed verification (`src/verify_v22_reconciliation.py`):

1. **Exact Attribution & Lifecycle Precedence (`M2`)**:
   - Java PIT attribution (`_parse_pit_killing_test` in `src/v2_guards_and_attribution.py`) strictly parses JUnit 5 engine paths and JUnit 3/4 `pkg.Cls.method(pkg.Cls)` / bare forms with zero substring matching (`other.com.acme.FooTest` never matches `com.acme.FooTest`).
   - Python `pytest` attribution (`src/v2_pytest_attribution_runner.py`) distinguishes explicit parameterized instances (`test_x[A]` vs. `test_x[B]`) and checks `teardown` failure strictly **before** `call` kill credit (`call AssertionError + teardown RuntimeError` yields `UNKNOWN_TEARDOWN_FAIL`).
2. **Lossless 8-Tuple Java Mutant Identity Across All PIT Versions (`M3`)**:
   - PIT XML parsing (`extract_pit_xml_indexes_and_blocks`) reads both `<index>`/`<block>` (PIT 1.7.4) and `<indexes><index>`/`<blocks><block>` (PIT 1.9.11/1.15.2) across all `110` eligible Java samples, keying in-loop and held-out evaluations on lossless 8-tuple `exact_id` with fail-closed `UNKNOWN_AMBIGUOUS_MUTANT_ID` on conflicting duplicates.
    - All `1,201/1,201` eligible held-out entries evaluated in `v2.2.0-reconciled` were re-executed live under lossless `exact_id` + single-lifecycle Java (`SingleClassRepeatedRunner`, falling back to same-JVM `Request.method` strictly for custom `@RunWith(...)` classes) and single-session Python (`pytest_collection_modifyitems` cloning the partner item via `from_parent` so module/class fixtures execute strictly once). (In `v2.3.0-certified`, this repeated-pollution protocol was further hardened to exact lifecycle verification plus matched unmodified-repository controls; see Section 8.)
3. **Full Dual-Role Affected-Scope PR Gate & Unified `iPFlakies` (`M4` & `M5`)**:
   - All `24` $W^+$-passing `TEST_ONLY_PR` Java `OD` PRs are evaluated through the full dual-role affected-scope gate (`target 3x` + `analyze_affected_test_scope` + `partner 3x` + uncovered-partner closure + unknown closure), alongside controlled `visualee` `@Before clear()` ablation diagnostics (`data/v22_visualee_diagnostic.json`).
   - All `77` partner-matched Python `iPFlakies` `B1` entries are evaluated through the unified `v2.2` Python evaluator preserving `ok_m`, `target_unknown_count`, `partner_unknown_count`, and raw per-mutant statuses (`0` target/partner unknowns).
4. **Full `B3+` & `WriteDuplicateType` Trajectory Reconciliation + Stale-Bytecode Root Cause (`M1`)**:
   - Forensic replay proved that all `9` $W^+$ failures on `WriteDuplicateType` in `v21_six_sample_inloop_audit.json` stemmed from JDK 8 `maven-compiler-plugin`'s 1-second timestamp resolution skipping recompilation when `restore_class_files()` restored `.class` files with current wall-clock `mtime` in the same second as the next candidate write. Fixed via `backup_class_files(..., invalidate=True)` + old-`mtime` preservation (`test_19` in `test_harness_regressions.py`). Under stale-bytecode-free replay, `9/9` `WriteDuplicateType` candidates pass deterministic NonDex `910001..910005`, `26/27` $W^+$-passing `WriteDuplicateType` candidates exhibit zero retention flips, and the single real JUnit-3 sibling-isolation flip (`deepseek-v4-pro :: fastjson::WriteDuplicateType.test_dupType2`, candidate `3c55f3e4fb`: `1.0 -> 0.75`) is genuinely regenerated across all affected strategies (`B2`, `B3`, `B3+`, `B4`, `Ours`); because regenerated `B4` and `Ours` reached `UNSTABLE_RETRY` in Round 1 and `SAFE_PASS` (`1.0`) in Round 2 without hitting `EROSION_RETRY`, `WriteDuplicateType.test_dupType2` does not enter the prefix-forked erosion-recovery ablation (whereas `WriteDuplicateType.test_dupType` is enrolled across all three models).
   - All `4` `r1` `K=5` `B3+` guard divergences (`Flash::ljson`, `Pro::Algorithms`, `Qwen::ClusterViewTest`, `Qwen::ljson`) are genuinely regenerated from Round 1 under current `B3+` rules (plus the `3` terminal `r5`/`K=1` updates and held-out verification of every newly accepted candidate), yielding `0` `B3+` mismatches across all 5 models, archived in `data/v22_reconciliation_manifest.json`.
5. **Per-Model / Per-Strategy Test-Level Semantic `M_ext` Endpoints (`M6`)**:
   - Adjudicated `M_ext` losses are computed directly per model, per strategy, and per test on fixed cohort $|E| = 177$, reporting `GENUINE_ORACLE_WEAKENING`, `TEST_RESPONSIBILITY_SHIFT`, and `LEGITIMATE_RELAXATION_OR_CANONICALIZATION` separately (never conflated).
   - Post-hoc attrition timing (`4` pre-confirmatory in `v2.0.0-frozen` + `1` post-hoc attribution exclusion of `koryphe::StringJoinTest`), post-hoc PR adjudication timing, exact non-zero repository counts per model, and candidate-fingerprint deduplication wording are explicitly aligned across all artifacts.

---

## 8. Protocol Revision Log (`v2.3.0-certified` — Complete Fresh `3/3` Java Re-Freeze, Exact Repeated-Pollution Controls, Atomic PR Dual-Role Gate, & Full Trajectory Reconciliation)

Following Round-5 and Round-6 independent adversarial auditing (`R5-1` through `R5-5`, `R6-1` and `R6-2`), every remaining cross-version measurement and repeated-pollution phase-verification gap was closed end-to-end and locked behind a 19-test unit/synthetic regression suite (`src/test_harness_regressions.py`), live JVM JUnit 4 phase counterexamples (`data/v23_r6_live_java_counterexamples.json`), and a 10-invariant hard-failing certification verifier (`src/verify_v23_certification.py`):

1. **100% Fresh `3/3` Clean-$e_0$ PIT Re-Freeze on All `110` Eligible Java Tests (`R5-1`)**:
   - Eliminated all legacy empty-index PIT 1.9.11/1.15.2 `exact_id` entries (`empty_idx = 0`, `dup_eids = 0`, `bad_gran = 0`, `bad_st = 0`, `missing_3x = 0`) by running fresh `3/3` clean-$e_0$ PIT baselines (`M_det`, `polluter_m_det`, and `M_ext`) under lossless 8-tuple XML parsing across all `110` eligible Java tests (`exec_benchmark_sha256` prefix `eecf64845bb3810e`; `3,249` Java target `M_det`, `371` Java partner `M_det`, `3,262` Java `M_ext`, `0` tests dropped below `3` kills; pool diffs archived in `data/v23_stage1_pool_diff.json`: `48` `M_det`, `12` partner, and `55` `M_ext` pools updated).
2. **Full Endpoint, Trajectory, Forked Ablation, & `B1` Reconciliation (`R5-2`)**:
   - All `429` unique `W+`-passing in-loop Java candidates across the `51` `M_det`/partner-changed samples and all `371` accepted `K=5` candidates across the `55` `M_ext`-changed samples were re-evaluated on the fresh `2.3.0-certified` pools (`0` denominator mismatches across all 5 models).
   - Every trajectory across all 5 models (`3` `K=5` + `2` `K=1`) whose decision or witness set changed was genuinely regenerated via `run_v2_strategy_loop` (`172` live LLM calls, `163` endpoint outcome changes, `2` `deepseek-v4-pro` 16k reasoning-cap omissions cleanly dropped and scored `0` in `ITT-208%` and Fixed $Y_s$; `34` forked ablation updates across `n = 128` eligible forks; `42` newly accepted `K=5` candidates live-evaluated on remote; full audit trail in `data/v23_reconciliation_manifest.json`).
   - All `165` authoritative `B1` entries (`75` `FlakyDoctor` GPT-4 `:P`, `13` Magicoder `:P`, `77` partner-matched `iPFlakies`) are evaluated through the single unified `2.3.0-certified` evaluator (`0` legacy `2.1.0-audited` rows remain; archived in `data/b1_provenance_manifest.json`).
3. **Exact Single-Lifecycle Repeated-Pollution Phase Verification & Matched Unmodified-Repo Controls (`R5-3`, `R5-4`, `R6-1`, `R6-2`)**:
   - Upgraded both Java (`FT1HeldoutRepeatedRunner` verifying exact `p1 -> p2 -> target` order and overriding `BlockJUnit4ClassRunner`'s `withBefores`, `possiblyExpectingExceptions`, and `withAfters` to emit per-slot `setup`/`call`/`teardown`/`outer_fail`/`call_exc` verdicts via `FT1_REP_PHASE_KNOWN` and `FT1_REP_PHASES`; custom `@RunWith(...)` classes fall back to same-JVM `Request.method` with `phase_level_known = false`, strictly forbidding `Stress` exemption whenever any failure occurs; evaluated by `evaluate_java_repeated_pollution_report`, covered by `test_21_r61_java_repeated_pollution_phase_lifecycle` plus live JVM JUnit 4 counterexamples in `data/v23_r6_live_java_counterexamples.json`) and Python (`pytest_collection_modifyitems` + `pytest_runtest_protocol` + `pytest_runtest_logreport` verifying exact full nodeid, role, execution order `p1 < p2 < target`, and `setup == passed` & `teardown == passed` on ALL three roles; covered by `test_20_r53_py_repeated_pollution_exact_lifecycle`).
   - Executed matched unmodified upstream repository controls (`partner -> partner -> target`, sequentially per repo with `mvn test-compile` first) across all `142` eligible `OD` tests with uniform fail-closed `is_unknown` accounting (archived in `data/v23_upstream_repeated_pollution_controls.json`):
     - *Python (`93` attempted)*: `90` valid (`is_unknown = false`), `3` fail-closed `is_unknown = true` (`2` `py-autodoc` teardown errors, `1` `fooof` setup skip), `27` verified non-idempotent `p2` `call`-only controls across `11` repositories.
     - *Java (`49` attempted)*: `49/49` exact `p1 -> p2 -> target` execution order (`0` runner/compile failures), `43` phase-verified valid (`is_unknown = false`), `6` fail-closed `is_unknown = true` (`3` `cloud-slang` custom `@RunWith` lacking phase hooks -> `UNKNOWN_PHASE_UNAVAILABLE`; `3` `marine-api` constructor `<init>` failures prior to `@Before` -> `UNKNOWN_TARGET_SETUP_PENDING`), `5` phase-verified non-idempotent `p2` `call`-only controls across `2` repositories (`4` `jnr-posix` [`java.lang.AssertionError`] + `1` `shardingsphere-elasticjob` [`com.google.gson.JsonParseException`]). Zero Stress exemptions are authorized on any of the `9` unknown controls (all `39/39` held-out candidates on those `9` controls pass `strict = true`).
   - Verified all `758` eligible OD held-out evaluations (`480` Python + `278` Java; `0` unknown repeated-pollution verdicts; all `73` Stress exemptions [`72` Python + `1` Java `shardingsphere-elasticjob::AppConstraintEvaluatorTest.assertExistExecutorOnS0::6d6c8d30033f205785` with phase-verified `p2` `setup=PASS, call=FAIL (com.google.gson.JsonParseException), teardown=PASS`] hard-checked by `verify_v23_certification.py` Check 5) and upgraded all held-out statistics and tables (`Table 4b`, `Table 6`) to report **Strict All-Pass $Y_s^{\text{strict}}$** as primary alongside **Matched-Control Target-Stress Sensitivity $Y_s^{\text{stress}}$** (permitting a `p2` `call` failure solely when the matched unmodified upstream control is valid [`is_unknown = false`] with `p1` passing and `p2` `call`-only failure, all `setup`/`teardown` phases pass in the candidate run—requiring `phase_level_known = true` and exact `p2` exception-class equality in Java and `p2` `call`-only failure-state match in Python—and the candidate leaves the partner unmodified).
4. **Atomic Same-Tree Full Dual-Role PR Gate (`R5-5`)**:
   - Re-evaluated all `45` cleanly applied `TEST_ONLY_PR` Java instances (single `applied_tree_sha256` across `W+`, target `3x` PIT, and partner `3x` PIT; `extra_files_edited > 0` folded into `affects_partner_or_shared`, archived in `data/pr_reference_manifest.json` and `data/pr_independent_adjudication_input.json`). Under fresh `3/3` pools, `43` pass `W+` (`14/14` `http-request` pass `W+` and `1.0` target/partner retention; `visualee` PR #8 exposes `5` pure `@Before clear()` false rejections alongside `3` `fastjson` canonicalizations; `nacos` genuine weakening caught).
5. **100% Symmetric `M_ext`-Loss Adjudication (`0` Unadjudicated)**:
   - All `111` active unique `M_ext`-loss patches across all six `K=5` strategies (`0` unadjudicated) are adjudicated symmetrically under the locked contract rubric in `data/mext_loss_adjudication_input.json`, transparently classifying the `3` newly surfaced `visualee::GraphCreatorTest` `Ours` patches (where Round-2 synthetic `DependencyFilter` fluent-return assertions satisfied the 3 `M_det` `NullReturnValsMutator` witnesses without checking mutated internal fields) as non-legitimate `TEST_RESPONSIBILITY_SHIFT`.

