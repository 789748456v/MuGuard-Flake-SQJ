#!/usr/bin/env python3
"""
FT1 v3.1 Frozen Baseline Verifier & Restorer (`R3-2`).

Enforces under `ft1_safe_eval.sh`'s per-project `flock`:
  1. Explicit commit verification: `git rev-parse HEAD == frozen_sha` (never trusts the git index).
  2. Full tracked-tree & staged-index comparison against `frozen_sha`: ONLY paths listed in
     `approved_modified_files` (setup-configured `pom.xml` files) may differ from `frozen_sha`.
  3. Exact SHA-256 verification of every approved `pom.xml` against the frozen baseline manifest,
     catching any candidate-injected `<dependency>` or un-restored POM modification.
  4. Stray untracked source/test/runner file detection (`FT1OrderRunner*`, `.ft1_v2_mut_*`, etc.).
  5. Restoration against explicit `frozen_sha` + approved POM backup bytes, followed by mandatory
     re-verification; aborts with `===FT1_INFRA_FAIL===` (`exit 97`) before evaluation if dirty
     state cannot be verified clean, and emits `===FT1_POST_DIRTY===` (`exit 98`) if evaluation
     leaves any un-restored state behind.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
from typing import Any, Dict, List, Tuple

DEFAULT_MANIFEST = "/root/sqj_work/FT1/data/frozen_repo_manifest.json"
DEFAULT_APPROVED_ROOT = "/root/sqj_work/FT1/data/approved_poms"

BENIGN_UNTRACKED_PREFIXES = (
    ".ft1_venv/",
    ".eggs/",
    "build/",
    "dist/",
    ".nondex/",
    ".pytest_cache/",
)
SOURCE_OR_BUILD_EXTS = (
    ".java", ".py", ".xml", ".gradle", ".kt", ".groovy", ".scala", ".properties", ".yaml", ".yml",
)
STRAY_HARNESS_MARKERS = (
    "FT1OrderRunner",
    "FT1HeldoutRepeatedRunner",
    ".ft1_v2_mut_",
    "_ft1_rep_heldout_plug.py",
)


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def run_git(pdir: str, args: List[str]) -> Tuple[int, str, str]:
    p = subprocess.run(
        ["git", "-C", pdir] + args,
        capture_output=True,
        text=True,
        errors="replace",
        timeout=60,
    )
    return p.returncode, (p.stdout or ""), (p.stderr or "")


def classify_untracked(pdir: str) -> List[str]:
    rc, out, _ = run_git(pdir, ["ls-files", "--others", "--exclude-standard"])
    if rc != 0:
        return [f"git_ls_files_failed(rc={rc})"]
    bad: List[str] = []
    for raw in out.splitlines():
        rel = raw.strip().replace("\\", "/")
        if not rel:
            continue
        base = os.path.basename(rel)
        if any(m in base for m in STRAY_HARNESS_MARKERS):
            bad.append(rel)
            continue
        if rel in (".coverage", ".ft1_cov.json") or "/.nondex/" in ("/" + rel) or "/__pycache__/" in ("/" + rel):
            continue
        if any(rel.startswith(pref) for pref in BENIGN_UNTRACKED_PREFIXES):
            continue
        parts = rel.split("/")
        if any(p.endswith(".egg-info") or p == "target" for p in parts):
            continue
        if rel.endswith(SOURCE_OR_BUILD_EXTS) or parts[0] in ("src", "test", "tests"):
            bad.append(rel)
    return sorted(set(bad))


def verify_baseline(pdir: str, entry: Dict[str, Any], approved_root: str) -> Tuple[bool, List[str]]:
    issues: List[str] = []
    fsha = entry["frozen_sha"]
    approved: Dict[str, Dict[str, Any]] = entry.get("approved_modified_files") or {}

    rc_h, out_h, _ = run_git(pdir, ["rev-parse", "HEAD"])
    head = out_h.strip().splitlines()[0].strip() if (rc_h == 0 and out_h.strip()) else ""
    if rc_h != 0 or head != fsha:
        issues.append(f"HEAD={head or 'ERR'}!=frozen_sha={fsha[:12]}")

    rc_w, out_w, _ = run_git(pdir, ["diff", "--name-only", fsha, "--"])
    rc_i, out_i, _ = run_git(pdir, ["diff", "--cached", "--name-only", fsha, "--"])
    if rc_w != 0 or rc_i != 0:
        issues.append(f"git_diff_failed(w={rc_w},i={rc_i})")
    else:
        changed = sorted(set(
            x.strip().replace("\\", "/")
            for x in (out_w.splitlines() + out_i.splitlines())
            if x.strip()
        ))
        for rel in changed:
            if rel not in approved:
                issues.append(f"unapproved_diff:{rel}")

    for rel, meta in sorted(approved.items()):
        abs_p = os.path.join(pdir, rel)
        if not os.path.isfile(abs_p):
            issues.append(f"missing_approved_pom:{rel}")
            continue
        cur_sha = sha256_file(abs_p)
        if cur_sha != meta["sha256"]:
            issues.append(f"pom_sha_mismatch:{rel}({cur_sha[:10]}!={meta['sha256'][:10]})")
        bak_p = os.path.join(approved_root, meta["backup_rel"])
        if not os.path.isfile(bak_p):
            issues.append(f"missing_pom_backup:{meta['backup_rel']}")

    for urel in classify_untracked(pdir):
        issues.append(f"untracked:{urel}")

    return (len(issues) == 0), issues


def restore_baseline(pdir: str, entry: Dict[str, Any], approved_root: str, dirty_hints: List[str] = None) -> List[str]:
    errs: List[str] = []
    fsha = entry["frozen_sha"]
    approved: Dict[str, Dict[str, Any]] = entry.get("approved_modified_files") or {}

    rc_r, out_r, err_r = run_git(pdir, ["reset", "--hard", fsha])
    if rc_r != 0:
        errs.append(f"git_reset_failed(rc={rc_r}):{(out_r + ' ' + err_r)[-160:].strip()}")

    # Bump mtime on any previously dirty tracked files so incremental `mvn test-compile` always recompiles them
    for hint in (dirty_hints or []):
        if hint.startswith("unapproved_diff:"):
            rel_d = hint.split(":", 1)[1]
            abs_d = os.path.join(pdir, rel_d)
            if os.path.isfile(abs_d):
                try:
                    os.utime(abs_d, None)
                except OSError:
                    pass

    for rel, meta in sorted(approved.items()):
        bak_p = os.path.join(approved_root, meta["backup_rel"])
        dst_p = os.path.join(pdir, rel)
        try:
            if not os.path.isfile(bak_p):
                errs.append(f"missing_backup:{bak_p}")
                continue
            os.makedirs(os.path.dirname(dst_p), exist_ok=True)
            shutil.copyfile(bak_p, dst_p)
        except Exception as e:
            errs.append(f"copy_pom_failed:{rel}:{e}")

    for urel in classify_untracked(pdir):
        if urel.startswith("git_ls_files_failed"):
            errs.append(urel)
            continue
        abs_u = os.path.join(pdir, urel)
        try:
            if os.path.isdir(abs_u) and not os.path.islink(abs_u):
                shutil.rmtree(abs_u)
            elif os.path.lexists(abs_u):
                os.remove(abs_u)
        except Exception as e:
            errs.append(f"remove_untracked_failed:{urel}:{e}")
    return errs


def main() -> None:
    if len(sys.argv) < 3:
        print("===FT1_INFRA_FAIL=== usage: ft1_verify_and_restore_repo.py <proj_dir> <pre|post>")
        sys.exit(97)
    pdir = os.path.normpath(sys.argv[1].strip())
    phase = sys.argv[2].strip()
    manifest_path = os.environ.get("FT1_REPO_MANIFEST", DEFAULT_MANIFEST)
    approved_root = os.environ.get("FT1_APPROVED_POMS_DIR", DEFAULT_APPROVED_ROOT)

    if not os.path.isdir(pdir) or not os.path.isdir(os.path.join(pdir, ".git")):
        print(f"===FT1_INFRA_FAIL=== missing git repo at {pdir}")
        sys.exit(95)
    if not os.path.isfile(manifest_path):
        print(f"===FT1_INFRA_FAIL=== missing frozen_repo_manifest at {manifest_path}")
        sys.exit(97)

    try:
        manifest = json.load(open(manifest_path, encoding="utf-8"))
    except Exception as e:
        print(f"===FT1_INFRA_FAIL=== corrupt frozen_repo_manifest: {e}")
        sys.exit(97)

    repos = manifest.get("repos") or {}
    entry = repos.get(pdir) or repos.get(sys.argv[1].strip())
    if not entry:
        print(f"===FT1_INFRA_FAIL=== repo {pdir} not registered in frozen_repo_manifest")
        sys.exit(97)

    ok, issues = verify_baseline(pdir, entry, approved_root)
    if ok:
        sys.exit(0)

    restore_errs = restore_baseline(pdir, entry, approved_root, dirty_hints=issues)
    ok2, issues2 = verify_baseline(pdir, entry, approved_root)

    if phase == "pre":
        if restore_errs or not ok2:
            detail = "; ".join(restore_errs + issues2)
            print(f"===FT1_INFRA_FAIL=== pre-eval baseline verification failed: {detail}")
            sys.exit(97)
        print(f"===FT1_PRE_RESTORED=== {', '.join(issues)}")
        sys.exit(0)
    else:
        tracked_or_pom_issues = [x for x in issues if not x.startswith("untracked:")]
        detail = ", ".join(issues)
        if restore_errs or not ok2:
            rem = "; ".join(restore_errs + issues2)
            print(f"===FT1_POST_DIRTY=== {detail} | UNRESTORED: {rem}")
            sys.exit(98)
        if tracked_or_pom_issues:
            print(f"===FT1_POST_DIRTY=== {detail} (restored to baseline)")
            sys.exit(98)
        # Only runtime untracked test side-effect files (e.g., OD polluter sqlite/config) existed and are now verified removed
        print(f"===FT1_POST_CLEANED_UNTRACKED=== {detail}")
        sys.exit(0)


if __name__ == "__main__":
    main()
