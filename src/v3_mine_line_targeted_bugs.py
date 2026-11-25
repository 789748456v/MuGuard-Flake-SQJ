"""
FT-1 v3.0 Line-Targeted Historical Bug-Commit Miner (`git log -L <line>,<line>:<file>`).

Instead of scanning whole files (which yields commits in unrelated methods), uses the EXACT
production file paths and line numbers of surviving mutants from our 400 eroded patches
(`taxonomy_audit_codebook.json`) to run `git log -L` directly on the exact eroded production
lines across all 55+ eroded repositories!

Outputs: `data/v3_line_targeted_bug_candidates.json`
"""
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from remote_and_llm import DATA_DIR, run_ssh, scp_to_remote

OUT_PATH = DATA_DIR / "v3_line_targeted_bug_candidates.json"

REMOTE_LINE_MINER = r"""
import json, os, re, subprocess, sys

spec = json.loads(sys.stdin.read())
out = []
for item in spec:
    sid = item["sample_id"]
    lang = item["language"]
    pdir = item["proj_dir"]
    targets = item["line_targets"]  # list of [file_path, line_no]

    git_root = pdir
    if not os.path.isdir(os.path.join(git_root, ".git")):
        for sub in os.listdir(pdir) if os.path.isdir(pdir) else []:
            cand = os.path.join(pdir, sub)
            if os.path.isdir(os.path.join(cand, ".git")):
                git_root = cand
                break
    if not os.path.isdir(os.path.join(git_root, ".git")):
        continue

    seen_shas = set()
    cand_commits = []
    for fpath, ln in targets[:10]:
        if not fpath or not ln or ln <= 0:
            continue
        rf = None
        if os.path.isabs(fpath):
            if fpath.startswith(git_root) and os.path.exists(fpath):
                rf = os.path.relpath(fpath, git_root).replace("\\", "/")
        else:
            if os.path.exists(os.path.join(git_root, fpath)):
                rf = fpath.replace("\\", "/")
            elif os.path.exists(os.path.join(pdir, fpath)):
                rf = os.path.relpath(os.path.join(pdir, fpath), git_root).replace("\\", "/")
        if not rf:
            continue

        lo = max(1, ln - 2)
        hi = ln + 2
        cmd = ["git", "-C", git_root, "log", "-n", "12", "-s", "--no-merges", f"-L{lo},{hi}:{rf}"]
        # Use --format to extract SHAs touching those exact lines!
        p = subprocess.run(
            ["git", "-C", git_root, "log", "-n", "12", "--no-merges", "--format=COMMIT:%H\t%ad\t%s", "--date=short", f"-L{lo},{hi}:{rf}"],
            capture_output=True, text=True, errors="replace", timeout=20
        )
        if p.returncode != 0:
            continue
        shas = []
        for out_l in p.stdout.splitlines():
            if out_l.startswith("COMMIT:"):
                parts = out_l[7:].split("\t", 2)
                if len(parts) == 3:
                    shas.append(tuple(parts))
        # Skip the earliest initial-creation commit (reverting file creation deletes the file!)
        for sha, dt, subj in shas[:-1]:
            if sha in seen_shas:
                continue
            seen_shas.add(sha)
            if re.search(r"\b(javadoc|typo|readme|format|checkstyle|license|header|whitespace)\b", subj, re.I):
                continue
            p_diff = subprocess.run(
                ["git", "-C", git_root, "diff", sha, f"{sha}^", "--", rf],
                capture_output=True, text=True, errors="replace", timeout=15
            )
            if p_diff.returncode != 0 or not p_diff.stdout.strip():
                continue
            changed_lines = [l for l in p_diff.stdout.splitlines() if l.startswith(("+", "-")) and not l.startswith(("+++", "---"))]
            if not (1 <= len(changed_lines) <= 90):
                continue
            p_chk = subprocess.run(
                ["git", "-C", git_root, "apply", "--check", "-"],
                input=p_diff.stdout, capture_output=True, text=True, errors="replace", timeout=10
            )
            if p_chk.returncode == 0:
                cand_commits.append({
                    "commit_sha": sha,
                    "date": dt,
                    "subject": subj[:140],
                    "touched_focal_files": [rf],
                    "hit_eroded_line": f"{rf}:L{ln}",
                    "diff_lines": len(changed_lines),
                })
                if len(cand_commits) >= 6:
                    break
        if len(cand_commits) >= 6:
            break

    if cand_commits:
        out.append({
            "sample_id": sid,
            "project_name": item["project_name"],
            "language": lang,
            "git_root": git_root,
            "has_eroded_llm_patches": True,
            "candidate_commits": cand_commits,
        })

print(json.dumps(out))
"""


