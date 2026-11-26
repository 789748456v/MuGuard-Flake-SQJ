"""
Shared Integrity & Provenance Gatekeeper for FT1 v3.1 (`R3-4` & `R3-5`).

Single source of truth imported by:
  - `v31_build_flakyguard_dcg.py`
  - `v31_run_sota_head_to_head.py`
  - `v31_adjudicate_and_analyze_sota.py`

Enforces:
  1. Fingerprint-bound DCG context certification (`R3-4`): samples whose legacy coverage map included
     `M_ext`-only lines (or any quarantined/rebuilt repository) stay unscheduled and uncertified until
     their rebuilt `M_ext`-free DCG context is complete and matches the canonical `covered_map` SHA-256.
     Checkpoint reuse is bound to the exact `dcg_context_sha256` + S3 Round-1 `prompt_sha`, never to
     unconditional boolean flags.
  2. Shared historical held-out lookup & analysis eligibility (`R3-5`): historical held-out entries are
     retained ONLY for verified-clean repositories; any candidate in an implicated repository (`avro`,
     `remoting`, `openwebbeans`) requires `v31_safe_eval:` provenance from the baseline-verified wrapper,
     forcing fresh `post_freeze_heldout` evaluation before Phase D analysis.
"""
import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

# Completed SOTA samples whose in-loop evaluations overlapped multi-worker working-tree contamination
FORCE_REEVAL_SIDS: Set[str] = {
    "avro::TestReflect.testRecord",
    "avro::TestReflect.testMultipleFieldAliases",
    "avro::TestReflect.testAvroDoc",
    "avro::TestReflect.testAnnotationMultiAvroMeta",
    "remoting::ClassFilterTest.userRequest_singleClassLoader",
}

# Completed SOTA samples whose legacy DCG context included M_ext-only lines
DCG_REBUILT_COMPLETED_SIDS: Set[str] = {
    "marine-api::AISMessageFactoryTest.testCreateWithIncorrectOrder",
    "spring-ws::SpringDigestPasswordValidationCallbackHandlerTest.testAuthenticateUserDigestUserNotFound",
}

# Repositories where historical (non-v31_safe_eval) held-out entries are NOT trusted without fresh safe-eval provenance
# OR explicit pre-contamination file-mtime verification. To be maximally conservative (zero LLM cost), we require
# fresh `v31_safe_eval:` held-out provenance for ALL accepted SOTA candidates in `avro`, `remoting`, and `openwebbeans`.
SAFE_EVAL_REQUIRED_PROJECTS: Set[str] = {"avro", "remoting", "openwebbeans"}

# Preserved until `openwebbeans` clean baseline-coverage + DCG audit is explicitly verified; see `get_quarantined_projects`.
DEFAULT_QUARANTINED_PROJECTS: Set[str] = {"openwebbeans"}


def canonical_sha256(obj: Any) -> str:
    raw = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def dcg_entry_fingerprint(dcg_entry: Optional[Dict[str, Any]]) -> str:
    if not dcg_entry or not isinstance(dcg_entry, dict):
        return ""
    sfs = dcg_entry.get("selected_functions") or []
    payload = {
        "selection_method": dcg_entry.get("selection_method"),
        "selected_ids": dcg_entry.get("selected_ids") or [],
        "selected_functions": [
            {
                "node_id": f.get("node_id"),
                "signature": f.get("signature"),
                "rel_file": f.get("rel_file"),
                "start_line": f.get("start_line"),
                "end_line": f.get("end_line"),
                "code": f.get("code"),
            }
            for f in sfs
        ],
    }
    return canonical_sha256(payload)


