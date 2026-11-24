import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

FT1_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = FT1_ROOT
LOGS_DIR = FT1_ROOT / "logs"
DATA_DIR = FT1_ROOT / "data"
API_LOG_PATH = LOGS_DIR / "api_usage.jsonl"

# Environment-variable-configured paths (never commit credentials or private host files)
REMOTE_INFO_PATH = Path(os.environ.get("FT1_REMOTE_INFO_PATH", str(FT1_ROOT / ".remote" / "info.txt")))
SSH_KEY_PATH = Path(os.environ.get("FT1_SSH_KEY_PATH", str(Path.home() / ".ssh" / "id_ed25519")))
DEEPSEEK_KEY_PATH = Path(os.environ.get("FT1_DEEPSEEK_KEY_PATH", str(FT1_ROOT / ".llm_api" / "deepseek.key")))
QWEN_KEY_PATH = Path(os.environ.get("FT1_QWEN_KEY_PATH", str(FT1_ROOT / ".llm_api" / "qwen.key")))
REMOTE_WORK_DIR = os.environ.get("FT1_REMOTE_WORK_DIR", "/workspace/FT1")


def ensure_dirs() -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_remote_host() -> Tuple[str, str, int]:
    """Load remote execution host from environment variables (`FT1_REMOTE_HOST`, `FT1_REMOTE_USER`, `FT1_REMOTE_PORT`)."""
    host = os.environ.get("FT1_REMOTE_HOST", "").strip()
    user = os.environ.get("FT1_REMOTE_USER", "root").strip()
    port = int(os.environ.get("FT1_REMOTE_PORT", "22"))
    if host:
        return user, host, port
    if REMOTE_INFO_PATH.exists():
        text = REMOTE_INFO_PATH.read_text(encoding="utf-8", errors="ignore")
        ip_match = re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", text)
        if ip_match:
            host = ip_match.group(0)
            pm = re.search(r"(?:port|PORT|-p)\s*[:=]?\s*(\d{2,5})", text)
            if pm:
                port = int(pm.group(1))
            um = re.search(r"([a-zA-Z0-9_.-]+)@" + re.escape(host), text)
            if um:
                user = um.group(1)
            return user, host, port
    raise RuntimeError("Configure FT1_REMOTE_HOST (and optional FT1_REMOTE_USER / FT1_REMOTE_PORT) to execute remote Docker builds.")


def load_api_key(path: Path, env_var: Optional[str] = None) -> str:
    if env_var and os.environ.get(env_var, "").strip():
        return os.environ[env_var].strip()
    if path.exists():
        raw = path.read_text(encoding="utf-8", errors="ignore").strip()
        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line and not line.startswith("sk-"):
                line = line.split("=", 1)[1].strip().strip('"').strip("'")
            return line
    raise RuntimeError(f"Missing API key: set environment variable {env_var or 'DEEPSEEK_API_KEY / DASHSCOPE_API_KEY'} or provide {path.name}.")


def mask_sensitive(text: str) -> str:
    """Redact any accidental IP or sk- token from stdout/stderr before displaying or saving."""
    if not text:
        return text
    host = os.environ.get("FT1_REMOTE_HOST", "").strip()
    if host:
        text = text.replace(host, "<REDACTED_REMOTE_IP>")
    for env_k in ("DEEPSEEK_API_KEY", "DASHSCOPE_API_KEY", "OPENAI_API_KEY"):
        val = os.environ.get(env_k, "").strip()
        if val and len(val) > 6:
            text = text.replace(val, "<REDACTED_API_KEY>")
    text = re.sub(r"\bsk-[A-Za-z0-9_-]{18,}\b", "<REDACTED_SK>", text)
    return text


def infer_provider(model: str) -> str:
    m = model.lower().strip()
    if m.startswith("deepseek"):
        return "deepseek"
    return "qwen"


