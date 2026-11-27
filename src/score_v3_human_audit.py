"""
FT-1 v3.1 Human Dual-Rater & Three-Way Codex Triangulation Scorer (`score_v3_human_audit.py`).

Fixes verified (`H3` & `H4`):
  - Exact field and tie-break alignment with `v3_codex_10session_blinded_audit.py` and
    `v31_adjudicate_and_analyze_sota.py` across all 277 items (`PR_53`: `label`, `TAX_60`: `primary_mechanism` &
    `semantic_severity`, `HELDOUT_111`: `label_wsl`, `SOTA_53`: `extra7` + `extra46` 10-session votes), asserted at
    startup (`len == 277`, 100% non-empty across every dimension).
  - Idempotent `ARBITRATION_SHEET.csv` preservation: previously filled `consensus_label` and `arbitration_note` rows
    are preserved intact across repeated runs.
  - Degenerate `cohen_kappa()` handling: returns `"kappa": None` (never fabricating `0.0` or `1.0`) whenever `n == 0`
    or `abs(1.0 - pe) <= 1e-12` (zero-variance single-category marginals).
"""
import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

FT1_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(FT1_ROOT / "src"))
from v3_codex_10session_blinded_audit import (
    HO_WSL_LABELS,
    PR_LABELS,
    TAX_MECH_LABELS,
    TAX_SEV_LABELS,
    majority_vote,
)

DATA_DIR = FT1_ROOT / "data"
PACK_DIR = FT1_ROOT / "human_audit_pack"
V31_KEY_PATH = DATA_DIR / "v31_human_audit_key.json"
V30_KEY_PATH = DATA_DIR / "v3_human_audit_key.json"
OUT_PATH = DATA_DIR / "v3_human_dual_rater_reliability.json"


def cohen_kappa(pairs: List[Tuple[str, str]]) -> Dict[str, Any]:
    n = len(pairs)
    if n == 0:
        return {"n": 0, "agree": 0, "raw_pct": 0.0, "expected_pct": 0.0, "kappa": None}
    agree = sum(1 for a, b in pairs if a == b)
    po = agree / n
    ca = Counter(a for a, _ in pairs)
    cb = Counter(b for _, b in pairs)
    labels = set(ca.keys()) | set(cb.keys())
    pe = sum((ca[k] / n) * (cb[k] / n) for k in labels)
    kappa: Optional[float] = round((po - pe) / (1.0 - pe), 4) if abs(1.0 - pe) > 1e-12 else None
    return {
        "n": n,
        "agree": agree,
        "raw_pct": round(po * 100, 2),
        "expected_pct": round(pe * 100, 2),
        "kappa": kappa,
    }


def normalize_wsl(val: str) -> str:
    v = (val or "").strip().upper()
    if v in ("W", "GENUINE_ORACLE_WEAKENING", "GENUINE_DEGRADATION"):
        return "W"
    if v in ("S", "TEST_RESPONSIBILITY_SHIFT"):
        return "S"
    if v in ("L", "LEGITIMATE_CANONICALIZATION", "LEGITIMATE_RELAXATION_OR_CANONICALIZATION", "LEGITIMATE_RELAXATION_ARTIFACT", "LEGITIMATE_RELAXATION"):
        return "L"
    return v


