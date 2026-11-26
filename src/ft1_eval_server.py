#!/usr/bin/env python3
"""
FT1 v3.1 persistent remote evaluation server (one SSH connection for the whole campaign).

Protocol (JSON lines over stdin/stdout):
  -> startup: {"type": "hello", "sha256": {<script>: <sha256>}, "pid": <int>}
  <- request: {"id": "<uuid>", "proj_dir": "...", "mode": "in_loop_gate|post_freeze_heldout", "sid": "...", "b64": "..."}
  -> result : {"type": "result", "id": "<uuid>", "stdout": "<wrapper stdout>", "rc": <int>, "wall_sec": <float>}

Each job runs `setsid -w bash ft1_safe_eval.sh ...` with stdin=/dev/null and stdout/stderr redirected to a FILE
(never a pipe), so a dropped SSH connection or a dead server can neither SIGHUP nor SIGPIPE an evaluator mid-edit;
the wrapper's per-project flock + pristine check + post-restore keep repositories consistent across reconnects.
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

ROOT = "/root/sqj_work/FT1"
SCRIPTS = ["remote_eval_v2.py", "ft1_safe_eval.sh", "ft1_verify_and_restore_repo.py",
           "v2_guards_and_attribution.py", "remote_v2_freeze_benchmark_and_mext.py",
           "v2_pytest_attribution_runner.py", "remote_core.py", "remote_python_core.py",
           "v3_remote_coverage_gate.py"]
OUT_LOCK = threading.Lock()


def emit(obj):
    with OUT_LOCK:
        sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
        sys.stdout.flush()


def sha(path):
    try:
        return hashlib.sha256(open(path, "rb").read()).hexdigest()
    except Exception:
        return "MISSING"


def run_job(req):
    t0 = time.time()
    fd, out_path = tempfile.mkstemp(prefix="ft1job_", suffix=".out", dir="/tmp")
    os.close(fd)
    try:
        with open(out_path, "w") as fo:
            proc = subprocess.run(
                ["setsid", "-w", "bash", f"{ROOT}/scripts/ft1_safe_eval.sh",
                 req["proj_dir"], req["mode"], req["sid"], req["b64"]],
                stdin=subprocess.DEVNULL, stdout=fo, stderr=subprocess.STDOUT, cwd=ROOT,
            )
        out = open(out_path, encoding="utf-8", errors="replace").read()
        emit({"type": "result", "id": req["id"], "stdout": out, "rc": proc.returncode,
              "wall_sec": round(time.time() - t0, 2)})
    except Exception as e:
        emit({"type": "result", "id": req["id"], "stdout": f"===FT1_INFRA_FAIL=== server exception: {e}",
              "rc": 94, "wall_sec": round(time.time() - t0, 2)})
    finally:
        try:
            os.unlink(out_path)
        except Exception:
            pass


def main():
    max_workers = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    emit({"type": "hello", "pid": os.getpid(), "max_workers": max_workers,
          "sha256": {s: sha(f"{ROOT}/scripts/{s}") for s in SCRIPTS}})
    ex = ThreadPoolExecutor(max_workers=max_workers)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue
        if req.get("type") == "ping":
            emit({"type": "pong", "id": req.get("id")})
            continue
        ex.submit(run_job, req)
    ex.shutdown(wait=True)


if __name__ == "__main__":
    main()
