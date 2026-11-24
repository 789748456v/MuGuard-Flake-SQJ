import re
from typing import Any, Dict, List, Optional, Tuple


ASSERT_CALL_RE = re.compile(
    r"\b(?:assert[A-Z][A-Za-z0-9_]*|fail|verify|verifyNoMoreInteractions|verifyZeroInteractions|assertThat|assertThatJson|check[A-Z][A-Za-z0-9_]*)\s*\("
)
TAUTOLOGY_RE = re.compile(
    r"\b(?:assertTrue\s*\(\s*true\s*\)|assertFalse\s*\(\s*false\s*\)|assertNull\s*\(\s*null\s*\))"
)
IGNORE_RE = re.compile(
    r"(?:@(?:org\.junit\.)?Ignore\b|@(?:org\.junit\.jupiter\.api\.)?Disabled\b|\bassumeTrue\s*\(\s*false\s*\)|\bassumeFalse\s*\(\s*true\s*\))"
)
CATCH_SWALLOW_RE = re.compile(
    r"catch\s*\(\s*(?:final\s+)?(?:AssertionError|Throwable|Error|Exception)\s+[A-Za-z0-9_]+\s*\)\s*\{\s*(?://[^\n]*\s*|/\*.*?\*/\s*)*\}",
    re.DOTALL,
)
CATCH_ASSERTION_ERR_RE = re.compile(
    r"catch\s*\(\s*(?:final\s+)?(?:AssertionError|Throwable|Error)\b"
)


def strip_comments_and_strings(code: str) -> str:
    """Remove Java comments and string literals to avoid false regex matches."""
    pattern = re.compile(
        r'//.*?$|/\*.*?\*/|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'',
        re.DOTALL | re.MULTILINE,
    )

    def repl(m: re.Match) -> str:
        s = m.group(0)
        if s.startswith("//") or s.startswith("/*"):
            return " "
        return '""'

    return pattern.sub(repl, code)


def extract_method_body_statements(method_code: str) -> str:
    cleaned = strip_comments_and_strings(method_code)
    brace_idx = cleaned.find("{")
    rbrace_idx = cleaned.rfind("}")
    if brace_idx == -1 or rbrace_idx == -1 or rbrace_idx <= brace_idx:
        return ""
    return cleaned[brace_idx + 1 : rbrace_idx].strip()


def is_non_empty_method(method_code: str) -> bool:
    body = extract_method_body_statements(method_code)
    return len(body.replace(";", "").strip()) > 0


def count_assertions(method_code: str) -> int:
    cleaned = strip_comments_and_strings(method_code)
    return len(ASSERT_CALL_RE.findall(cleaned))