def load_codex_10session_majorities(key: Dict[str, Any]) -> Dict[str, Dict[str, str]]:
    """Build verified `blind_id -> Codex 10-session majority` dict across all 277 items."""
    out: Dict[str, Dict[str, str]] = {}
    raw_p = DATA_DIR / "v3_codex_10session_raw_runs.json"
    sessions_map = json.loads(raw_p.read_text(encoding="utf-8"))["sessions"]
    s_names = sorted(sessions_map.keys())
    assert len(s_names) == 10, f"Expected 10 Codex sessions, got {len(s_names)}"

    # 1. PR_53 (`label` -> 5-class & binary YES/NO)
    for bid in sorted(key["pr_53"].keys()):
        votes = [sessions_map[s]["PR_53"][bid]["label"].strip().upper() for s in s_names]
        maj, _, _ = majority_vote(votes, PR_LABELS)
        legit_yn = "YES" if maj in ("CONTRACT_PRESERVING_FIX", "LEGITIMATE_RELAXATION") else "NO"
        out[bid] = {
            "rater_label_5class": maj,
            "is_legitimate_repair_YES_NO": legit_yn,
        }

    # 2. TAX_60 (`primary_mechanism` & `semantic_severity`)
    for bid in sorted(key["tax_60"].keys()):
        m_votes = [sessions_map[s]["TAX_60"][bid]["primary_mechanism"].strip() for s in s_names]
        s_votes = [sessions_map[s]["TAX_60"][bid]["semantic_severity"].strip().upper() for s in s_names]
        maj_m, _, _ = majority_vote(m_votes, TAX_MECH_LABELS)
        maj_s, _, _ = majority_vote(s_votes, TAX_SEV_LABELS)
        out[bid] = {
            "primary_mechanism_M1toM7": maj_m.upper(),
            "wsl": normalize_wsl(maj_s),
        }

    # 3. HELDOUT_111 (`label_wsl`)
    for bid in sorted(key["ho_all"].keys()):
        w_votes = [sessions_map[s]["HELDOUT_111"][bid]["label_wsl"].strip().upper() for s in s_names]
        maj_w, _, _ = majority_vote(w_votes, HO_WSL_LABELS)
        out[bid] = {"wsl": normalize_wsl(maj_w)}

    # 4. SOTA_53 (`extra7` + `extra46`, using identical `HO_WSL_LABELS` tie-break order as `v31_adjudicate_and_analyze_sota.py`)
    if "sota_53" in key:
        extra7 = json.loads((DATA_DIR / "v3_extra7_heldout_wsl_10sessions.json").read_text(encoding="utf-8"))
        extra46 = json.loads((DATA_DIR / "v31_sota_extra_wsl_10sessions.json").read_text(encoding="utf-8"))
        for bid, meta in sorted(key["sota_53"].items()):
            ck = meta["cand_key"]
            if ck in extra46:
                sv = [v.strip().upper() for v in extra46[ck]["session_votes"]]
                assert len(sv) == 10, f"Incomplete 10-session votes on {bid} ({ck})"
                maj_w, _, _ = majority_vote(sv, HO_WSL_LABELS)
            elif ck in extra7:
                ps = extra7[ck].get("per_session") or {}
                sv = [ps[k]["label_wsl"].strip().upper() for k in sorted(ps.keys())] if isinstance(ps, dict) and ps else [extra7[ck]["majority_label"].strip().upper()] * 10
                assert len(sv) == 10, f"Incomplete extra7 votes on {bid} ({ck})"
                maj_w, _, _ = majority_vote(sv, HO_WSL_LABELS)
            else:
                raise KeyError(f"Missing Codex 10-session record for {bid} ({ck})")
            out[bid] = {"wsl": normalize_wsl(maj_w)}

    # Startup completeness & non-empty unit assertion (`H3`)
    expected_total = len(key["pr_53"]) + len(key["tax_60"]) + len(key["ho_all"]) + len(key.get("sota_53", {}))
    assert len(out) == expected_total == 277, f"Expected 277 Codex majority records, got {len(out)}"
    for bid in key["pr_53"]:
        assert out[bid].get("rater_label_5class") in PR_LABELS and out[bid].get("is_legitimate_repair_YES_NO") in ("YES", "NO"), f"Bad PR_53 majority on {bid}: {out[bid]}"
    for bid in key["tax_60"]:
        assert out[bid].get("primary_mechanism_M1toM7") in {m.upper() for m in TAX_MECH_LABELS} and out[bid].get("wsl") in ("W", "S", "L"), f"Bad TAX_60 majority on {bid}: {out[bid]}"
    for bid in list(key["ho_all"]) + list(key.get("sota_53", {})):
        assert out[bid].get("wsl") in ("W", "S", "L"), f"Bad held-out majority on {bid}: {out[bid]}"

    return out


