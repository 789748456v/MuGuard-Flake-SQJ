#!/bin/bash
# FT1 v3.1 disconnect-safe, per-project-locked, frozen-baseline-verified evaluator wrapper (`R3-2`).
# Usage: ft1_safe_eval.sh <proj_dir> <mode> <sample_id> <b64_candidate>
# Guarantees:
#   1. Per-project flock: no two evaluations (or an orphaned one) touch the same repo concurrently.
#   2. Pre-eval baseline check INSIDE the lock against explicit frozen commit SHA + exact approved POM SHA-256s;
#      restores against the frozen baseline and re-verifies, aborting with FT1_INFRA_FAIL before evaluation on failure.
#   3. Post-eval baseline verification + restoration before releasing the lock; emits FT1_POST_DIRTY if evaluation left changes.
#   4. Invoked via `setsid -w` with stdin=/dev/null and stdout to a file, so an SSH drop cannot SIGHUP it mid-edit.
PDIR="$1"; MODE="$2"; SID="$3"; B64="$4"
LOCK="/tmp/ft1lock_$(echo -n "$PDIR" | md5sum | cut -c1-16)"
exec 9>"$LOCK"
flock -w 1800 9 || { echo "===FT1_INFRA_FAIL=== lock timeout"; exit 96; }
python3 /root/sqj_work/FT1/scripts/ft1_verify_and_restore_repo.py "$PDIR" pre || exit $?
cd /root/sqj_work/FT1 || { echo "===FT1_INFRA_FAIL=== missing FT1 root"; exit 95; }
python3 /root/sqj_work/FT1/scripts/remote_eval_v2.py "$MODE" "$SID" "$B64"
rc=$?
python3 /root/sqj_work/FT1/scripts/ft1_verify_and_restore_repo.py "$PDIR" post || exit $?
echo "===FT1_EVAL_RC=== $rc"
