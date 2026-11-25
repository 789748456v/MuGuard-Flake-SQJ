"""
FT-1 v3.0 10-Session Physically Blinded Independent Codex Ensemble Audit
(`v3_codex_10session_blinded_audit.py`).

Executes R = 10 completely independent, stateless (`--ephemeral`), read-only,
order-permuted OpenAI Codex (`gpt-6-astra`) blinded audit sessions outside the
repository across all 224 semantic-audit items:
  1. PR_53 (all 53 cleanly applied developer pull requests; 43 W+-passing test-only)
  2. TAX_60 (all 60 active stratified in-loop eroded patches)
  3. HELDOUT_111 (all 111 unique held-out M_ext-loss patches with production source slices)

Blinding Guarantees:
  - Executes inside an empty directory outside the repository (`--skip-git-repo-check`,
    `-s read-only`, `--ephemeral`), preventing access to repository files, paper text,
    strategy names, model identities, gate verdicts, PR merge statuses, Stage-1 labels,
    or other sessions' outputs, and preventing heuristic script shortcuts.
  - Each session s in 1..10 applies an independent Fisher-Yates shuffle (`seed = 20261100 + s`)
    before batching, eliminating presentation-order and context-anchoring bias.

Outputs (strictly additive under `research/FT1/data/`):
  - `data/v3_codex_10session_raw_runs.json` (checkpointed per batch; all 2,240 ratings + rationales)
  - `data/v3_codex_10session_blinded_audit.json` (Fleiss' kappa, per-session & consensus Cohen's kappa
    vs. Stage-1, vote-entropy distribution, boundary cases, and 10-session downstream invariance)
"""
import argparse
import json
import math
import os
import random
import re
import shutil
import statistics
import subprocess
import tempfile
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Tuple

FT1_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = FT1_ROOT / "data"
DOSSIER_DIR = FT1_ROOT / "human_audit_pack" / "diff_dossiers"
KEY_PATH = DATA_DIR / "v3_human_audit_key.json"
RAW_OUT_PATH = DATA_DIR / "v3_codex_10session_raw_runs.json"
SUMMARY_OUT_PATH = DATA_DIR / "v3_codex_10session_blinded_audit.json"

SAVE_LOCK = threading.Lock()

PR_LABELS = [
    "CONTRACT_PRESERVING_FIX",
    "LEGITIMATE_RELAXATION",
    "GENUINE_ORACLE_WEAKENING",
    "TEST_RESPONSIBILITY_SHIFT",
    "INSUFFICIENT_EVIDENCE",
]

TAX_MECH_LABELS = [
    "M1_PredicateProjectionOrRelaxation",
    "M2_StateResetOrClobber",
    "M3_ControlFlowOrExceptionChange",
    "M4_SUTCallAlterationOrBypass",
    "M5_PartnerOrSharedScopeEdit",
    "M6_DelimiterOrFormatNormalization",
    "M7_PreconditionOrSingletonSetupAlteration",
]

TAX_SEV_LABELS = [
    "GENUINE_DEGRADATION",
    "TEST_RESPONSIBILITY_SHIFT",
    "LEGITIMATE_RELAXATION_ARTIFACT",
]

HO_WSL_LABELS = [
    "GENUINE_ORACLE_WEAKENING",
    "TEST_RESPONSIBILITY_SHIFT",
    "LEGITIMATE_RELAXATION_OR_CANONICALIZATION",
]

DECISION_TREE_RUBRIC = """
### Canonical Zero-Ambiguity Decision Tree (Apply Step 1 -> Step 2 -> Step 3 in strict order):

- **Step 1 — Legitimate Canonicalization / Relaxation (`LEGITIMATE_RELAXATION`, `LEGITIMATE_RELAXATION_ARTIFACT`, or `LEGITIMATE_RELAXATION_OR_CANONICALIZATION`)**:
  Assign ONLY if ALL three hold:
  (a) The edit strictly eliminates a genuine nondeterminism root cause (e.g., deterministic sorting/TreeMap/JSON object comparison for unordered collections/JSON, resetting polluted state *before* the test's own setup, or order-insensitive set/multiset comparison that preserves element multiplicity and key-to-value binding);
  (b) Every core production invariant originally verified by the test (key-to-value bijection, field count/multiplicity, delimiter/trailing-separator integrity, null/boundary branches, target production call path) remains fully enforced;
  (c) Any surviving/lost mutants arise purely because deterministic canonicalization no longer traverses an incidental order-dependent branch (e.g., enabling `SortField` skips the unsorted serializer path) rather than accepting wrong production outputs.

- **Step 2 — Test Responsibility Shift (`TEST_RESPONSIBILITY_SHIFT`)**:
  If Step 1 does NOT hold, assign `TEST_RESPONSIBILITY_SHIFT` if ANY of the following holds:
  (a) **Output / Subject Substitution**: Stops checking the original serialized/formatted output (e.g., JSON/XML/SQL/HTTP body string) and instead asserts on a different internal object (e.g., in-memory `Schema` or raw `Map`);
  (b) **Production Entry / Collaborator Bypass**: Replaces the real production call or default collaborator with a mock, stub, no-op, or alternate helper, or removes the call to the focal production method;
  (c) **Precondition / Fixture Narrowing or Global Singleton Wipe**: Shrinks or alters input fixtures (e.g., reducing a multi-entry fixture to 1 entry so post-delete lookup paths become unreachable), or clears/resets a global singleton/registry/container so filter/registration branches are skipped;
  (d) **Partner / Shared-Scope Impairment**: Modifies a partner test or shared fixture/teardown in a way that alters or removes production behavior previously exercised by the partner or shared lifecycle.

- **Step 3 — Genuine Oracle Weakening / Degradation (`GENUINE_ORACLE_WEAKENING` or `GENUINE_DEGRADATION`)**:
  Otherwise, the test still invokes the focal production path on the intended output, but relaxes assertion or parsing fidelity so defective outputs can pass:
  - Replaces exact equality with partial substring (`contains`), non-null (`isNotNull`), key-set/size-only checks, or independent field checks that lose key-to-value binding or permit extra unexpected entries;
  - Tokenizes/normalizes strings in a lossy way (e.g., Java 1-arg `split(",")` dropping trailing empty tokens and masking trailing-separator bugs, or collapsing duplicates via `Set`/`TreeMap` when multiplicity matters);
  - Deletes, comments out, or weakens an assertion or exception check.
"""