def load_rater_answers(rater_tag: str, pack_dir: Path = PACK_DIR) -> Dict[str, Dict[str, str]]:
    out: Dict[str, Dict[str, str]] = {}

    def _merge(bid: str, row: Dict[str, Any]) -> None:
        if not bid:
            return
        cur = out.setdefault(bid, {})
        for k, v in row.items():
            s = ("" if v is None else str(v)).strip()
            if s:
                cur[k] = s

    xlsx_name = f"{rater_tag.replace('rater_', 'Rater_')}_Audit_Sheet.xlsx"
    xp = pack_dir / xlsx_name
    if xp.exists():
        try:
            import openpyxl
            wb = openpyxl.load_workbook(str(xp), data_only=True)
            sheet_map = {
                "1_PR_53": {
                    "【请填】5分类标签": "rater_label_5class",
                    "【请填】是否合法(YES/NO)": "is_legitimate_repair_YES_NO",
                    "【请填】置信度(1-3)": "confidence_1to3",
                    "【选填】备注": "rationale_1_sentence",
                    "【请填】一句话依据": "rationale_1_sentence",
                },
                "2_TAX_60": {
                    "【请填】主要机制(M1-M7)": "primary_mechanism_M1toM7",
                    "【请填】语义性质(W/S/L)": "semantic_severity_W_S_L",
                    "【请填】置信度(1-3)": "confidence_1to3",
                    "【选填】备注": "rationale_1_sentence",
                    "【请填】一句话依据": "rationale_1_sentence",
                },
                "3_HELDOUT_111": {
                    "【请填】标签(W/S/L)": "label_W_S_L",
                    "【请填】置信度(1-3)": "confidence_1to3",
                    "【选填】备注": "rationale_1_sentence",
                    "【请填】一句话依据": "rationale_1_sentence",
                },
                "4_SOTA_53": {
                    "【请填】标签(W/S/L)": "label_W_S_L",
                    "【请填】置信度(1-3)": "confidence_1to3",
                    "【选填】备注": "rationale_1_sentence",
                },
            }
            for sname, cmap in sheet_map.items():
                if sname not in wb.sheetnames:
                    continue
                ws = wb[sname]
                hdrs = [str(ws.cell(1, c).value or "").strip() for c in range(1, ws.max_column + 1)]
                for r_i in range(2, ws.max_row + 1):
                    bid = str(ws.cell(r_i, 1).value or "").strip()
                    if not bid:
                        continue
                    row = {cmap[h]: ws.cell(r_i, c_i).value for c_i, h in enumerate(hdrs, 1) if h in cmap}
                    _merge(bid, row)
        except Exception as e:
            print(f"[WARN] Could not parse {xp.name}: {e}")

    for prefix in ("PR_53", "TAX_60", "HELDOUT", "SOTA_53"):
        cp = pack_dir / f"{rater_tag}_{prefix}.csv"
        if cp.exists():
            with cp.open("r", encoding="utf-8-sig", newline="") as f:
                for row in csv.DictReader(f):
                    _merge((row.get("blind_id") or "").strip(), row)

    for jname in (f"{rater_tag}_audit_backup.json", f"{rater_tag}_completed_audit.json"):
        jp = pack_dir / jname
        if jp.exists():
            obj = json.loads(jp.read_text(encoding="utf-8"))
            ans = obj.get("answers", obj)
            for bid, row in ans.items():
                if isinstance(row, dict):
                    _merge(bid.strip(), row)

    return out


