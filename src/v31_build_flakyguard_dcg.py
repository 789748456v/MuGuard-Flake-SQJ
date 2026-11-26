"""
Phase B: Build & Certify `data/v3_flakyguard_dcg_context.json` across ALL 203/203 Eligible Tests.

Faithfully implements FlakyGuard's (Li et al., ASE 2025 Distinguished Paper, §III-B Algorithm 1 & §IV-A)
Dynamic Call Graph (DCG) context builder on confirmatory `qwen3.7-plus` (`temperature = 0.0`):
  1. Uploads `v31_remote_dcg_extractor.py` + dynamic line-coverage spec (`target_cov` U `partner_cov`
     from `v3_baseline_coverage.json` + `m_det`/`m_ext` fallback lines for the 3 `argLine` Java tests)
     to `/root/sqj_work/FT1` and extracts pruned executed production functions & call edges `G_DCG`.
  2. If `|V_DCG| <= 5`: directly retains all dynamically executed production functions (`F <= 5`).
  3. If `|V_DCG| > 5`: runs FlakyGuard Algorithm 1 LLM-guided BFS expansion (`k = 3` callees per hop
     from root functions invoked by the test) followed by `qwen3.7-plus` selection of at most `F = 5`
     most relevant focal production functions (each <= 1,500 chars).
"""
import argparse
import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from remote_and_llm import DATA_DIR, chat_completion, run_ssh, scp_from_remote, scp_to_remote
from run_full_study_matrix import CACHE_LOCK, load_disk_llm_cache, log, prompt_cache_key
from v31_sota_integrity import canonical_sha256, dcg_entry_fingerprint

OUT_PATH = DATA_DIR / "v3_flakyguard_dcg_context.json"
RAW_DCG_CACHE_PATH = DATA_DIR / "v31_raw_dcg_graphs_cache.json"
V31_LLM_CACHE_PATH = DATA_DIR / "v31_llm_cache_qwen3.7-plus.json"
LOCK = threading.Lock()


def call_fast_selector(model: str, prompt: str, purpose: str, ro_cache: Dict[str, Any], rw_cache: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
    k = prompt_cache_key(model, prompt)
    with CACHE_LOCK:
        if k in rw_cache and (rw_cache[k].get("content") or "").strip():
            return rw_cache[k], True
        if k in ro_cache and (ro_cache[k].get("content") or "").strip():
            return ro_cache[k], True
    for attempt in range(4):
        try:
            resp = chat_completion(
                provider=None,
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=1536,
                purpose=purpose,
                timeout=120,
                extra_body={"enable_thinking": True, "thinking_budget": 768},
            )
            if (resp.get("content") or "").strip():
                slim = {"content": resp["content"], "latency_sec": resp["latency_sec"], "tokens": (resp.get("usage") or {}).get("total_tokens", 0)}
                with CACHE_LOCK:
                    rw_cache[k] = slim
                return slim, False
        except Exception as e:
            log(f"  [DCG retry {attempt+1}/4] {purpose}: {str(e)[:120]}")
            if any(q in str(e) for q in ("Arrearage", "http_402", "good standing")):
                raise
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"DCG selector API call failed after retries (no heuristic fallback): {purpose}")


def build_sample_covered_map(sample: Dict[str, Any], bc_entry: Dict[str, Any]) -> Dict[str, List[int]]:
    """Combine dynamic JaCoCo/`coverage.py` line coverage with attributed in-loop focal mutant (`M_det`) lines.
    Held-out `M_ext` pools are NEVER used here: repair-time context must stay independent of held-out validation."""
    cov: Dict[str, Set[int]] = {}
    if bc_entry.get("ok_target") and isinstance(bc_entry.get("target_cov"), dict):
        for k, lns in bc_entry["target_cov"].items():
            cov.setdefault(k, set()).update(int(x) for x in lns if int(x) > 0)
    if bc_entry.get("ok_partner") and isinstance(bc_entry.get("partner_cov"), dict):
        for k, lns in bc_entry["partner_cov"].items():
            cov.setdefault(k, set()).update(int(x) for x in lns if int(x) > 0)

    # In-loop focal mutant lines only (covers the 3 argLine Java tests lacking JaCoCo coverage)
    lang = sample["language"]
    mut_lists = [
        sample.get("m_det") or [],
        sample.get("polluter_m_det") or [],
    ]
    for mlist in mut_lists:
        for m in mlist:
            lno = int(m.get("lineNumber") or 0)
            if lno <= 0:
                continue
            if lang == "python":
                rk = m.get("rel_file")
                if rk:
                    cov.setdefault(rk, set()).add(lno)
            else:
                mc = m.get("mutatedClass")
                if mc:
                    cov.setdefault(mc, set()).add(lno)
    return {k: sorted(v) for k, v in cov.items() if v}