def save_json_atomic(path: Path, obj: Any) -> None:
    tmp = path.with_suffix(f".{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
    for _ in range(5):
        try:
            tmp.replace(path)
            return
        except PermissionError:
            time.sleep(0.1)
    tmp.replace(path)


def parse_dossier_blocks(md_path: Path, prefix: str) -> Dict[str, str]:
    txt = md_path.read_text(encoding="utf-8")
    blocks: Dict[str, str] = {}
    pattern = re.compile(rf"(## ({prefix}-\d+)\b.*?)(?=\n## {prefix}-\d+\b|\Z)", re.S)
    for m in pattern.finditer(txt):
        full_block, bid = m.group(1).strip(), m.group(2).strip()
        blocks[bid] = full_block
    return blocks


def build_blinded_items(key: Dict[str, Any]) -> Dict[str, Dict[str, str]]:
    pr_blocks = parse_dossier_blocks(DOSSIER_DIR / "PR_53_diffs.md", "PR")
    tax_blocks = parse_dossier_blocks(DOSSIER_DIR / "TAX_60_diffs.md", "TAX")
    ho_blocks = parse_dossier_blocks(DOSSIER_DIR / "HELDOUT_diffs.md", "HO")
    pr_diff_cache = json.loads((DATA_DIR / "v3_pr_diff_cache.json").read_text(encoding="utf-8"))

    # Enrich PR blocks strictly with objective test-only W+ execution status + full blinded PR file diff hunks
    # (so class-level @Before/@After/partner/shared-fixture edits outside target method body are 100% visible,
    # while stripping commit headers, URLs, merge status, and gate verdicts!)
    pr_enriched: Dict[str, str] = {}
    for bid, blk in pr_blocks.items():
        meta = key["pr_53"][bid]
        is_test_only_wplus = (meta.get("pr_scope_type") == "TEST_ONLY_PR") and (meta.get("w_plus_passed") is True)
        status_line = (
            "- **Standalone Test-Only Stabilization (W+)**: PASSED (clean test-only execution stabilized)"
            if is_test_only_wplus
            else f"- **Standalone Test-Only Stabilization (W+)**: NOT STABILIZED AS TEST-ONLY (scope={meta.get('pr_scope_type')}, w_plus_passed={meta.get('w_plus_passed')})"
        )
        slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", meta["pr_url"].strip().rstrip("/")) + ".diff"
        raw_pr_diff = pr_diff_cache.get(slug, "")
        # Keep only pure `diff --git ...` sections (strip commit/author/URL headers)
        idx_git = raw_pr_diff.find("diff --git ")
        clean_pr_diff = raw_pr_diff[idx_git:].strip() if idx_git >= 0 else raw_pr_diff.strip()
        if len(clean_pr_diff) > 25000:
            clean_pr_diff = clean_pr_diff[:25000] + "\n... [truncated remaining lines of large PR diff]"

        lines = blk.splitlines()
        lines.insert(2, status_line)
        enriched_blk = "\n".join(lines) + f"\n\n**Complete Blinded PR File-Level Unified Diff (includes class/setup/teardown/partner files)**:\n```diff\n{clean_pr_diff}\n```"
        pr_enriched[bid] = enriched_blk

    assert len(pr_enriched) == 53, f"Expected 53 PR blocks, got {len(pr_enriched)}"
    assert len(tax_blocks) == 60, f"Expected 60 TAX blocks, got {len(tax_blocks)}"
    assert len(ho_blocks) == 111, f"Expected 111 HO blocks, got {len(ho_blocks)}"
    return {"PR_53": pr_enriched, "TAX_60": tax_blocks, "HELDOUT_111": ho_blocks}


def make_schema(task_name: str) -> Dict[str, Any]:
    if task_name == "PR_53":
        item_props = {
            "blind_id": {"type": "string"},
            "label": {"type": "string", "enum": PR_LABELS},
            "confidence": {"type": "integer", "enum": [1, 2, 3]},
            "rationale": {"type": "string"},
        }
        req = ["blind_id", "label", "confidence", "rationale"]
    elif task_name == "TAX_60":
        item_props = {
            "blind_id": {"type": "string"},
            "primary_mechanism": {"type": "string", "enum": TAX_MECH_LABELS},
            "semantic_severity": {"type": "string", "enum": TAX_SEV_LABELS},
            "confidence": {"type": "integer", "enum": [1, 2, 3]},
            "rationale": {"type": "string"},
        }
        req = ["blind_id", "primary_mechanism", "semantic_severity", "confidence", "rationale"]
    else:
        item_props = {
            "blind_id": {"type": "string"},
            "label_wsl": {"type": "string", "enum": HO_WSL_LABELS},
            "confidence": {"type": "integer", "enum": [1, 2, 3]},
            "rationale": {"type": "string"},
        }
        req = ["blind_id", "label_wsl", "confidence", "rationale"]

    return {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": item_props,
                    "required": req,
                    "additionalProperties": False,
                },
            }
        },
        "required": ["items"],
        "additionalProperties": False,
    }