def run_ssh(remote_cmd: str, timeout: int = 300, retries: int = 2) -> Tuple[int, str, str]:
    user, host, port = load_remote_host()
    wrapped_cmd = f"mkdir -p {REMOTE_WORK_DIR} && cd {REMOTE_WORK_DIR} && {remote_cmd}"
    cmd = [
        "ssh", "-n", "-o", "BatchMode=yes", "-o", "ControlMaster=no",
        "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=30",
        "-p", str(port), "-i", str(SSH_KEY_PATH), f"{user}@{host}", wrapped_cmd,
    ]
    last_err = None
    for attempt in range(retries + 1):
        try:
            proc = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
            if proc.returncode == 255 and attempt < retries:
                time.sleep(2 * (attempt + 1))
                continue
            return proc.returncode, mask_sensitive(proc.stdout), mask_sensitive(proc.stderr)
        except subprocess.TimeoutExpired as e:
            out_s = mask_sensitive(e.stdout.decode("utf-8", errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or ""))
            err_s = mask_sensitive(e.stderr.decode("utf-8", errors="replace") if isinstance(e.stderr, bytes) else (e.stderr or ""))
            last_err = RuntimeError(f"SSH timed out after {timeout}s. stdout={out_s[-400:]} stderr={err_s[-400:]}")
            if attempt < retries and timeout <= 90:
                time.sleep(2 * (attempt + 1))
                continue
            raise last_err from None
    raise last_err or RuntimeError("SSH failed")


def scp_to_remote(local_path: Path, remote_rel_path: str, timeout: int = 120) -> None:
    if remote_rel_path.startswith("/") or ".." in remote_rel_path:
        raise ValueError("remote_rel_path must be relative inside REMOTE_WORK_DIR")
    user, host, port = load_remote_host()
    target = f"{REMOTE_WORK_DIR}/{remote_rel_path}"
    parent = os.path.dirname(target).replace("\\", "/")
    rc, _, err = run_ssh(f"mkdir -p '{parent}'", timeout=90)
    if rc != 0:
        raise RuntimeError(f"Failed creating remote dir: {err}")
    cmd = ["scp", "-o", "BatchMode=yes", "-o", "ControlMaster=no", "-o", "StrictHostKeyChecking=accept-new",
           "-P", str(port), "-i", str(SSH_KEY_PATH), str(local_path), f"{user}@{host}:{target}"]
    proc = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"SCP upload failed: {mask_sensitive(proc.stderr)}")


def scp_from_remote(remote_rel_path: str, local_path: Path, timeout: int = 180) -> None:
    local_resolved = local_path.resolve()
    if FT1_ROOT.resolve() not in local_resolved.parents and local_resolved != FT1_ROOT.resolve():
        raise ValueError("local_path must be inside repository root")
    if remote_rel_path.startswith("/") or ".." in remote_rel_path:
        raise ValueError("remote_rel_path must be relative inside REMOTE_WORK_DIR")
    local_path.parent.mkdir(parents=True, exist_ok=True)
    user, host, port = load_remote_host()
    source = f"{REMOTE_WORK_DIR}/{remote_rel_path}"
    cmd = ["scp", "-o", "BatchMode=yes", "-o", "ControlMaster=no", "-o", "StrictHostKeyChecking=accept-new",
           "-P", str(port), "-i", str(SSH_KEY_PATH), f"{user}@{host}:{source}", str(local_path)]
    proc = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"SCP download failed: {mask_sensitive(proc.stderr)}")