def parse_selected_ids(text: str, valid_ids: Set[str], limit: int) -> List[str]:
    picked: List[str] = []
    for m in re.finditer(r"\b(F\d+)\b", text or ""):
        fid = m.group(1)
        if fid in valid_ids and fid not in picked:
            picked.append(fid)
            if len(picked) >= limit:
                break
    return picked


def select_valid_ids_or_raise(
    model: str,
    prompt: str,
    valid_ids: Set[str],
    limit: int,
    purpose: str,
    ro_cache: Dict[str, Any],
    rw_cache: Dict[str, Any],
) -> List[str]:
    """Call `call_fast_selector` and parse valid node IDs; retry up to 3 times on empty/invalid IDs and
    raise `RuntimeError` if still empty. Heuristic fallbacks (`neighbors[:3]` / `visited[:5]`) are forbidden."""
    pkey = prompt_cache_key(model, prompt)
    for attempt in range(3):
        resp, was_cached = call_fast_selector(
            model=model,
            prompt=prompt,
            purpose=purpose if attempt == 0 else f"{purpose}_retry{attempt}",
            ro_cache=ro_cache if attempt == 0 else {},
            rw_cache=rw_cache,
        )
        picked = parse_selected_ids(resp.get("content", ""), valid_ids, limit=limit)
        if picked:
            return picked
        log(f"  [DCG PARSE EMPTY {attempt+1}/3] {purpose}: no valid IDs in {sorted(valid_ids)}; evicting cache & retrying")
        with CACHE_LOCK:
            rw_cache.pop(pkey, None)
        if not was_cached:
            time.sleep(1.0)
    raise RuntimeError(
        f"DCG selector returned no valid IDs after 3 attempts for {purpose} (valid={sorted(valid_ids)}); "
        "refusing heuristic fallback"
    )


def _stamp_dcg_provenance(
    rec: Dict[str, Any],
    dcg: Dict[str, Any],
    covered_map: Optional[Dict[str, List[int]]] = None,
) -> Dict[str, Any]:
    if covered_map is not None:
        rec["covered_map_sha256"] = canonical_sha256(covered_map)
    rec["raw_dcg_sha256"] = canonical_sha256(dcg.get("nodes") or [])
    rec["dcg_context_sha256"] = dcg_entry_fingerprint(rec)
    return rec