def compute_mext_affected_dcg_sids(
    elig_samples: List[Dict[str, Any]],
    bc_samples: Dict[str, Any],
    build_cov_fn: Callable[[Dict[str, Any], Dict[str, Any]], Dict[str, List[int]]],
) -> Set[str]:
    affected: Set[str] = set(DCG_REBUILT_COMPLETED_SIDS)
    for s in elig_samples:
        sid = s["sample_id"]
        ext_lines: Set[Tuple[str, int]] = set()
        for k in ("m_ext_op_value_flow", "m_ext_op_call", "m_ext_operator", "m_ext_instance"):
            for m in s.get(k) or []:
                f = m.get("rel_file") if s["language"] == "python" else m.get("mutatedClass")
                if f and int(m.get("lineNumber") or 0) > 0:
                    ext_lines.add((f, int(m["lineNumber"])))
        cmap = build_cov_fn(s, bc_samples.get(sid, {}))
        base_lines = {(f, ln) for f, lns in cmap.items() for ln in lns}
        if ext_lines - base_lines:
            affected.add(sid)
    return affected


def get_quarantined_projects(data_dir: Path) -> Set[str]:
    """Keep `openwebbeans` quarantined unless its clean baseline-coverage + DCG audit certificate is present
    AND its DCG selection has been verified against clean frozen HEAD."""
    cert_p = data_dir / "v31_openwebbeans_audit_cert.json"
    if cert_p.exists():
        try:
            cert = json.loads(cert_p.read_text(encoding="utf-8"))
            if cert.get("audit_verified_clean_for_scheduling") is True:
                return set()
        except Exception:
            pass
    return set(DEFAULT_QUARANTINED_PROJECTS)


def is_sample_dcg_certified(
    sample: Dict[str, Any],
    dcg_store: Dict[str, Any],
    raw_dcg_store: Dict[str, Any],
    bc_samples: Dict[str, Any],
    affected_sids: Set[str],
    build_cov_fn: Callable[[Dict[str, Any], Dict[str, Any]], Dict[str, List[int]]],
) -> Tuple[bool, str]:
    sid = sample["sample_id"]
    dcg_samples = dcg_store.get("samples") or {}
    entry = dcg_samples.get(sid)
    if not entry:
        return False, "missing_dcg_entry"
    sfs = entry.get("selected_functions") or []
    if not (1 <= len(sfs) <= 5) or not all(bool((f.get("code") or "").strip()) for f in sfs):
        return False, "invalid_selected_functions"

    if sid in affected_sids:
        rebuilt_ctx = set(dcg_store.get("mext_free_rebuilt_sids") or [])
        rebuilt_raw = set(raw_dcg_store.get("mext_free_rebuilt_sids") or [])
        if sid not in rebuilt_ctx or sid not in rebuilt_raw:
            return False, "pending_mext_free_dcg_rebuild"
        exp_cov_sha = canonical_sha256(build_cov_fn(sample, bc_samples.get(sid, {})))
        if entry.get("covered_map_sha256") != exp_cov_sha:
            return False, "covered_map_sha256_mismatch"
        raw_nodes = ((raw_dcg_store.get("samples") or {}).get(sid) or {}).get("nodes")
        if not raw_nodes or entry.get("raw_dcg_sha256") != canonical_sha256(raw_nodes):
            return False, "raw_dcg_sha256_mismatch"

    return True, "ok"


def is_checkpoint_record_eligible(
    rec: Dict[str, Any],
    sample: Dict[str, Any],
    dcg_store: Dict[str, Any],
    raw_dcg_store: Dict[str, Any],
    bc_samples: Dict[str, Any],
    affected_sids: Set[str],
    build_cov_fn: Callable[[Dict[str, Any], Dict[str, Any]], Dict[str, List[int]]],
    expected_s3_r1_prompt_sha: str,
    quarantined_projects: Set[str],
) -> Tuple[bool, str]:
    sid = sample["sample_id"]
    if sample["project_name"].lower() in quarantined_projects:
        return False, "project_quarantined"

    dcg_ok, reason = is_sample_dcg_certified(sample, dcg_store, raw_dcg_store, bc_samples, affected_sids, build_cov_fn)
    if not dcg_ok:
        return False, f"dcg_not_certified:{reason}"

    cur_dcg_fp = dcg_entry_fingerprint((dcg_store.get("samples") or {}).get(sid))
    rec_dcg_fp = rec.get("dcg_context_sha256") or ""

    # Check S3 Round-1 prompt SHA matches current DCG context!
    s3_rounds = (((rec.get("strategies") or {}).get("S3_FlakyGuard") or {}).get("rounds") or [])
    s3_r1_sha = s3_rounds[0].get("prompt_sha", "") if s3_rounds else ""
    if not s3_r1_sha or s3_r1_sha != expected_s3_r1_prompt_sha:
        return False, "s3_r1_prompt_sha_mismatch_with_current_dcg"

    if sid in affected_sids:
        if rec_dcg_fp != cur_dcg_fp:
            return False, "affected_sample_missing_rebuilt_dcg_fingerprint"

    if rec_dcg_fp and rec_dcg_fp != cur_dcg_fp:
        return False, "dcg_context_sha256_drift"

    if sid in FORCE_REEVAL_SIDS:
        if not rec.get("safe_eval_wrapper_sha256") or not rec.get("evaluator_sha256"):
            return False, "force_reeval_missing_safe_wrapper_provenance"

    return True, "ok"


