"""
FT-1 v3.0 Real Historical Production Bug-Fix Commit Miner (Phase 1: Read-Only Git Scan).

Scans all Java & Python repositories on the remote execution server for real historical
bug-fix commits touching the exact focal production files exercised by our 203 eligible
flaky tests. Prioritizes tests that have eroded LLM patches so that we can replay real
historical production bugs against:
  Original Test vs. Unconstrained Eroded Patch vs. Structural Guard vs. Coverage Gate vs. MuGuard-Flake!

Outputs: `data/v3_real_bug_candidates.json`
"""
import base64
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent))
from remote_and_llm import DATA_DIR, run_ssh

OUT_PATH = DATA_DIR / "v3_real_bug_candidates.json"

REMOTE_MINER_PY = r"""
import json, os, re, subprocess, sys

spec = json.loads(sys.stdin.read())
BUG_RE = re.compile(r"\b(fix|fixed|fixes|bug|npe|null|error|issue|regression|crash|wrong|broken|patch|defect|correct|handle|prevent|avoid|edge)\b", re.I)

out = []
for item in spec:
    sid = item["sample_id"]
    lang = item["language"]
    pdir = item["proj_dir"]
    files = item["focal_files"]
    # Find actual .git root
    git_root = pdir
    if not os.path.isdir(os.path.join(git_root, ".git")):
        # Check one level down (Java projects layout: projects/<hash>/<repo_name>/.git)
        for sub in os.listdir(pdir) if os.path.isdir(pdir) else []:
            cand = os.path.join(pdir, sub)
            if os.path.isdir(os.path.join(cand, ".git")):
                git_root = cand
                break
    if not os.path.isdir(os.path.join(git_root, ".git")):
        continue

    rel_files = []
    for f in files:
        if not f:
            continue
        if os.path.isabs(f):
            if f.startswith(git_root):
                rf = os.path.relpath(f, git_root).replace("\\", "/")
                rel_files.append(rf)
        else:
            cand = os.path.join(git_root, f)
            if os.path.exists(cand):
                rel_files.append(f.replace("\\", "/"))
            elif os.path.exists(os.path.join(pdir, f)):
                rf = os.path.relpath(os.path.join(pdir, f), git_root).replace("\\", "/")
                rel_files.append(rf)
    rel_files = sorted(set(rel_files))[:12]
    if not rel_files:
        continue

    # Search last 120 commits touching focal production files
    cmd = ["git", "-C", git_root, "log", "-n", "120", "--no-merges", "--format=%H\t%ad\t%s", "--date=short", "--"] + rel_files
    p = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=25)
    if p.returncode != 0 or not p.stdout.strip():
        continue

    def has_executable_code_change(diff_txt, lang_name):
        for ln in diff_txt.splitlines():
            if not ln or ln.startswith(("+++", "---", "@@", "diff ", "index ")):
                continue
            if ln[0] not in ("+", "-"):
                continue
            body = ln[1:].strip()
            if not body:
                continue
            if lang_name == "java":
                if body.startswith(("//", "/*", "*", "*/", "@param", "@return", "@throws", "import ")):
                    continue
            else:
                if body.startswith(("#", '"' * 3, "'" * 3)):
                    continue
            return True
        return False

    cand_commits = []
    for line in p.stdout.splitlines():
        parts = line.split("\t", 2)
        if len(parts) < 3:
            continue
        sha, dt, subj = parts
        if re.search(r"\b(javadoc|typo|readme|changelog|comment|format|checkstyle|lint|whitespace)\b", subj, re.I):
            continue
        if not BUG_RE.search(subj):
            continue
        # Inspect diff stats for this commit on ONLY the focal files (must be small/focused <= 80 changed lines)
        p2 = subprocess.run(
            ["git", "-C", git_root, "show", "--numstat", "--format=", sha, "--"] + rel_files,
            capture_output=True, text=True, errors="replace", timeout=15
        )
        ins = dels = 0
        touched = []
        for nl in p2.stdout.splitlines():
            cols = nl.split("\t")
            if len(cols) == 3 and cols[0].isdigit() and cols[1].isdigit():
                ins += int(cols[0])
                dels += int(cols[1])
                touched.append(cols[2])
        if touched and 1 <= (ins + dels) <= 80:
            # Check clean reverse-apply onto current HEAD AND executable code change!
            p_diff = subprocess.run(
                ["git", "-C", git_root, "diff", sha, f"{sha}^", "--"] + touched,
                capture_output=True, text=True, errors="replace", timeout=15
            )
            if p_diff.returncode == 0 and p_diff.stdout.strip() and has_executable_code_change(p_diff.stdout, lang):
                p_chk = subprocess.run(
                    ["git", "-C", git_root, "apply", "--check", "-"],
                    input=p_diff.stdout, capture_output=True, text=True, errors="replace", timeout=10
                )
                if p_chk.returncode == 0:
                    cand_commits.append({
                        "commit_sha": sha,
                        "date": dt,
                        "subject": subj[:140],
                        "touched_focal_files": touched,
                        "ins": ins,
                        "dels": dels,
                        "clean_revert_on_head": True,
                    })
                    if len(cand_commits) >= 8:
                        break
    if cand_commits:
        out.append({
            "sample_id": sid,
            "project_name": item["project_name"],
            "language": lang,
            "git_root": git_root,
            "has_eroded_llm_patches": item["has_eroded"],
            "candidate_commits": cand_commits,
        })

print(json.dumps(out))
"""


