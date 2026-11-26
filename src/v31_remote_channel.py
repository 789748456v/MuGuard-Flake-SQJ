"""
Local client for the persistent remote evaluation server (`ft1_eval_server.py`).

One long-lived SSH connection carries every evaluation request, eliminating per-evaluation SSH handshakes
(the shared server rate-limits new connections). Verifies deployed evaluator hashes against local sources on
every (re)connect, classifies infrastructure failures (never cached, never recorded as repair outcomes), and
reconnects transparently with throttled backoff.
"""
import hashlib
import json
import os
import random
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from remote_and_llm import SSH_KEY_PATH, load_remote_host, mask_sensitive

SRC_DIR = Path(__file__).resolve().parent
VERIFIED_SCRIPTS = ["remote_eval_v2.py", "ft1_safe_eval.sh", "ft1_verify_and_restore_repo.py",
                    "v2_guards_and_attribution.py", "remote_v2_freeze_benchmark_and_mext.py",
                    "v2_pytest_attribution_runner.py", "remote_core.py", "remote_python_core.py",
                    "v3_remote_coverage_gate.py"]
ALL_SYNC_SCRIPTS = VERIFIED_SCRIPTS + ["ft1_eval_server.py"]


class InfraError(RuntimeError):
    """Infrastructure failure: retry the SAME candidate; never cache; never record as a repair outcome."""


class DeploymentMismatchError(RuntimeError):
    """Raised when remote script SHA-256 differs from local source."""


def build_sync_files_payload() -> Dict[str, str]:
    files: Dict[str, str] = {}
    for fn in ALL_SYNC_SCRIPTS:
        raw = (SRC_DIR / fn).read_bytes()
        if fn.endswith(".sh") or fn == "ft1_verify_and_restore_repo.py":
            raw = raw.replace(b"\r\n", b"\n")
        files[fn] = raw.decode("utf-8")
    return files


def local_sha(fn: str) -> str:
    raw = (SRC_DIR / fn).read_bytes()
    if fn.endswith(".sh") or fn == "ft1_verify_and_restore_repo.py":
        raw = raw.replace(b"\r\n", b"\n")  # deployed executable scripts are CRLF-normalized
    return hashlib.sha256(raw).hexdigest()