def main() -> None:
    bm = json.loads((DATA_DIR / "v2_frozen_benchmark_light.json").read_text(encoding="utf-8"))
    s_by_id = {s["sample_id"]: s for s in bm["samples"] if s.get("v2_eligible")}
    cb = json.loads((DATA_DIR / "taxonomy_audit_codebook.json").read_text(encoding="utf-8"))["entries"]

    # Collect exact (file, line) targets of surviving mutants for every eroded sample!
    lines_by_sid: Dict[str, Set[Tuple[str, int]]] = defaultdict(set)
    for e in cb:
        sid = e["sample_id"]
        if sid not in s_by_id:
            continue
        s = s_by_id[sid]
        fmap = s.get("fqcn_to_path") or {}
        # Map mutant ID -> (file, line) from s["m_det"]
        mdet_map = {}
        for m in (s.get("m_det") or []):
            cls = m.get("mutatedClass") or ""
            ln = int(m.get("lineNumber") or 0)
            fp = fmap.get(cls) or fmap.get(cls.split("$")[0]) if s["language"] == "java" else (m.get("rel_file") or "")
            if fp and ln > 0:
                mdet_map[(cls.split(".")[-1], ln)] = (fp, ln)
                mdet_map[(cls, ln)] = (fp, ln)
        for sm in (e.get("survived_summaries") or []):
            if isinstance(sm, str):
                # Parse `Class::method:L123` or `pkg.Class:L123`
                import re
                m_ln = re.search(r":L(\d+)", sm)
                if m_ln:
                    ln = int(m_ln.group(1))
                    for (cname, mln), pair in mdet_map.items():
                        if mln == ln and cname in sm:
                            lines_by_sid[sid].add(pair)
                            break
        # Fallback: also include first 6 m_det lines if parsing missed
        if not lines_by_sid[sid]:
            for m in (s.get("m_det") or [])[:6]:
                cls = m.get("mutatedClass") or ""
                ln = int(m.get("lineNumber") or 0)
                fp = fmap.get(cls) or fmap.get(cls.split("$")[0]) if s["language"] == "java" else (m.get("rel_file") or "")
                if fp and ln > 0:
                    lines_by_sid[sid].add((fp, ln))

    spec = []
    for sid, tset in sorted(lines_by_sid.items()):
        s = s_by_id[sid]
        spec.append({
            "sample_id": sid,
            "project_name": s["project_name"],
            "language": s["language"],
            "proj_dir": s["proj_dir"],
            "line_targets": sorted(tset)[:10],
        })

    tmp_spec = DATA_DIR / "_tmp_line_bug_spec.json"
    tmp_spec.write_text(json.dumps(spec), encoding="utf-8")
    tmp_scr = DATA_DIR / "_tmp_line_miner.py"
    tmp_scr.write_text(REMOTE_LINE_MINER, encoding="utf-8")
    try:
        scp_to_remote(tmp_spec, "scripts/_tmp_line_bug_spec.json")
        scp_to_remote(tmp_scr, "scripts/_tmp_line_miner.py")
    finally:
        tmp_spec.unlink(missing_ok=True)
        tmp_scr.unlink(missing_ok=True)

    rc, out, err = run_ssh("python3 scripts/_tmp_line_miner.py < scripts/_tmp_line_bug_spec.json", timeout=240)
    if rc != 0:
        raise RuntimeError(f"Line miner failed rc={rc}: {err[:300]}")
    res = json.loads(out)
    OUT_PATH.write_text(json.dumps({"total_samples": len(res), "samples": res}, indent=2), encoding="utf-8")
    tot_c = sum(len(x["candidate_commits"]) for x in res)
    print(f"[OK] Line-Targeted Bug Miner (`git log -L` on exact eroded lines): {len(res)} eroded tests, {tot_c} bullseye commits!")
    for x in res[:15]:
        c0 = x["candidate_commits"][0]
        print(f"  [{x['language']:6s}] {x['sample_id']} ({len(x['candidate_commits'])}): {c0['commit_sha'][:8]} {c0['date']} @ {c0['hit_eroded_line']} '{c0['subject'][:55]}'")


if __name__ == "__main__":
    main()