def main() -> None:
    bm = json.loads((DATA_DIR / "v2_frozen_benchmark_light.json").read_text(encoding="utf-8"))
    tax_cb = json.loads((DATA_DIR / "taxonomy_audit_codebook.json").read_text(encoding="utf-8"))["entries"]
    eroded_sids = {e["sample_id"] for e in tax_cb}

    spec: List[Dict[str, Any]] = []
    for s in bm["samples"]:
        if not s.get("v2_eligible"):
            continue
        lang = s["language"]
        focal_files: List[str] = []
        if lang == "java":
            fmap = s.get("fqcn_to_path") or {}
            for fc in (s.get("focal_classes") or []):
                if fc in fmap:
                    focal_files.append(fmap[fc])
            if not focal_files:
                focal_files = list(fmap.values())
        else:
            # Extract python focal file paths from m_det
            seen = set()
            for m in (s.get("m_det") or []):
                fp = m.get("rel_file") or m.get("file_rel") or m.get("file") or ""
                if fp and fp not in seen:
                    seen.add(fp)
                    focal_files.append(fp)
        spec.append({
            "sample_id": s["sample_id"],
            "project_name": s["project_name"],
            "language": lang,
            "proj_dir": s["proj_dir"],
            "focal_files": focal_files,
            "has_eroded": s["sample_id"] in eroded_sids,
        })

    # Prioritize samples that actually have eroded LLM patches!
    spec.sort(key=lambda x: (0 if x["has_eroded"] else 1, x["sample_id"]))
    b64_script = base64.b64encode(REMOTE_MINER_PY.encode("utf-8")).decode("ascii")
    b64_spec = base64.b64encode(json.dumps(spec).encode("utf-8")).decode("ascii")

    # Upload spec via base64 file on remote to avoid command-line length cap
    cmd = f"""python3 -c '
import base64, subprocess, sys
script = base64.b64decode("{b64_script}").decode("utf-8")
open("/tmp/ft1_mine_bugs.py", "w").write(script)
' && echo "{b64_spec}" | base64 -d | python3 /tmp/ft1_mine_bugs.py"""

    # Use scp to upload spec cleanly since spec can be ~40 KB
    tmp_local = DATA_DIR / "_tmp_bug_mine_spec.json"
    tmp_local.write_text(json.dumps(spec), encoding="utf-8")
    from remote_and_llm import scp_to_remote
    scp_to_remote(tmp_local, "scripts/_tmp_bug_mine_spec.json")
    tmp_local.unlink(missing_ok=True)

    tmp_script = DATA_DIR / "_tmp_mine_bugs.py"
    tmp_script.write_text(REMOTE_MINER_PY, encoding="utf-8")
    scp_to_remote(tmp_script, "scripts/_tmp_mine_bugs.py")
    tmp_script.unlink(missing_ok=True)

    rc, out, err = run_ssh("python3 scripts/_tmp_mine_bugs.py < scripts/_tmp_bug_mine_spec.json", timeout=240)
    if rc != 0:
        raise RuntimeError(f"Remote bug miner failed (rc={rc}): {err[:400]}")
    res = json.loads(out)
    OUT_PATH.write_text(json.dumps({"total_samples_with_candidates": len(res), "samples": res}, indent=2), encoding="utf-8")

    tot_commits = sum(len(x["candidate_commits"]) for x in res)
    eroded_hits = [x for x in res if x["has_eroded_llm_patches"]]
    eroded_commits = sum(len(x["candidate_commits"]) for x in eroded_hits)
    print(f"[OK] Real Historical Bug-Fix Commit Mining Complete!")
    print(f"     Samples with clean-revertable focal bug-fix commits: {len(res)} ({tot_commits} commits total)")
    print(f"     Of which HAVE eroded LLM patches: {len(eroded_hits)} tests ({eroded_commits} clean-revertable bug commits!)")
    for x in eroded_hits[:10]:
        c0 = x["candidate_commits"][0]
        print(f"       - [{x['language']}] {x['sample_id']}: {len(x['candidate_commits'])} commits (e.g. {c0['commit_sha'][:8]} {c0['date']} '{c0['subject'][:65]}')")


if __name__ == "__main__":
    main()