class RemoteEvalChannel:
    def __init__(self, remote_workers: int = 4) -> None:
        self.remote_workers = remote_workers
        self.proc: Optional[subprocess.Popen] = None
        self.lock = threading.Lock()
        self.pending: Dict[str, Dict[str, Any]] = {}
        self.pending_lock = threading.Lock()
        self.generation = 0
        self.last_connect = 0.0
        self.deployed_sha: Dict[str, str] = {}

    def _connect(self) -> None:
        wait = 10.0 - (time.time() - self.last_connect)
        if wait > 0:
            time.sleep(wait)
        self.last_connect = time.time()
        user, host, port = load_remote_host()
        boot_py = (
            "import json, os, sys; "
            "line = sys.stdin.readline(); "
            "d = json.loads(line)['files']; "
            "os.makedirs('scripts', exist_ok=True); "
            "[open('scripts/' + k, 'wb').write(v.encode('utf-8')) for k, v in d.items() "
            "if (not os.path.exists('scripts/' + k)) or open('scripts/' + k, 'rb').read() != v.encode('utf-8')]; "
            "[os.chmod('scripts/' + k, 0o755) for k in d if k.endswith('.sh') or 'verify_and_restore' in k]; "
            "sys.path.insert(0, '/root/sqj_work/FT1/scripts'); "
            f"sys.argv = ['ft1_eval_server.py', '{self.remote_workers}']; "
            "import ft1_eval_server; ft1_eval_server.main()"
        )
        cmd = [
            "ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
            "-o", "ConnectTimeout=30", "-o", "ServerAliveInterval=20", "-o", "ServerAliveCountMax=6",
            "-p", str(port), "-i", str(SSH_KEY_PATH), f"{user}@{host}",
            f"cd /root/sqj_work/FT1 && exec python3 -u -c \"{boot_py}\"",
        ]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding="utf-8", errors="replace", bufsize=1)
        try:
            proc.stdin.write(json.dumps({"type": "sync_scripts", "files": build_sync_files_payload()}, ensure_ascii=False) + "\n")
            proc.stdin.flush()
        except Exception:
            pass
        hello_line = proc.stdout.readline()
        if not hello_line:
            err = mask_sensitive(proc.stderr.read()[-300:] if proc.stderr else "")
            proc.kill()
            raise InfraError(f"eval server handshake failed: {err}")
        hello = json.loads(hello_line)
        mism = {fn: (hello["sha256"].get(fn), local_sha(fn)) for fn in VERIFIED_SCRIPTS
                if hello["sha256"].get(fn) != local_sha(fn)}
        if mism:
            proc.kill()
            raise DeploymentMismatchError(f"DEPLOYMENT HASH MISMATCH (refusing to evaluate): {mism}")
        self.deployed_sha = dict(hello["sha256"])
        self.proc = proc
        self.generation += 1
        gen = self.generation
        threading.Thread(target=self._reader, args=(proc, gen), daemon=True).start()

    def _reader(self, proc: subprocess.Popen, gen: int) -> None:
        for line in proc.stdout:
            try:
                msg = json.loads(line)
            except Exception:
                continue
            if msg.get("type") == "result":
                with self.pending_lock:
                    slot = self.pending.get(msg["id"])
                if slot:
                    slot["msg"] = msg
                    slot["event"].set()
        # Connection closed: fail every waiter of this generation so callers retry
        with self.pending_lock:
            for slot in self.pending.values():
                if slot["gen"] == gen and not slot["event"].is_set():
                    slot["msg"] = None
                    slot["event"].set()

    def _ensure(self) -> int:
        with self.lock:
            if self.proc is None or self.proc.poll() is not None:
                init_cool = float(os.environ.get("FT1_INITIAL_SSH_COOLDOWN", "0") or "0")
                if init_cool > 0:
                    os.environ["FT1_INITIAL_SSH_COOLDOWN"] = "0"
                    print(f"[SSH CHANNEL] Waiting {int(init_cool)}s initial silent cool-down so sshd penalty/MaxStartups window clears...", flush=True)
                    time.sleep(init_cool)
                last_err: Optional[Exception] = None
                # Minimal polite attempts (never hammer sshd in a tight loop if PerSourcePenalties/DenyHosts is active)
                for att, wait_s in enumerate((0.0, 45.0)):
                    if wait_s > 0:
                        print(f"[SSH CHANNEL retry {att}/1] Sleeping {wait_s:.0f}s before single retry ({last_err})...", flush=True)
                        time.sleep(wait_s)
                    try:
                        self._connect()
                        break
                    except InfraError as e:
                        last_err = e
                else:
                    raise last_err or InfraError("Failed to establish persistent SSH eval channel")
            return self.generation

    def evaluate_raw(self, proj_dir: str, mode: str, sid: str, b64: str, timeout: float = 2400.0) -> Dict[str, Any]:
        gen = self._ensure()
        rid = uuid.uuid4().hex
        ev = threading.Event()
        with self.pending_lock:
            self.pending[rid] = {"event": ev, "msg": None, "gen": gen}
        try:
            with self.lock:
                self.proc.stdin.write(json.dumps({"id": rid, "proj_dir": proj_dir, "mode": mode, "sid": sid, "b64": b64}) + "\n")
                self.proc.stdin.flush()
        except Exception as e:
            with self.pending_lock:
                self.pending.pop(rid, None)
            with self.lock:
                if self.proc:
                    self.proc.kill()
            raise InfraError(f"send failed: {e}")
        ok = ev.wait(timeout)
        with self.pending_lock:
            slot = self.pending.pop(rid, None)
        if not ok or not slot or slot["msg"] is None:
            raise InfraError("eval server connection lost or job timed out")
        return slot["msg"]


def parse_wrapper_output(stdout: str, sid: str) -> Dict[str, Any]:
    """Parse ft1_safe_eval.sh output; raise InfraError on any infrastructure failure."""
    if "===FT1_INFRA_FAIL===" in stdout:
        raise InfraError(f"{sid}: {stdout.split('===FT1_INFRA_FAIL===', 1)[1][:200]}")
    if "===FT1_POST_DIRTY===" in stdout:
        raise InfraError(f"{sid}: repository not restored after eval: {stdout.split('===FT1_POST_DIRTY===', 1)[1][:200]}")
    s_tag, e_tag = "===FT1_V2_EVAL_START===", "===FT1_V2_EVAL_END==="
    if s_tag not in stdout or e_tag not in stdout:
        raise InfraError(f"{sid}: evaluator produced no result block: {stdout[-400:]}")
    ev = json.loads(stdout.split(s_tag, 1)[1].split(e_tag, 1)[0].strip())
    if ev.get("infra_error"):
        raise InfraError(f"{sid}: evaluator infrastructure error: {str(ev.get('infra_error_detail', ''))[:300]}")
    pre = None
    if "===FT1_PRE_RESTORED===" in stdout:
        pre = stdout.split("===FT1_PRE_RESTORED===", 1)[1].splitlines()[0].strip()
    ev["_pre_restored_files"] = pre
    return ev