def build_batch_prompt(task_name: str, batch_ids: List[str], blocks: Dict[str, str]) -> str:
    joined = "\n\n---\n\n".join(blocks[bid] for bid in batch_ids)
    id_list = ", ".join(batch_ids)
    if task_name == "PR_53":
        task_inst = f"""You are an independent, blinded software-testing auditor evaluating {len(batch_ids)} developer pull-request test patches (`{id_list}`).
Do NOT attempt to run shell commands; all information is self-contained below. Read each diff and lost-mutant list carefully and classify EVERY item into `label`:
1. `INSUFFICIENT_EVIDENCE`: Assign whenever `Standalone Test-Only Stabilization (W+)` is `NOT STABILIZED AS TEST-ONLY` (e.g., PR also modifies production/build files so test-only isolation does not capture the full fix, or standalone `w_plus_passed` is False/None).
2. Otherwise (when `Standalone Test-Only Stabilization (W+)` is `PASSED`), classify the semantic quality of the test fix:
   - `CONTRACT_PRESERVING_FIX`: Eliminates flakiness (e.g., JSONAssert/assertThatJson structural equality, deterministic key sorting, proper state setup/isolation) while preserving 100% of the original test's oracle contract and value/structure checks (0 lost baseline mutants, or purely equivalent structural check).
   - `LEGITIMATE_RELAXATION`: Satisfies Step 1 of the Decision Tree below when minor incidental mutants are lost solely due to legitimate canonicalization while all intended functional invariants hold.
   - `TEST_RESPONSIBILITY_SHIFT`: Satisfies Step 2 of the Decision Tree (replaces serialized/formatted output check with another internal object, mocks/bypasses focal call, or shrinks/wipes preconditions).
   - `GENUINE_ORACLE_WEAKENING`: Satisfies Step 3 of the Decision Tree (relaxes exact check into partial substring/map-subset/non-null/lossy split that permits defective outputs, extra unexpected entries, or unbound fields).
{DECISION_TREE_RUBRIC}"""
    elif task_name == "TAX_60":
        task_inst = f"""You are an independent, blinded software-testing auditor evaluating {len(batch_ids)} stabilized flaky-test patches (`{id_list}`) that lost one or more baseline focal mutation obligations (`Surviving Mutants`).
Do NOT attempt to run shell commands; all information is self-contained below. Read EVERY code diff and its surviving mutants carefully and assign BOTH:

A. `primary_mechanism` (choose the single root-cause edit mechanism responsible for the lost obligations):
  - `M1_PredicateProjectionOrRelaxation`: Weakens, deletes, or projects an assertion predicate onto a coarser property (e.g., `assertEquals` -> `assertTrue(contains(...))`, `isNotNull`, checking keys/length only, relaxing numeric/boolean check).
  - `M2_StateResetOrClobber`: Clears, resets, or overwrites shared/global/singleton state in target teardown/body or clobbers state needed by focal/partner verification (e.g., `.clear()` on a global container/cache, wiping static state).
  - `M3_ControlFlowOrExceptionChange`: Alters control flow, loops, conditionals, or try/catch exception handling inside the test in a way that skips or swallows verification paths.
  - `M4_SUTCallAlterationOrBypass`: Drops, replaces, mocks, or bypasses the focal production method call (e.g., dropping `.toJSONString()`, substituting a mock/stub `ConnectionFactory`, calling a different helper).
  - `M5_PartnerOrSharedScopeEdit`: Modifies the `[PARTNER]` test method or shared class fixture (`scope=PARTNER_AND_TARGET` or `PARTNER_ONLY`) such that partner or shared focal obligations are lost.
  - `M6_DelimiterOrFormatNormalization`: Parses, splits, strips, regex-slices, or normalizes string/SQL/JSON/ToString formatting (e.g., `split(",")` dropping trailing empty tokens, regex/substring slicing, sorting/TreeMap key normalization of formatted strings).
  - `M7_PreconditionOrSingletonSetupAlteration`: Alters test input fixtures, setup arguments, builder configuration, or pre-execution object state (e.g., shrinking multi-key dictionary/config fixture to 1 item, changing constructor/config flags before invoking SUT).

B. `semantic_severity` (evaluate strictly via the 3-step Decision Tree; note that in `TAX_60`, Step 1 maps to `LEGITIMATE_RELAXATION_ARTIFACT`, Step 2 maps to `TEST_RESPONSIBILITY_SHIFT`, and Step 3 maps to `GENUINE_DEGRADATION`; pay close attention to whether fixture-narrowing/setup changes merely shift responsibility vs genuinely degrade oracle checking of the focal contract):
{DECISION_TREE_RUBRIC}"""
    else:
        task_inst = f"""You are an independent, blinded software-testing auditor evaluating {len(batch_ids)} stabilized flaky-test patches (`{id_list}`) that lost one or more unseen held-out production mutants (`M_ext`).
Do NOT attempt to run shell commands; all information (unified diff + exact lost production mutants with `+-3` lines of production source code) is self-contained below.
Classify EVERY item into `label_wsl` (`LEGITIMATE_RELAXATION_OR_CANONICALIZATION`, `TEST_RESPONSIBILITY_SHIFT`, or `GENUINE_ORACLE_WEAKENING`) strictly following Step 1 -> Step 2 -> Step 3:
{DECISION_TREE_RUBRIC}"""

    return (
        f"{task_inst}\n\n"
        f"IMPORTANT: Return an entry in `items` for EVERY ONE of these exact {len(batch_ids)} IDs: {id_list}.\n"
        f"Keep each `rationale` concise (1 sentence, <= 30 words).\n\n"
        f"================ BLINDED PATCH DOSSIERS ================\n\n{joined}\n"
    )