def log_api_usage(entry: Dict[str, Any]) -> None:
    ensure_dirs()
    with API_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def chat_completion(
    provider: Optional[str],
    model: str,
    messages: List[Dict[str, str]],
    temperature: float = 0.0,
    max_tokens: int = 6144,
    base_url: Optional[str] = None,
    purpose: str = "general",
    timeout: int = 180,
    extra_body: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Call OpenAI-compatible chat completions using environment variables `DEEPSEEK_API_KEY` or `DASHSCOPE_API_KEY`."""
    ensure_dirs()
    eff_provider = provider if provider in ("deepseek", "qwen") else infer_provider(model)
    if eff_provider == "deepseek":
        key = load_api_key(DEEPSEEK_KEY_PATH, env_var="DEEPSEEK_API_KEY")
        url_base = base_url or "https://api.deepseek.com/v1"
    elif eff_provider == "qwen":
        key = load_api_key(QWEN_KEY_PATH, env_var="DASHSCOPE_API_KEY")
        url_base = base_url or "https://dashscope.aliyuncs.com/compatible-mode/v1"
    else:
        raise ValueError(f"Unknown provider: {eff_provider}")

    endpoint = url_base.rstrip("/") + "/chat/completions"
    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if extra_body:
        payload.update(extra_body)
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "text/event-stream, application/json",
            "Authorization": f"Bearer {key}",
            "User-Agent": "MuGuard-Flake-Client/2.4",
        },
        method="POST",
    )
    t0 = time.time()
    status_str = "ok"
    err_msg = None
    resp_obj: Dict[str, Any] = {}
    content_parts: List[str] = []
    reasoning_parts: List[str] = []
    usage: Dict[str, Any] = {}
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ctype = (resp.headers.get("Content-Type") or "").lower()
            if "text/event-stream" in ctype or payload.get("stream"):
                raw_lines: List[str] = []
                got_sse = False
                max_wall_sec = max(float(timeout) * 2.5, 420.0)
                for raw_line in resp:
                    if time.time() - t0 > max_wall_sec:
                        raise TimeoutError(f"SSE stream wall-clock exceeded {max_wall_sec:.0f}s")
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line or line.startswith(":"):
                        continue
                    if line.startswith("data:"):
                        got_sse = True
                        data_str = line[5:].strip()
                        if data_str == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(chunk, dict):
                            if chunk.get("usage"):
                                usage = chunk["usage"]
                            choices = chunk.get("choices") or []
                            if choices:
                                delta = choices[0].get("delta") or choices[0].get("message") or {}
                                if delta.get("content"):
                                    content_parts.append(delta["content"])
                                if delta.get("reasoning_content"):
                                    reasoning_parts.append(delta["reasoning_content"])
                    else:
                        raw_lines.append(line)
                if not got_sse and raw_lines:
                    resp_obj = json.loads("\n".join(raw_lines))
                    usage = resp_obj.get("usage", {}) if isinstance(resp_obj, dict) else {}
                    msg_o = resp_obj["choices"][0]["message"]
                    content_parts.append(msg_o.get("content", "") or "")
                    reasoning_parts.append(msg_o.get("reasoning_content", "") or "")
            else:
                body = resp.read().decode("utf-8", errors="replace")
                resp_obj = json.loads(body)
                usage = resp_obj.get("usage", {}) if isinstance(resp_obj, dict) else {}
                msg_o = resp_obj["choices"][0]["message"]
                content_parts.append(msg_o.get("content", "") or "")
                reasoning_parts.append(msg_o.get("reasoning_content", "") or "")
    except urllib.error.HTTPError as e:
        status_str = f"http_{e.code}"
        err_body = e.read().decode("utf-8", errors="replace") if e.fp else str(e)
        err_msg = mask_sensitive(err_body[:500])
    except Exception as e:
        status_str = "error"
        err_msg = mask_sensitive(str(e)[:500])

    latency = round(time.time() - t0, 3)
    completion_details = (usage.get("completion_tokens_details") or {}) if isinstance(usage, dict) else {}
    log_record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "provider": eff_provider,
        "base_url": url_base,
        "model": model,
        "temperature": temperature,
        "prompt_tokens": usage.get("prompt_tokens", 0) if isinstance(usage, dict) else 0,
        "completion_tokens": usage.get("completion_tokens", 0) if isinstance(usage, dict) else 0,
        "reasoning_tokens": completion_details.get("reasoning_tokens", 0),
        "total_tokens": usage.get("total_tokens", 0) if isinstance(usage, dict) else 0,
        "latency_sec": latency,
        "status": status_str,
        "purpose": purpose,
    }
    if err_msg:
        log_record["error"] = err_msg
    log_api_usage(log_record)
    if status_str != "ok":
        raise RuntimeError(f"API call failed ({eff_provider}/{model} -> {status_str}): {err_msg}")
    return {"content": "".join(content_parts), "reasoning_content": "".join(reasoning_parts), "usage": usage, "latency_sec": latency, "raw": resp_obj}