def check_static_ast_guard(
    orig_methods: Dict[str, str],
    cand_methods: Dict[str, str],
) -> Tuple[bool, List[str]]:
    """
    Programmatic Static AST Guard (B3):
    Enforces non-decreasing assertion count, bans tautologies (assertTrue(true)),
    bans downgrading value equality to assertNotNull, bans @Ignore/@Disabled/assumeFalse,
    and bans empty catch or catching AssertionError/Throwable.
    """
    violations: List[str] = []
    for mname, orig_src in orig_methods.items():
        cand_src = cand_methods.get(mname)
        if not cand_src:
            continue
        if not is_non_empty_method(cand_src):
            violations.append(f"Method `{mname}` has an empty method body.")
            continue

        orig_clean = strip_comments_and_strings(orig_src)
        cand_clean = strip_comments_and_strings(cand_src)

        if IGNORE_RE.search(cand_clean) and not IGNORE_RE.search(orig_clean):
            violations.append(f"Method `{mname}` introduces `@Ignore`/`@Disabled` or `assumeTrue(false)`.")

        if TAUTOLOGY_RE.search(cand_clean) and not TAUTOLOGY_RE.search(orig_clean):
            violations.append(f"Method `{mname}` introduces tautological assertion (`assertTrue(true)` / `assertFalse(false)`).")

        if CATCH_SWALLOW_RE.search(cand_clean):
            violations.append(f"Method `{mname}` contains an empty `catch` block swallowing exceptions.")

        if CATCH_ASSERTION_ERR_RE.search(cand_clean) and not CATCH_ASSERTION_ERR_RE.search(orig_clean):
            violations.append(f"Method `{mname}` catches `AssertionError`/`Throwable`/`Error`.")

        orig_asserts = count_assertions(orig_src)
        cand_asserts = count_assertions(cand_src)
        if cand_asserts < orig_asserts:
            violations.append(
                f"Method `{mname}` reduced assertion count from {orig_asserts} to {cand_asserts} (assertion count must not decrease)."
            )

        orig_eq = len(re.findall(r"\b(?:assertEquals|assertArrayEquals|isEqualTo)\s*\(", orig_clean))
        cand_eq = len(re.findall(r"\b(?:assertEquals|assertArrayEquals|isEqualTo)\s*\(", cand_clean))
        orig_nn = len(re.findall(r"\b(?:assertNotNull|isNotNull)\s*\(", orig_clean))
        cand_nn = len(re.findall(r"\b(?:assertNotNull|isNotNull)\s*\(", cand_clean))
        if orig_eq > 0 and cand_eq == 0 and cand_nn > orig_nn:
            violations.append(
                f"Method `{mname}` downgraded exact equality assertions (`assertEquals`/`isEqualTo`) to weak `assertNotNull` checks."
            )

        # Check identical-argument assertEquals(x, x)
        for m in re.finditer(r"\bassertEquals\s*\(\s*([^,()]+?)\s*,\s*([^,()]+?)\s*\)", cand_clean):
            a1, a2 = m.group(1).strip(), m.group(2).strip()
            if a1 and a1 == a2 and a1 != '""':
                violations.append(f"Method `{mname}` contains self-comparing tautology `assertEquals({a1}, {a2})`.")

    return (len(violations) == 0), violations


PROMPT_GUARD_CONTRACT = """
CRITICAL ORACLE-PRESERVATION CONTRACT (STRICTLY ENFORCED):
1. Do NOT delete, comment out, or reduce the number of assertions in any test method.
2. Do NOT weaken exact value, key-set, element-count, multiplicity, or structural completeness checks into partial checks (e.g., NEVER replace full JSON/Map/Collection/String equality with `assertNotNull`, partial `.contains(...)` without exact size/element verification, checking only a subset of JSON keys, or `JSONAssert.assertEquals(..., false)` with `strict=false`).
3. Do NOT swallow exceptions with `try-catch`, add `@Ignore`/`@Disabled`, or reset/clear/overwrite the system-under-test state AFTER test setup and BEFORE assertions execute.
4. For ID tests: convert both expected and actual structures into an order-insensitive but COMPLETE representation (e.g., `JsonParser`/`ObjectMapper.readTree` full tree equality, `Map`/`Set`, or deterministically sorted `List` with exact size & element equality).
5. For OD tests: clean/reset shared polluted state ONLY at the VERY START of the victim test (before the victim sets up its own test data) or in a `finally` block at the VERY END of the polluter test (after all polluter assertions finish).
""".strip()


def parse_llm_patch_response(raw_text: str, test_type: str) -> Dict[str, Any]:
    """Extract fixed method code, imports, and pom dependency from LLM output."""
    def extract_between(text: str, start_tag: str, end_tag: str) -> Optional[str]:
        s = text.find(start_tag)
        if s == -1:
            return None
        e = text.find(end_tag, s + len(start_tag))
        if e == -1:
            return None
        val = text[s + len(start_tag) : e].strip()
        # Strip accidental markdown fences inside tags
        val = re.sub(r"^```[a-zA-Z]*\s*", "", val)
        val = re.sub(r"\s*```$", "", val)
        return val.strip() or None

    imports_raw = extract_between(raw_text, "//<import start>", "//<import end>")
    imports: List[str] = []
    if imports_raw:
        for ln in imports_raw.splitlines():
            ln = ln.strip()
            if ln.startswith("import ") and ln.endswith(";"):
                imports.append(ln)

    pom_raw = extract_between(raw_text, "<!-- <pom.xml start> -->", "<!-- <pom.xml end> -->")

    if test_type == "ID":
        fix_code = extract_between(raw_text, "//<fix start>", "//<fix end>")
        if not fix_code:
            # Fallback: extract java code block if tags omitted
            m = re.search(r"```(?:java)?\s*(.*?)```", raw_text, re.DOTALL)
            if m and ("void " in m.group(1)):
                fix_code = m.group(1).strip()
        return {
            "test_code": fix_code,
            "imports": imports,
            "pom": pom_raw,
        }
    else:
        vic_code = extract_between(raw_text, "//<victim start>", "//<victim end>")
        pol_code = extract_between(raw_text, "//<polluter start>", "//<polluter end>")
        if not vic_code:
            vic_code = extract_between(raw_text, "//<fix start>", "//<fix end>")
        return {
            "victim_code": vic_code,
            "polluter_code": pol_code,
            "imports": imports,
            "pom": pom_raw,
        }