def select_focal_functions_llm(
    sample: Dict[str, Any],
    dcg: Dict[str, Any],
    model: str,
    ro_cache: Dict[str, Any],
    rw_cache: Dict[str, Any],
    covered_map: Optional[Dict[str, List[int]]] = None,
) -> Dict[str, Any]:
    nodes: List[Dict[str, Any]] = dcg["nodes"]
    by_id = {n["node_id"]: n for n in nodes}
    sid = sample["sample_id"]

    if len(nodes) <= 5:
        rec_le5 = {
            "sample_id": sid,
            "language": sample["language"],
            "total_dcg_nodes": len(nodes),
            "total_dcg_edges": dcg["total_dcg_edges"],
            "selection_method": "ALL_EXECUTED_LE_5",
            "bfs_traversed_ids": [n["node_id"] for n in nodes],
            "selected_ids": [n["node_id"] for n in nodes],
            "selected_functions": [
                {
                    "node_id": n["node_id"],
                    "signature": n["signature"],
                    "rel_file": n["rel_file"],
                    "start_line": n["start_line"],
                    "end_line": n["end_line"],
                    "is_root": n["is_root"],
                    "dcg_hop": n["dcg_hop"],
                    "covered_lines_count": n["covered_lines_count"],
                    "code": n["code"],
                }
                for n in nodes
            ],
        }
        return _stamp_dcg_provenance(rec_le5, dcg, covered_map)

    # Step 1: FlakyGuard Algorithm 1 LLM-guided BFS traversal (`k = 3` per hop from root functions)
    roots = [n["node_id"] for n in nodes if n["is_root"]][:8]
    if not roots:
        roots = [n["node_id"] for n in nodes[:3]]
    visited: List[str] = list(roots)
    frontier: List[str] = list(roots)

    for hop in (1, 2):
        neighbors: List[str] = []
        for u in frontier:
            for v in by_id[u].get("callees", []):
                if v in by_id and v not in visited and v not in neighbors:
                    neighbors.append(v)
        if not neighbors:
            break
        if len(neighbors) <= 3:
            chosen_hop = neighbors
        else:
            cand_neighbors = neighbors[:18]
            cand_desc = "\n".join(
                f"- `{nid}`: `{by_id[nid]['signature']}` in `{by_id[nid]['rel_file']}` "
                f"(executed lines: {by_id[nid]['covered_lines_count']})\n"
                f"  Preview: `{' '.join(by_id[nid]['code'].splitlines()[:3])[:200]}`"
                for nid in cand_neighbors
            )
            bfs_prompt = (
                "You are running FlakyGuard's Dynamic Call Graph (DCG) breadth-first context selector.\n"
                f"Flaky Test (`{sample['test_type']}`): `{sample['test_full_name']}`\n"
                f"Test Code:\n```\n{sample['orig_test_code'][:1800]}\n```\n"
                f"Failure Diagnostic:\n```\n{(sample.get('flaky_error_summary') or '')[:900]}\n```\n"
                f"Current Expanded Focal Functions: {', '.join(by_id[x]['signature'] for x in frontier[:6])}\n\n"
                f"Candidate Dynamically Executed Callees at Hop {hop}:\n{cand_desc}\n\n"
                "Select up to k=3 callee IDs (`F...`) most relevant to understanding the flaky failure's root cause and state/assertion invariants.\n"
                "Reply ONLY with a JSON array of up to 3 IDs, e.g. [\"F4\", \"F7\", \"F9\"]."
            )
            chosen_hop = select_valid_ids_or_raise(
                model=model,
                prompt=bfs_prompt,
                valid_ids=set(cand_neighbors),
                limit=3,
                purpose=f"v31_fg_bfs_h{hop}_{sid}",
                ro_cache=ro_cache,
                rw_cache=rw_cache,
            )
        visited.extend(chosen_hop)
        frontier = chosen_hop

    # Ensure focal scope methods invoked via callbacks/reflection are also eligible in candidate pool (cap 16)
    for n in nodes:
        if len(visited) >= 16:
            break
        if n["node_id"] not in visited and n.get("in_focal_scope"):
            visited.append(n["node_id"])

    # Step 2: FlakyGuard Post-Traversal Ranking (`F <= 5` focal production functions)
    if len(visited) <= 5:
        final_ids = visited
        sel_method = "DCG_BFS_LE_5"
    else:
        pool_ids = visited[:16]
        pool_blocks = []
        for nid in pool_ids:
            nd = by_id[nid]
            snippet = "\n".join(nd["code"].splitlines()[:12])[:450]
            pool_blocks.append(
                f"[{nid}] `{nd['signature']}` (`{nd['rel_file']}:L{nd['start_line']}-L{nd['end_line']}`, "
                f"root={nd['is_root']}, hop={nd['dcg_hop']}, cov_lines={nd['covered_lines_count']})\n"
                f"```\n{snippet}\n```"
            )
        rank_prompt = (
            "You are FlakyGuard's Focal Production Context Selector (ASE 2025, F=5).\n"
            f"Target Flaky Test (`{sample.get('od_subtype') or sample['test_type']}`): `{sample['test_full_name']}`\n"
            f"Original Test Code:\n```\n{sample['orig_test_code'][:2000]}\n```\n"
            + (
                f"Partner Test Code:\n```\n{(sample.get('orig_polluter_code') or '')[:1200]}\n```\n"
                if sample.get("orig_polluter_code") else ""
            )
            + f"Failure Stack / Diagnostic:\n```\n{(sample.get('flaky_error_summary') or '')[:1000]}\n```\n\n"
            "Below are dynamically executed production functions from the pruned Dynamic Call Graph:\n\n"
            + "\n\n".join(pool_blocks)
            + "\n\nSelect AT MOST 5 function IDs (`F...`) that provide the most critical production context "
            "(data structures, serialization/ordering logic, shared static/singleton state, and assertion invariants) "
            "needed to repair the flaky test cleanly without breaking its intended test semantics.\n"
            "Reply ONLY with a JSON array of up to 5 IDs ordered by relevance, e.g. [\"F1\", \"F3\", \"F5\", \"F8\", \"F10\"]."
        )
        final_ids = select_valid_ids_or_raise(
            model=model,
            prompt=rank_prompt,
            valid_ids=set(pool_ids),
            limit=5,
            purpose=f"v31_fg_select5_{sid}",
            ro_cache=ro_cache,
            rw_cache=rw_cache,
        )
        sel_method = "DCG_LLM_BFS_TOP5"

    rec_out = {
        "sample_id": sid,
        "language": sample["language"],
        "total_dcg_nodes": len(nodes),
        "total_dcg_edges": dcg["total_dcg_edges"],
        "selection_method": sel_method,
        "bfs_traversed_ids": visited,
        "selected_ids": final_ids[:5],
        "selected_functions": [
            {
                "node_id": nid,
                "signature": by_id[nid]["signature"],
                "rel_file": by_id[nid]["rel_file"],
                "start_line": by_id[nid]["start_line"],
                "end_line": by_id[nid]["end_line"],
                "is_root": by_id[nid]["is_root"],
                "dcg_hop": by_id[nid]["dcg_hop"],
                "covered_lines_count": by_id[nid]["covered_lines_count"],
                "code": by_id[nid]["code"],
            }
            for nid in final_ids[:5]
        ],
    }
    return _stamp_dcg_provenance(rec_out, dcg, covered_map)