def _lost_mutant_count(hev: Dict[str, Any]) -> int:
    n = 0
    for k in ("lost_value_flow_mutants", "lost_call_mutants", "lost_op_mutants", "lost_inst_mutants"):
        n += len(hev.get(k) or [])
    return n


def is_heldout_entry_eligible(
    ck: str,
    hev: Optional[Dict[str, Any]],
    quarantined_projects: Set[str],
    existing_wsl_labels: Optional[Dict[str, Any]] = None,
) -> bool:
    if not hev or not isinstance(hev, dict) or hev.get("infra_error"):
        return False
    sid = ck.rsplit("::", 1)[0] if "::" in ck else ""
    proj = sid.split("::", 1)[0].lower() if "::" in sid else ""
    prov = str(hev.get("_provenance") or "")
    has_safe_prov = prov.startswith("v31_safe_eval:")

    if proj in quarantined_projects:
        return False
    if (sid in FORCE_REEVAL_SIDS) or (proj in SAFE_EVAL_REQUIRED_PROJECTS):
        if not has_safe_prov:
            return False

    # If this is an un-adjudicated evaluable strict-W+-passing loss, require complete uncapped lost-mutant list
    if existing_wsl_labels is not None and ck not in existing_wsl_labels:
        w_strict = bool(hev.get("w_plus_heldout_passed_strict", hev.get("w_plus_heldout_passed")))
        evaluable = (
            bool(hev.get("m_ext_runner_ok"))
            and bool(hev.get("m_ext_eval_ok"))
            and int(hev.get("m_ext_all_unknown") or 0) == 0
        )
        ret = hev.get("m_ext_all_retention")
        if w_strict and evaluable and ret is not None and float(ret) < 1.0:
            total = int(hev.get("m_ext_all_total") or 0)
            killed = int(hev.get("m_ext_all_killed") or 0)
            if _lost_mutant_count(hev) != max(0, total - killed):
                return False

    return True


def load_eligible_heldout_map(
    data_dir: Path,
    sota_ho_path: Path,
    existing_wsl_labels: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Single shared loader for Phase C (`--mode heldout`) and Phase D (`v31_adjudicate_and_analyze_sota.py`)."""
    quarantined = get_quarantined_projects(data_dir)
    eligible: Dict[str, Any] = {}

    sources: List[Tuple[Path, str]] = [
        (data_dir / "v2_heldout_validation.json", "evaluations"),
        (data_dir / "v3_completed_heldout.json", "evaluations"),
        (data_dir / "v3_b5_b6_heldout_validation.json", "evaluations"),
        (data_dir / "v3_python_indent_ablation.json", "heldout_evals"),
        (sota_ho_path, "evaluations"),
    ]
    for path, field in sources:
        if not path.exists():
            continue
        raw_map = json.loads(path.read_text(encoding="utf-8")).get(field, {}) or {}
        for ck, hev in raw_map.items():
            if is_heldout_entry_eligible(ck, hev, quarantined, existing_wsl_labels=existing_wsl_labels):
                eligible[ck] = hev
    return eligible