def build_repair_prompt(
    strategy: str,
    sample: Dict[str, Any],
    round_idx: int,
    prev_candidate: Optional[Dict[str, Any]] = None,
    feedback_msg: str = "",
) -> str:
    """
    Construct the repair prompt with identical FlakyDoctor-style localization context
    across B0, B2, B3, and Ours_MuGuard_Flake.
    """
    ttype = sample["test_type"]  # "ID" or "OD"
    use_guard_text = strategy in ("B2_Prompt_Guard", "B3_Static_AST_Guard", "Ours_MuGuard_Flake")

    if ttype == "ID":
        base_desc = (
            "Implementation-Dependent (ID) flaky tests fail non-deterministically when code assumes "
            "deterministic iteration/serialization order from unordered APIs (e.g., HashMap, HashSet, "
            "getDeclaredFields, JSON object key order, query-string parameter order).\n"
        )
        loc_block = (
            f"Target Flaky Test Method: `{sample['test_full_name']}`\n"
            f"Test Class Imports & Fields/Helpers Context:\n```java\n{sample['context_header']}\n```\n"
            f"Original Flaky Test Method:\n```java\n{sample['orig_test_code']}\n```\n"
            f"NonDex Failure Diagnostic:\n```\n{sample['flaky_error_summary']}\n```\n"
            f"Potential Non-Deterministic Lines Flagged:\n```\n{sample.get('potential_lines', '')}\n```\n"
        )
        format_instructions = (
            "Reply with ALL code sections clearly delimited using the exact tags below (no extra prose needed):\n"
            "1) Complete fixed test method (including `@Test` annotation if present in original, method signature, and body; do NOT define extra outer methods):\n"
            "//<fix start>\n...complete Java test method...\n//<fix end>\n"
            "2) Additional Java import statements if needed (leave empty if none):\n"
            "//<import start>\n//<import end>\n"
            "3) Additional Maven `<dependency>` block if strictly needed (prefer existing project/JDK libraries first; leave empty if none):\n"
            "<!-- <pom.xml start> -->\n<!-- <pom.xml end> -->"
        )
    else:
        base_desc = (
            "Order-Dependent (OD) flaky tests fail when a polluter test modifies shared static/singleton/global/external "
            "state that a subsequent victim test depends on.\n"
        )
        loc_block = (
            f"Victim Flaky Test: `{sample['test_full_name']}`\n"
            f"Polluter Test: `{sample['polluter_full_name']}`\n"
            f"Shared Class Context (Imports, Fields, @Before/@After Fixtures):\n```java\n{sample['context_header']}\n```\n"
            f"Original Victim Test Method:\n```java\n{sample['orig_test_code']}\n```\n"
            f"Original Polluter Test Method:\n```java\n{sample['orig_polluter_code']}\n```\n"
            f"Order-Execution (`polluter -> victim`) Failure Diagnostic:\n```\n{sample['flaky_error_summary']}\n```\n"
        )
        format_instructions = (
            "Reply with ALL code sections clearly delimited using the exact tags below (no extra prose needed):\n"
            "1) Complete fixed victim test method (including annotations and signature):\n"
            "//<victim start>\n...complete Java victim method...\n//<victim end>\n"
            "2) Complete polluter test method (include ONLY if you also add cleanup to polluter; otherwise repeat original polluter unchanged):\n"
            "//<polluter start>\n...complete Java polluter method...\n//<polluter end>\n"
            "3) Additional Java import statements if needed (leave empty if none):\n"
            "//<import start>\n//<import end>\n"
            "4) Additional Maven `<dependency>` block if strictly needed (leave empty if none):\n"
            "<!-- <pom.xml start> -->\n<!-- <pom.xml end> -->"
        )

    guard_section = f"\n{PROMPT_GUARD_CONTRACT}\n" if use_guard_text else (
        "\nDo not use empty method bodies or try-catch to swallow assertion errors; make sure the test compiles and passes deterministically.\n"
    )

    round_section = ""
    if round_idx > 1 and prev_candidate is not None:
        prev_str = (
            prev_candidate.get("test_code")
            if ttype == "ID"
            else f"Victim:\n{prev_candidate.get('victim_code')}\n\nPolluter:\n{prev_candidate.get('polluter_code')}"
        )
        round_section = (
            f"\n=== ITERATION {round_idx} FEEDBACK ON YOUR PREVIOUS CANDIDATE ===\n"
            f"Your Round {round_idx - 1} candidate was:\n```java\n{prev_str}\n```\n"
            f"Verification Feedback / Rejection Reason:\n{feedback_msg}\n"
            f"Please produce a corrected repair addressing the exact issue above.\n"
        )

    return (
        "You are an expert Java software testing engineer repairing a flaky test.\n"
        + base_desc
        + loc_block
        + guard_section
        + round_section
        + "\n"
        + format_instructions
    )