def score_pack(pack_dir: Path = PACK_DIR, out_path: Path = OUT_PATH) -> Dict[str, Any]:
    key_path = V31_KEY_PATH if V31_KEY_PATH.exists() else V30_KEY_PATH
    key = json.loads(key_path.read_text(encoding="utf-8"))
    codex_maj = load_codex_10session_majorities(key)
    ma = load_rater_answers("rater_A", pack_dir=pack_dir)
    mb = load_rater_answers("rater_B", pack_dir=pack_dir)

    # Load existing ARBITRATION_SHEET.csv rows so resolved `consensus_label` & `arbitration_note` are preserved idempotently!
    arb_path = pack_dir / "ARBITRATION_SHEET.csv"
    existing_arb_rows: Dict[Tuple[str, str], Dict[str, str]] = {}
    if arb_path.exists():
        with arb_path.open("r", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                k = ((row.get("blind_id") or "").strip(), (row.get("dimension") or "").strip())
                if k[0] and k[1]:
                    existing_arb_rows[k] = dict(row)

    report: Dict[str, Any] = {"protocol_version": "3.1.1-human-dual-rater-triangulated", "tasks": {}}
    merged_arb_map: Dict[Tuple[str, str], Dict[str, str]] = dict(existing_arb_rows)

    specs = [
        ("PR_53", "pr_53", [("rater_label_5class", "five_class", "rater_label_5class"), ("is_legitimate_repair_YES_NO", "legit_binary", "is_legitimate_repair_YES_NO")]),
        ("TAX_60", "tax_60", [("primary_mechanism_M1toM7", "mechanism_7class", "primary_mechanism_M1toM7"), ("semantic_severity_W_S_L", "severity_wsl", "wsl")]),
        ("HELDOUT_111", "ho_all", [("label_W_S_L", "heldout_wsl", "wsl")]),
    ]
    if "sota_53" in key:
        specs.append(("SOTA_53", "sota_53", [("label_W_S_L", "sota_wsl", "wsl")]))

    pooled_ho_ab: List[Tuple[str, str]] = []
    pooled_ho_codex: List[Tuple[str, str]] = []

    for prefix, key_sec, col_specs in specs:
        bids = sorted(key[key_sec].keys())
        task_res = {}
        for col, metric_name, codex_col in col_specs:
            pairs_ab = []
            pairs_hc = []
            incomplete = 0
            for bid in bids:
                va = (ma.get(bid) or {}).get(col, "").strip().upper()
                vb = (mb.get(bid) or {}).get(col, "").strip().upper()
                if col.endswith("W_S_L"):
                    va, vb = normalize_wsl(va), normalize_wsl(vb)
                if not va or not vb:
                    incomplete += 1
                    continue
                pairs_ab.append((va, vb))

                arb_key = (bid, metric_name)
                prev_arb = existing_arb_rows.get(arb_key) or {}
                prev_cons = (prev_arb.get("consensus_label") or "").strip().upper()
                if col.endswith("W_S_L") and prev_cons:
                    prev_cons = normalize_wsl(prev_cons)

                if va == vb:
                    consensus = va
                else:
                    consensus = prev_cons
                    merged_arb_map[arb_key] = {
                        "task": prefix,
                        "blind_id": bid,
                        "dimension": metric_name,
                        "rater_A": va,
                        "rater_B": vb,
                        "rater_A_rationale": (ma.get(bid) or {}).get("rationale_1_sentence", ""),
                        "rater_B_rationale": (mb.get(bid) or {}).get("rationale_1_sentence", ""),
                        "consensus_label": prev_arb.get("consensus_label", ""),
                        "arbitration_note": prev_arb.get("arbitration_note", ""),
                    }

                c_val = (codex_maj.get(bid) or {}).get(codex_col, "").strip().upper()
                if consensus and c_val:
                    pairs_hc.append((consensus, c_val))
                    if prefix in ("HELDOUT_111", "SOTA_53"):
                        pooled_ho_codex.append((consensus, c_val))
                if prefix in ("HELDOUT_111", "SOTA_53"):
                    pooled_ho_ab.append((va, vb))

            stats = cohen_kappa(pairs_ab)
            stats["total_items_in_pool"] = len(bids)
            stats["incomplete_pairs"] = incomplete
            stats["human_consensus_vs_codex_10session"] = cohen_kappa(pairs_hc)
            task_res[metric_name] = stats
        report["tasks"][prefix] = task_res

    if pooled_ho_ab:
        report["tasks"]["ALL_HELDOUT_164"] = {
            "combined_heldout_wsl": {
                **cohen_kappa(pooled_ho_ab),
                "human_consensus_vs_codex_10session": cohen_kappa(pooled_ho_codex),
            }
        }

    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    if merged_arb_map:
        arb_fields = ["task", "blind_id", "dimension", "rater_A", "rater_B", "rater_A_rationale", "rater_B_rationale", "consensus_label", "arbitration_note"]
        ordered_rows = [merged_arb_map[k] for k in sorted(merged_arb_map.keys())]
        with arb_path.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=arb_fields)
            w.writeheader()
            for r in ordered_rows:
                w.writerow({fn: r.get(fn, "") for fn in arb_fields})
        unresolved_n = sum(1 for r in ordered_rows if not (r.get("consensus_label") or "").strip())
        print(f"[INFO] Wrote {len(ordered_rows)} arbitration rows ({unresolved_n} unresolved, {len(ordered_rows)-unresolved_n} preserved resolved) to {arb_path}")

    return report