def save_atomic(path: Path, obj: Any) -> None:
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen3.7-plus")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--rebuild-mext-affected", action="store_true", help="Re-extract and re-select ONLY samples whose old coverage map contained M_ext-only lines")
    ap.add_argument("--rebuild-sids", default="", help="Optional comma-separated sample_ids to force rebuild")
    args = ap.parse_args()

    bm = json.loads((DATA_DIR / "v2_frozen_benchmark_light.json").read_text(encoding="utf-8"))
    elig = [s for s in bm["samples"] if s.get("v2_eligible")]
    bc = json.loads((DATA_DIR / "v3_baseline_coverage.json").read_text(encoding="utf-8"))["samples"]

    # Step 1: Extract raw executed DCG graphs from remote benchmark repo snapshots (cached locally once extracted)
    rebuild_sids: List[str] = [x.strip() for x in args.rebuild_sids.split(",") if x.strip()]
    if args.rebuild_mext_affected:
        for s in elig:
            ext_lines = set()
            for k in ("m_ext_op_value_flow", "m_ext_op_call", "m_ext_operator", "m_ext_instance"):
                for m in s.get(k) or []:
                    f = m.get("rel_file") if s["language"] == "python" else m.get("mutatedClass")
                    if f and int(m.get("lineNumber") or 0) > 0:
                        ext_lines.add((f, int(m["lineNumber"])))
            cmap = build_sample_covered_map(s, bc.get(s["sample_id"], {}))
            base_lines = {(f, l) for f, ls in cmap.items() for l in ls}
            if (ext_lines - base_lines) and s["sample_id"] not in rebuild_sids:
                rebuild_sids.append(s["sample_id"])
    if rebuild_sids:
        log(f"[PHASE B FIX] Rebuilding M_ext-free DCG graphs for {len(rebuild_sids)} samples: {rebuild_sids}")

    if RAW_DCG_CACHE_PATH.exists() and not rebuild_sids:
        raw_dcg_all = json.loads(RAW_DCG_CACHE_PATH.read_text(encoding="utf-8"))
    else:
        spec_samples = []
        for s in elig:
            sid = s["sample_id"]
            if rebuild_sids and sid not in rebuild_sids:
                continue
            spec_samples.append({
                "sample_id": sid,
                "language": s["language"],
                "proj_dir": s["proj_dir"],
                "module": s.get("module", ""),
                "test_type": s["test_type"],
                "orig_test_code": s["orig_test_code"],
                "orig_polluter_code": s.get("orig_polluter_code", ""),
                "context_header": s.get("context_header", ""),
                "fqcn_to_path": s.get("fqcn_to_path") or {},
                "focal_classes": s.get("focal_classes") or [],
                "covered_map": build_sample_covered_map(s, bc.get(sid, {})),
            })
        local_spec = DATA_DIR / "_tmp_v31_dcg_spec.json"
        local_spec.write_text(json.dumps({"samples": spec_samples}, ensure_ascii=False), encoding="utf-8")
        try:
            src_script = Path(__file__).resolve().parent / "v31_remote_dcg_extractor.py"
            log("[PHASE B] Uploading v31_remote_dcg_extractor.py and spec to remote server...")
            scp_to_remote(src_script, "scripts/v31_remote_dcg_extractor.py")
            time.sleep(1.0)
            scp_to_remote(local_spec, "data/_tmp_v31_dcg_spec.json")
            rc, out, err = run_ssh(
                "python3 /root/sqj_work/FT1/scripts/v31_remote_dcg_extractor.py "
                "/root/sqj_work/FT1/data/_tmp_v31_dcg_spec.json "
                "/root/sqj_work/FT1/data/_tmp_v31_dcg_raw.json",
                timeout=300,
            )
            print(out.strip())
            if rc != 0:
                raise RuntimeError(f"Remote DCG extraction failed (rc={rc}): {err}")
            if rebuild_sids:
                tmp_raw = DATA_DIR / "_tmp_v31_dcg_raw_rebuild.json"
                scp_from_remote("data/_tmp_v31_dcg_raw.json", tmp_raw)
                new_part = json.loads(tmp_raw.read_text(encoding="utf-8"))["samples"]
                tmp_raw.unlink()
                raw_dcg_all = json.loads(RAW_DCG_CACHE_PATH.read_text(encoding="utf-8"))
                for sid in rebuild_sids:
                    raw_dcg_all["samples"][sid] = new_part[sid]
                raw_dcg_all.setdefault("mext_free_rebuilt_sids", sorted(set(raw_dcg_all.get("mext_free_rebuilt_sids", [])) | set(rebuild_sids)))
                save_atomic(RAW_DCG_CACHE_PATH, raw_dcg_all)
            else:
                scp_from_remote("data/_tmp_v31_dcg_raw.json", RAW_DCG_CACHE_PATH)
                raw_dcg_all = json.loads(RAW_DCG_CACHE_PATH.read_text(encoding="utf-8"))
        finally:
            if local_spec.exists():
                local_spec.unlink()

    dcg_map = raw_dcg_all["samples"]
    empty_sids = [sid for sid, d in dcg_map.items() if d["total_dcg_nodes"] == 0]
    assert len(dcg_map) == 203 and not empty_sids, f"Expected 203 non-empty DCG graphs, got len={len(dcg_map)}, empty={empty_sids}"
    log(f"[PHASE B] All 203/203 DCG graphs extracted cleanly! Running LLM-guided F<=5 focal selection on {args.model}...")

    store = (
        json.loads(OUT_PATH.read_text(encoding="utf-8"))
        if OUT_PATH.exists()
        else {"protocol_version": "3.1.0-flakyguard-dcg-f5", "model": args.model, "max_focal_functions": 5, "samples": {}}
    )
    done_map: Dict[str, Any] = store.setdefault("samples", {})
    for sid in rebuild_sids:
        old = done_map.pop(sid, None)
        if old is not None:
            store.setdefault("superseded_mext_contaminated_selections", {})[sid] = old.get("selected_ids")
    s_by_id = {s["sample_id"]: s for s in elig}
    cov_by_id = {s["sample_id"]: build_sample_covered_map(s, bc.get(s["sample_id"], {})) for s in elig}
    todo = [s["sample_id"] for s in elig if s["sample_id"] not in done_map or not done_map[s["sample_id"]].get("selected_functions")]

    _, ro_cache = load_disk_llm_cache(args.model)
    rw_cache = json.loads(V31_LLM_CACHE_PATH.read_text(encoding="utf-8")) if V31_LLM_CACHE_PATH.exists() else {}
    log(f"[PHASE B] {len(done_map)}/203 already selected; running {len(todo)} with {args.workers} workers...")

    failed_sids: List[str] = []
    completed_rebuilds: List[str] = []
    if todo:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = {
                ex.submit(select_focal_functions_llm, s_by_id[sid], dcg_map[sid], args.model, ro_cache, rw_cache, cov_by_id[sid]): sid
                for sid in todo
            }
            done_cnt = 0
            for fut in as_completed(futs):
                sid = futs[fut]
                try:
                    res = fut.result()
                except Exception as e:
                    failed_sids.append(sid)
                    log(f"  [DCG SELECT FAILED] {sid}: {type(e).__name__}: {str(e)[:220]}")
                    continue
                with LOCK:
                    done_map[sid] = res
                    if sid in rebuild_sids:
                        completed_rebuilds.append(sid)
                        store["mext_free_rebuilt_sids"] = sorted(set(store.get("mext_free_rebuilt_sids", [])) | set(completed_rebuilds))
                    done_cnt += 1
                    if done_cnt % 15 == 0 or done_cnt == len(todo):
                        save_atomic(OUT_PATH, store)
                        with CACHE_LOCK:
                            save_atomic(V31_LLM_CACHE_PATH, rw_cache)
                        log(f"  [DCG SELECT] {len(done_map)}/203 saved (latest: {sid} -> {len(res['selected_functions'])}/{res['total_dcg_nodes']} funcs)")

    # Backfill provenance hashes on unaffected existing selections
    for sid, rec in done_map.items():
        if not rec.get("dcg_context_sha256") or not rec.get("covered_map_sha256"):
            _stamp_dcg_provenance(rec, dcg_map[sid], cov_by_id[sid])

    save_atomic(OUT_PATH, store)
    with CACHE_LOCK:
        save_atomic(V31_LLM_CACHE_PATH, rw_cache)
    if failed_sids:
        raise RuntimeError(f"DCG focal selection incomplete ({len(failed_sids)} failed; left unscheduled): {failed_sids}")
    # Final certification checks
    assert len(done_map) == 203, f"Expected 203 samples, got {len(done_map)}"
    for sid, rec in done_map.items():
        sfs = rec.get("selected_functions") or []
        assert 1 <= len(sfs) <= 5, f"{sid} has invalid selected_functions count={len(sfs)}"
        assert all(bool((f.get("code") or "").strip()) for f in sfs), f"{sid} has empty function code!"
    mean_dcg = sum(r["total_dcg_nodes"] for r in done_map.values()) / 203.0
    mean_sel = sum(len(r["selected_functions"]) for r in done_map.values()) / 203.0
    log(f"=== [PHASE B PASS] 203/203 FlakyGuard DCG contexts certified! mean_dcg_nodes={mean_dcg:.1f}, mean_selected_F={mean_sel:.2f} ===")


if __name__ == "__main__":
    main()