def chunk_list(items: List[str], chunk_size: int) -> List[List[str]]:
    return [items[i : i + chunk_size] for i in range(0, len(items), chunk_size)]


def fleiss_kappa(ratings_matrix: List[List[str]], categories: List[str]) -> float:
    """
    Compute exact Fleiss' kappa across N items and R raters.
    ratings_matrix[i] = list of R category labels assigned to item i.
    """
    N = len(ratings_matrix)
    if N == 0:
        return 0.0
    R = len(ratings_matrix[0])
    if R <= 1:
        return 1.0
    cat_idx = {c: j for j, c in enumerate(categories)}
    k = len(categories)
    counts = [[0] * k for _ in range(N)]
    for i, row in enumerate(ratings_matrix):
        for val in row:
            counts[i][cat_idx[val]] += 1

    p_j = [sum(counts[i][j] for i in range(N)) / (N * R) for j in range(k)]
    P_e = sum(p * p for p in p_j)
    P_i = [
        (sum(counts[i][j] ** 2 for j in range(k)) - R) / (R * (R - 1))
        for i in range(N)
    ]
    P_bar = sum(P_i) / N
    if abs(1.0 - P_e) < 1e-12:
        return 1.0
    return round((P_bar - P_e) / (1.0 - P_e), 4)


def cohen_kappa(pairs: List[Tuple[str, str]]) -> Dict[str, float]:
    n = len(pairs)
    if n == 0:
        return {"n": 0, "agree": 0, "raw_pct": 0.0, "expected_pct": 0.0, "kappa": 0.0}
    agree = sum(1 for a, b in pairs if a == b)
    po = agree / n
    ca = Counter(a for a, _ in pairs)
    cb = Counter(b for _, b in pairs)
    labels = set(ca.keys()) | set(cb.keys())
    pe = sum((ca[k] / n) * (cb[k] / n) for k in labels)
    kappa = (po - pe) / (1.0 - pe) if abs(1.0 - pe) > 1e-12 else 1.0
    return {
        "n": n,
        "agree": agree,
        "raw_pct": round(po * 100.0, 2),
        "expected_pct": round(pe * 100.0, 2),
        "kappa": round(kappa, 4),
    }


def majority_vote(labels: List[str], tie_break_order: List[str]) -> Tuple[str, int, float]:
    cnt = Counter(labels)
    max_v = max(cnt.values())
    winners = [c for c, v in cnt.items() if v == max_v]
    winners.sort(key=lambda c: tie_break_order.index(c) if c in tie_break_order else 999)
    R = len(labels)
    k = max(2, len(tie_break_order))
    ent = -sum((v / R) * math.log2(v / R) for v in cnt.values() if v > 0) / math.log2(k)
    return winners[0], max_v, round(ent, 4)