def run_selftest() -> None:
    """Simulated full-score & idempotent-arbitration & degenerate-kappa self-test in a temporary directory."""
    import shutil
    import tempfile

    key = json.loads(V31_KEY_PATH.read_text(encoding="utf-8"))
    cm = load_codex_10session_majorities(key)
    assert len(cm) == 277

    # 1. Degenerate kappa check (`n == 0` and single-class `pe == 1.0`)
    assert cohen_kappa([])["kappa"] is None
    deg = cohen_kappa([("W", "W")] * 25)
    assert deg["n"] == 25 and deg["raw_pct"] == 100.0 and deg["kappa"] is None, f"Degenerate kappa failed: {deg}"

    # 2. Full 277-item simulated Rater A & Rater B + 1 arbitrated disagreement idempotency check
    canon_mech = {m.upper(): m for m in TAX_MECH_LABELS}
    ans_a: Dict[str, Dict[str, str]] = {}
    ans_b: Dict[str, Dict[str, str]] = {}
    for bid in key["pr_53"]:
        ans_a[bid] = {"rater_label_5class": cm[bid]["rater_label_5class"], "is_legitimate_repair_YES_NO": cm[bid]["is_legitimate_repair_YES_NO"]}
        ans_b[bid] = dict(ans_a[bid])
    for bid in key["tax_60"]:
        m_code = canon_mech[cm[bid]["primary_mechanism_M1toM7"]]
        ans_a[bid] = {"primary_mechanism_M1toM7": m_code, "semantic_severity_W_S_L": cm[bid]["wsl"]}
        ans_b[bid] = dict(ans_a[bid])
    for bid in list(key["ho_all"]) + list(key["sota_53"]):
        ans_a[bid] = {"label_W_S_L": cm[bid]["wsl"]}
        ans_b[bid] = dict(ans_a[bid])

    # Inject 1 disagreement on HO-001, resolve it in ARBITRATION_SHEET.csv, then re-run score_pack to verify idempotence!
    true_ho1 = ans_a["HO-001"]["label_W_S_L"]
    ans_b["HO-001"]["label_W_S_L"] = "L" if true_ho1 != "L" else "W"

    tmp_dir = Path(tempfile.mkdtemp(prefix="ft1_human_audit_selftest_"))
    try:
        (tmp_dir / "rater_A_audit_backup.json").write_text(json.dumps({"answers": ans_a}), encoding="utf-8")
        (tmp_dir / "rater_B_audit_backup.json").write_text(json.dumps({"answers": ans_b}), encoding="utf-8")
        out_tmp = tmp_dir / "report.json"

        rep1 = score_pack(pack_dir=tmp_dir, out_path=out_tmp)
        assert rep1["tasks"]["PR_53"]["five_class"]["raw_pct"] == 100.0
        assert rep1["tasks"]["TAX_60"]["mechanism_7class"]["raw_pct"] == 100.0
        assert rep1["tasks"]["TAX_60"]["severity_wsl"]["raw_pct"] == 100.0
        assert rep1["tasks"]["SOTA_53"]["sota_wsl"]["raw_pct"] == 100.0
        assert rep1["tasks"]["HELDOUT_111"]["heldout_wsl"]["human_consensus_vs_codex_10session"]["n"] == 110

        # Fill consensus_label in ARBITRATION_SHEET.csv and re-run TWICE to prove idempotent preservation!
        arb_p = tmp_dir / "ARBITRATION_SHEET.csv"
        with arb_p.open("r", encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 1 and rows[0]["blind_id"] == "HO-001"
        rows[0]["consensus_label"] = true_ho1
        rows[0]["arbitration_note"] = "Resolved in self-test"
        with arb_p.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)

        rep2 = score_pack(pack_dir=tmp_dir, out_path=out_tmp)
        rep3 = score_pack(pack_dir=tmp_dir, out_path=out_tmp)
        assert rep2["tasks"]["HELDOUT_111"]["heldout_wsl"]["human_consensus_vs_codex_10session"]["n"] == 111
        assert rep3["tasks"]["ALL_HELDOUT_164"]["combined_heldout_wsl"]["human_consensus_vs_codex_10session"]["raw_pct"] == 100.0
        with arb_p.open("r", encoding="utf-8-sig", newline="") as f:
            rows_after = list(csv.DictReader(f))
        assert len(rows_after) == 1 and rows_after[0]["consensus_label"] == true_ho1 and rows_after[0]["arbitration_note"] == "Resolved in self-test"
        print("[SELFTEST PASS] 277/277 canonical codes, 100% Codex triangulation, idempotent ARBITRATION_SHEET.csv, & degenerate kappa=None verified!")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true", help="Run isolated self-test in temp dir without touching real files")
    args = ap.parse_args()
    if args.selftest:
        run_selftest()
        return
    rep = score_pack()
    print(json.dumps(rep, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