def format_mutant_witness_feedback(survived_mutants: List[Dict[str, Any]], mode: str = "full") -> str:
    """
    Construct Differential Mutant Witness Feedback for MuGuard-Flake (EROSION_RETRY).
    Supports 3 feedback granularities for RQ3 N14-style diagnostic ablation:
      - mode == "full": concrete surviving focal mutants (class, method, line, source slice, mutator)
      - mode == "binary": binary score-drop signal ONLY (no class/method/line/mutator location)
      - mode == "placebo": N14 mismatched mutant control (caller passes non-surviving mutant(s))
    """
    if mode == "binary":
        return (
            "STATUS: EROSION_RETRY — Your candidate patch passed stability validation (W+), "
            f"but SILENTLY ERODED the test oracle: {len(survived_mutants)} deterministic focal mutant(s) "
            "that the original test killed 3/3 times in clean state NOW SURVIVED!\n"
            "Restore full deterministic assertion and SUT setup strength while keeping W+ stability."
        )
    lines = [
        "STATUS: EROSION_RETRY — Your candidate patch passed stability validation (W+), "
        f"but SILENTLY ERODED the test oracle: {len(survived_mutants)} deterministic focal mutant(s) "
        "that the original test killed 3/3 times in clean state NOW SURVIVED!",
        "",
        "Surviving Deterministic Mutant Counterexample Witness(es):",
    ]
    for idx, m in enumerate(survived_mutants[:4], start=1):
        lines.append(
            f"  [Mutant #{idx}] Focal Class `{m['mutatedClass']}` :: `{m['mutatedMethod']}` "
            f"(Line {m['lineNumber']}, Mutator: `{m['mutator'].split('.')[-1]}`)\n"
            f"    Mutation Effect: {m['description']}\n"
            f"    Production Source Slice around Line {m['lineNumber']}:\n"
            f"    ```java\n{m.get('source_slice', '(source line ' + str(m['lineNumber']) + ')')}\n    ```"
        )
    if len(survived_mutants) > 4:
        lines.append(f"  ... plus {len(survived_mutants) - 4} additional surviving deterministic focal mutant(s).")

    lines.extend([
        "",
        "MINIMAL-SCOPE REPAIR REQUIREMENT:",
        "- Keep your non-deterministic stabilization (order-insensitivity for ID, or pre-test/post-polluter state cleanup for OD),",
        "- BUT restore full deterministic predicate strength so EVERY surviving mutant above fails an assertion again!",
        "- Specifically: check exact collection/map/JSON key-set size, all field values, element multiplicities, and ensure state resets happen ONLY BEFORE setting up the test's own SUT state (or AFTER polluter's assertions in `finally`), never wiping out the state under test before asserting.",
    ])
    return "\n".join(lines)