def run_all_sessions(num_sessions: int, max_workers: int, reasoning_effort: str) -> Dict[str, Any]:
    codex_bin = shutil.which("codex")
    if not codex_bin:
        raise RuntimeError("`codex` CLI binary not found on PATH")

    key = json.loads(KEY_PATH.read_text(encoding="utf-8"))
    blocks_by_task = build_blinded_items(key)

    raw_state: Dict[str, Any] = {"protocol_version": "3.0.0-codex-10session-blinded", "sessions": {}}
    if RAW_OUT_PATH.exists():
        try:
            raw_state = json.loads(RAW_OUT_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    sessions_map: Dict[str, Dict[str, Any]] = raw_state.setdefault("sessions", {})

    work_root = Path(tempfile.gettempdir()) / "ft1_blind_codex_10sessions"
    empty_cwd = work_root / "empty_readonly_cwd"
    schema_dir = work_root / "schemas"
    out_dir = work_root / "outputs"
    empty_cwd.mkdir(parents=True, exist_ok=True)
    schema_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    schema_paths: Dict[str, Path] = {}
    for tname in ("PR_53", "TAX_60", "HELDOUT_111"):
        sp = schema_dir / f"schema_{tname}.json"
        sp.write_text(json.dumps(make_schema(tname), indent=2), encoding="utf-8")
        schema_paths[tname] = sp

    # Build deterministic shuffled batch schedule across all 10 sessions (~18-23 items/batch for crisp attention & fast checkpointing)
    jobs: List[Tuple[str, str, int, List[str]]] = []
    batch_sizes = {"PR_53": 18, "TAX_60": 20, "HELDOUT_111": 23}
    for s_idx in range(1, num_sessions + 1):
        s_tag = f"session_{s_idx:02d}"
        s_store = sessions_map.setdefault(s_tag, {"seed": 20261100 + s_idx, "PR_53": {}, "TAX_60": {}, "HELDOUT_111": {}})
        rng = random.Random(20261100 + s_idx)
        for tname in ("PR_53", "TAX_60", "HELDOUT_111"):
            all_ids = sorted(blocks_by_task[tname].keys())
            rng.shuffle(all_ids)
            batches = chunk_list(all_ids, batch_sizes[tname])
            done_ids = set(s_store.get(tname, {}).keys())
            for b_idx, b_ids in enumerate(batches):
                if all(bid in done_ids for bid in b_ids):
                    continue
                missing_in_batch = [bid for bid in b_ids if bid not in done_ids]
                jobs.append((s_tag, tname, b_idx, missing_in_batch))

    print(f"[10-SESSION CODEX AUDIT] Total pending batch calls: {len(jobs)} (max_workers={max_workers}, effort={reasoning_effort})", flush=True)

    def _exec_batch(job: Tuple[str, str, int, List[str]]) -> Tuple[str, str, int, Dict[str, Dict[str, Any]], float, str]:
        s_tag, tname, b_idx, b_ids = job
        prompt = build_batch_prompt(tname, b_ids, blocks_by_task[tname])
        out_file = out_dir / f"{s_tag}_{tname}_b{b_idx}.json"
        last_err = ""
        for attempt in range(1, 4):
            t0 = time.time()
            try:
                if out_file.exists():
                    out_file.unlink()
                proc = subprocess.run(
                    [
                        codex_bin,
                        "exec",
                        "--ephemeral",
                        "--skip-git-repo-check",
                        "-C",
                        str(empty_cwd),
                        "-s",
                        "read-only",
                        "-c",
                        f'model_reasoning_effort="{reasoning_effort}"',
                        "--output-schema",
                        str(schema_paths[tname]),
                        "-o",
                        str(out_file),
                        "-",
                    ],
                    input=prompt,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    capture_output=True,
                    timeout=480,
                )
                elapsed = round(time.time() - t0, 2)
                if proc.returncode != 0 or not out_file.exists():
                    last_err = f"rc={proc.returncode}: {(proc.stderr or '')[-400:]}"
                    time.sleep(3 * attempt)
                    continue
                parsed = json.loads(out_file.read_text(encoding="utf-8"))
                items_map = {it["blind_id"]: it for it in parsed.get("items", []) if it.get("blind_id") in b_ids}
                if set(items_map.keys()) != set(b_ids):
                    missing = sorted(set(b_ids) - set(items_map.keys()))
                    last_err = f"Missing IDs in response: {missing}"
                    time.sleep(2 * attempt)
                    continue
                model_hdr = ""
                for ln in (proc.stderr or "").splitlines()[:12]:
                    if ln.lower().startswith("model:"):
                        model_hdr = ln.split(":", 1)[1].strip()
                        break
                return s_tag, tname, b_idx, items_map, elapsed, model_hdr
            except Exception as e:
                last_err = str(e)
                time.sleep(3 * attempt)
        raise RuntimeError(f"Batch failed ({s_tag}/{tname}/b{b_idx}): {last_err}")

    if jobs:
        completed = 0
        detected_model = None
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futs = {ex.submit(_exec_batch, j): j for j in jobs}
            for fut in as_completed(futs):
                j = futs[fut]
                s_tag, tname, b_idx, items_map, elapsed, m_hdr = fut.result()
                completed += 1
                if m_hdr and not detected_model:
                    detected_model = m_hdr
                    raw_state["auditor_model"] = m_hdr
                raw_state["auditor_reasoning_effort"] = reasoning_effort
                with SAVE_LOCK:
                    sessions_map[s_tag][tname].update(items_map)
                    save_json_atomic(RAW_OUT_PATH, raw_state)
                print(
                    f"  [{completed}/{len(jobs)}] DONE {s_tag} {tname} b{b_idx} ({len(items_map)} items, {elapsed:.1f}s, model={m_hdr or 'default'})",
                    flush=True,
                )

    return analyze_10sessions(key, raw_state, num_sessions)


def analyze_10sessions(key: Dict[str, Any], raw_state: Dict[str, Any], num_sessions: int) -> Dict[str, Any]:
    sessions = [f"session_{i:02d}" for i in range(1, num_sessions + 1)]
    s_map = raw_state["sessions"]

    # Verify 100% completeness across all sessions
    for s in sessions:
        assert len(s_map[s]["PR_53"]) == 53, f"{s} PR_53 incomplete: {len(s_map[s]['PR_53'])}"
        assert len(s_map[s]["TAX_60"]) == 60, f"{s} TAX_60 incomplete: {len(s_map[s]['TAX_60'])}"
        assert len(s_map[s]["HELDOUT_111"]) == 111, f"{s} HELDOUT_111 incomplete: {len(s_map[s]['HELDOUT_111'])}"

    # Helper to summarize a rating dimension across R sessions + Stage 1
    def _eval_dimension(
        bids: List[str],
        categories: List[str],
        get_stage1,
        get_session_val,
    ) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        matrix: List[List[str]] = []
        per_session_pairs: Dict[str, List[Tuple[str, str]]] = {s: [] for s in sessions}
        consensus_pairs: List[Tuple[str, str]] = []
        unanimous = 0
        supermaj_8 = 0
        entropies: List[float] = []
        boundary_items: List[Dict[str, Any]] = []

        for bid in bids:
            s1 = get_stage1(bid)
            row = [get_session_val(s, bid) for s in sessions]
            matrix.append(row)
            maj, max_votes, ent = majority_vote(row, categories)
            entropies.append(ent)
            if max_votes == num_sessions:
                unanimous += 1
            if max_votes >= math.ceil(0.8 * num_sessions):
                supermaj_8 += 1
            if s1 is not None:
                consensus_pairs.append((s1, maj))
                for s, sv in zip(sessions, row):
                    per_session_pairs[s].append((s1, sv))
            if max_votes < math.ceil(0.8 * num_sessions) or (s1 is not None and maj != s1):
                boundary_items.append({
                    "blind_id": bid,
                    "stage1": s1,
                    "ensemble_majority": maj,
                    "majority_votes": f"{max_votes}/{num_sessions}",
                    "vote_counts": dict(Counter(row)),
                    "vote_entropy": ent,
                })

        fk = fleiss_kappa(matrix, categories)
        ck_cons = cohen_kappa(consensus_pairs)
        per_s_kappas = [cohen_kappa(per_session_pairs[s])["kappa"] for s in sessions]
        per_s_raws = [cohen_kappa(per_session_pairs[s])["raw_pct"] for s in sessions]
        summary = {
            "n_items": len(bids),
            "n_sessions": num_sessions,
            "intra_ensemble_fleiss_kappa": fk,
            "unanimous_10of10_count": unanimous,
            "unanimous_10of10_pct": round(100.0 * unanimous / len(bids), 2),
            "supermajority_8of10_count": supermaj_8,
            "supermajority_8of10_pct": round(100.0 * supermaj_8 / len(bids), 2),
            "mean_normalized_vote_entropy": round(statistics.mean(entropies), 4),
            "stage1_vs_majority_consensus": ck_cons,
            "stage1_vs_individual_sessions": {
                "kappa_mean": round(statistics.mean(per_s_kappas), 4),
                "kappa_sd": round(statistics.stdev(per_s_kappas), 4) if len(per_s_kappas) > 1 else 0.0,
                "kappa_min": round(min(per_s_kappas), 4),
                "kappa_max": round(max(per_s_kappas), 4),
                "raw_pct_mean": round(statistics.mean(per_s_raws), 2),
                "raw_pct_min": round(min(per_s_raws), 2),
                "raw_pct_max": round(max(per_s_raws), 2),
                "per_session_kappa": {s: round(k, 4) for s, k in zip(sessions, per_s_kappas)},
            },
            "ensemble_majority_distribution": dict(Counter(majority_vote(r, categories)[0] for r in matrix)),
            "boundary_or_disagreement_count": len(boundary_items),
        }
        return summary, boundary_items

    pr_all_ids = sorted(key["pr_53"].keys())
    pr_43_ids = sorted(
        bid for bid, m in key["pr_53"].items()
        if m.get("pr_scope_type") == "TEST_ONLY_PR" and m.get("w_plus_passed") is True
    )
    tax_60_ids = sorted(key["tax_60"].keys())
    tax_55_ids = sorted(
        bid for bid, m in key["tax_60"].items()
        if not m.get("is_active_replacement_for_stale_v2")
    )
    ho_111_ids = sorted(key["ho_all"].keys())

    def _pr_legit_bin(lbl: str) -> str:
        return "LEGITIMATE" if lbl in ("CONTRACT_PRESERVING_FIX", "LEGITIMATE_RELAXATION") else "NON_LEGITIMATE"

    def _tax_nonlegit_bin(sev: str) -> str:
        return "LEGITIMATE_ARTIFACT" if sev == "LEGITIMATE_RELAXATION_ARTIFACT" else "NON_LEGITIMATE_EROSION"

    def _ho_harm_bin(lbl: str) -> str:
        return "BENIGN_CANONICALIZATION" if lbl == "LEGITIMATE_RELAXATION_OR_CANONICALIZATION" else "HARMFUL_EROSION_WS"

    dims = {}
    boundaries = {}

    dims["pr_53_five_class"], boundaries["pr_53_five_class"] = _eval_dimension(
        pr_all_ids, PR_LABELS,
        lambda b: key["pr_53"][b]["stage1_label"],
        lambda s, b: s_map[s]["PR_53"][b]["label"],
    )
    dims["pr_43_legitimate_binary"], boundaries["pr_43_legitimate_binary"] = _eval_dimension(
        pr_43_ids, ["LEGITIMATE", "NON_LEGITIMATE"],
        lambda b: _pr_legit_bin(key["pr_53"][b]["stage1_label"]),
        lambda s, b: _pr_legit_bin(s_map[s]["PR_53"][b]["label"]),
    )
    dims["tax_60_primary_mechanism"], boundaries["tax_60_primary_mechanism"] = _eval_dimension(
        tax_60_ids, TAX_MECH_LABELS,
        lambda b: key["tax_60"][b]["stage1_mechanism"],
        lambda s, b: s_map[s]["TAX_60"][b]["primary_mechanism"],
    )
    dims["tax_55_primary_mechanism"], _ = _eval_dimension(
        tax_55_ids, TAX_MECH_LABELS,
        lambda b: key["tax_60"][b]["stage1_mechanism"],
        lambda s, b: s_map[s]["TAX_60"][b]["primary_mechanism"],
    )
    dims["tax_60_nonlegit_binary"], boundaries["tax_60_nonlegit_binary"] = _eval_dimension(
        tax_60_ids, ["NON_LEGITIMATE_EROSION", "LEGITIMATE_ARTIFACT"],
        lambda b: _tax_nonlegit_bin(key["tax_60"][b]["stage1_severity"]),
        lambda s, b: _tax_nonlegit_bin(s_map[s]["TAX_60"][b]["semantic_severity"]),
    )
    dims["tax_60_semantic_severity_3class"], boundaries["tax_60_semantic_severity_3class"] = _eval_dimension(
        tax_60_ids, TAX_SEV_LABELS,
        lambda b: key["tax_60"][b]["stage1_severity"],
        lambda s, b: s_map[s]["TAX_60"][b]["semantic_severity"],
    )
    dims["heldout_111_wsl_3class"], boundaries["heldout_111_wsl_3class"] = _eval_dimension(
        ho_111_ids, HO_WSL_LABELS,
        lambda b: key["ho_all"][b]["stage1_label"],
        lambda s, b: s_map[s]["HELDOUT_111"][b]["label_wsl"],
    )
    dims["heldout_111_harmful_ws_binary"], boundaries["heldout_111_harmful_ws_binary"] = _eval_dimension(
        ho_111_ids, ["HARMFUL_EROSION_WS", "BENIGN_CANONICALIZATION"],
        lambda b: _ho_harm_bin(key["ho_all"][b]["stage1_label"]),
        lambda s, b: _ho_harm_bin(s_map[s]["HELDOUT_111"][b]["label_wsl"]),
    )

    # Downstream Conclusion Invariance across all 10 individual sessions!
    # 1. PR_43 MuGuard false-rejection rate across sessions 1..10
    pr_false_rej_rates = []
    pr_weakening_caught = []
    for s in sessions:
        legit_ids = [b for b in pr_43_ids if _pr_legit_bin(s_map[s]["PR_53"][b]["label"]) == "LEGITIMATE"]
        nonlegit_ids = [b for b in pr_43_ids if _pr_legit_bin(s_map[s]["PR_53"][b]["label"]) == "NON_LEGITIMATE"]
        rej_legit = sum(1 for b in legit_ids if not key["pr_53"][b].get("pr_full_gate_accepts"))
        caught_nl = sum(1 for b in nonlegit_ids if not key["pr_53"][b].get("pr_full_gate_accepts"))
        pr_false_rej_rates.append(round(100.0 * rej_legit / max(1, len(legit_ids)), 2))
        pr_weakening_caught.append(f"{caught_nl}/{len(nonlegit_ids)}")

    # 2. TAX_60 non-legitimate erosion rate across sessions 1..10
    tax_nonlegit_rates = []
    for s in sessions:
        nl = sum(1 for b in tax_60_ids if _tax_nonlegit_bin(s_map[s]["TAX_60"][b]["semantic_severity"]) == "NON_LEGITIMATE_EROSION")
        tax_nonlegit_rates.append(round(100.0 * nl / 60.0, 2))

    # 3. Held-out W/S/L counts per model x strategy across sessions 1..10
    models_k5 = ["deepseek-flash", "deepseek-v4-pro", "qwen3.7-plus"]
    strats_k5 = [
        "B0_Standard_Validator",
        "B2_Prompt_Guard",
        "B3_Static_AST_Guard",
        "B3_Plus_Structural_Guard",
        "B4_Scalar_Gate_Loop",
        "Ours_MuGuard_Flake",
    ]
    cand_to_bid = {v["cand_key"]: bid for bid, v in key["ho_all"].items()}
    ho_invariance: Dict[str, Any] = {}
    for m in models_k5:
        v2_res = json.loads((DATA_DIR / f"v2_results_{m}.json").read_text(encoding="utf-8"))["results"]
        ho_invariance[m] = {}
        for st_name in strats_k5:
            # Find accepted patches for (m, st_name) that are in ho_all (M_ext loss patches)
            loss_bids = []
            for r in v2_res:
                if not r.get("v2_eligible"):
                    continue
                st = r["strategies"][st_name]
                if not st.get("accepted"):
                    continue
                acc_r = st.get("accepted_round")
                rd = next((x for x in st["rounds"] if x["round"] == acc_r), st["rounds"][-1])
                ck = f"{r['sample_id']}::{rd['candidate_fp']}"
                if ck in cand_to_bid:
                    loss_bids.append(cand_to_bid[ck])
            ws_per_session = []
            maj_wsl = Counter()
            for b in loss_bids:
                maj_lbl, _, _ = majority_vote([s_map[s]["HELDOUT_111"][b]["label_wsl"] for s in sessions], HO_WSL_LABELS)
                maj_wsl[maj_lbl] += 1
            for s in sessions:
                ws = sum(1 for b in loss_bids if s_map[s]["HELDOUT_111"][b]["label_wsl"] != "LEGITIMATE_RELAXATION_OR_CANONICALIZATION")
                ws_per_session.append(ws)
            ho_invariance[m][st_name] = {
                "total_mext_loss": len(loss_bids),
                "majority_W": maj_wsl["GENUINE_ORACLE_WEAKENING"],
                "majority_S": maj_wsl["TEST_RESPONSIBILITY_SHIFT"],
                "majority_L": maj_wsl["LEGITIMATE_RELAXATION_OR_CANONICALIZATION"],
                "harmful_WS_min_across_10_sessions": min(ws_per_session),
                "harmful_WS_max_across_10_sessions": max(ws_per_session),
                "harmful_WS_mean_across_10_sessions": round(statistics.mean(ws_per_session), 2),
            }

    out = {
        "protocol_version": "3.0.0-codex-10session-blinded-ensemble",
        "auditor_cli": "codex-cli 0.153.4",
        "auditor_model": raw_state.get("auditor_model", "gpt-6-astra"),
        "auditor_reasoning_effort": raw_state.get("auditor_reasoning_effort", "medium"),
        "num_independent_sessions": num_sessions,
        "total_blinded_adjudications": num_sessions * 224,
        "blinding_guarantees": (
            "10 stateless (--ephemeral), read-only (-s read-only) OpenAI Codex sessions executed "
            "outside the repository (--skip-git-repo-check) with independent Fisher-Yates item-order "
            "permutations (seeds 20261101..20261110), zero access to repository files, strategy names, "
            "model identities, gate verdicts, PR merge statuses, Stage-1 labels, or peer sessions."
        ),
        "dimensions": dims,
        "downstream_conclusion_invariance_across_10_sessions": {
            "pr_43_muguard_false_rejection_pct_range": [min(pr_false_rej_rates), max(pr_false_rej_rates)],
            "pr_43_muguard_false_rejection_pct_per_session": pr_false_rej_rates,
            "pr_43_nonlegit_caught_per_session": pr_weakening_caught,
            "tax_60_nonlegit_erosion_pct_range": [min(tax_nonlegit_rates), max(tax_nonlegit_rates)],
            "tax_60_nonlegit_erosion_pct_per_session": tax_nonlegit_rates,
            "heldout_111_harmful_WS_by_model_and_strategy": ho_invariance,
        },
        "boundary_and_disagreement_ledger": boundaries,
    }
    save_json_atomic(SUMMARY_OUT_PATH, out)
    print(f"\n[SAVED] {SUMMARY_OUT_PATH}", flush=True)
    for dname, dval in dims.items():
        s1_maj = dval["stage1_vs_majority_consensus"]
        print(
            f"  {dname}: Fleiss_k={dval['intra_ensemble_fleiss_kappa']:.4f}, "
            f"10/10_unanimous={dval['unanimous_10of10_pct']:.1f}%, >=8/10={dval['supermajority_8of10_pct']:.1f}% | "
            f"Stage1_vs_Maj: raw={s1_maj['raw_pct']:.1f}%, Cohen_k={s1_maj['kappa']:.4f}",
            flush=True,
        )
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--sessions", type=int, default=10)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--effort", type=str, default="medium")
    ap.add_argument("--analyze-only", action="store_true")
    args = ap.parse_args()
    if args.analyze_only:
        k = json.loads(KEY_PATH.read_text(encoding="utf-8"))
        rs = json.loads(RAW_OUT_PATH.read_text(encoding="utf-8"))
        analyze_10sessions(k, rs, args.sessions)
    else:
        run_all_sessions(args.sessions, args.workers, args.effort)
