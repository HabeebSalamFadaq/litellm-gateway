"""
Single-port entrypoint:
- /admin/*  -> Flask admin app
- everything else -> LiteLLM proxy (running as a subprocess on a localhost port)

This lets the Vercel dashboard talk to /admin/* on the same
https://<gateway>.up.railway.app host that Claude Code uses for
/v1/messages.
"""
import os
import sys
import shutil
import subprocess
import time
import re
import json
import requests
from flask import Flask, request, Response, stream_with_context

# Configuration
LITELLM_PORT = int(os.environ.get("LITELLM_PORT", "4002"))
ADMIN_PORT = int(os.environ.get("ADMIN_PORT", "4001"))
PUBLIC_PORT = int(os.environ.get("PORT", "4000"))
LITELLM_HOST = "127.0.0.1"
CONFIG_PATH = os.environ.get("LITELLM_CONFIG_PATH", "/app/config.yaml")
MASTER_KEY = os.environ.get("LITELLM_MASTER_KEY", "")

# ----------------------------------------------------------------------------
# Resolve the litellm binary path. On Linux the Dockerfile image has it
# in PATH. On dev machines it lives elsewhere. Use shutil.which and fall
# back to a few common locations.
# ----------------------------------------------------------------------------
def find_litellm():
    p = shutil.which("litellm")
    if p:
        return p
    # Common dev locations
    for cand in [
        os.path.expanduser("~/.local/bin/litellm"),
        os.path.expanduser("~/AppData/Roaming/Python/Python314/Scripts/litellm.exe"),
        os.path.expanduser("~/AppData/Roaming/Python/Python313/Scripts/litellm.exe"),
        os.path.expanduser("~/AppData/Roaming/Python/Python312/Scripts/litellm.exe"),
        os.path.expanduser("~/AppData/Roaming/Python/Python311/Scripts/litellm.exe"),
    ]:
        if os.path.exists(cand):
            return cand
    # Last resort: assume PATH in production
    return "litellm"

LITELLM_BIN = find_litellm()

# ----------------------------------------------------------------------------
# Start LiteLLM directly. No entrypoint script, no migrations needed for
# the standard litellm image. The admin API uses SQLite for logging.
# ----------------------------------------------------------------------------
def start_litellm():
    cmd = [
        LITELLM_BIN,
        "--config", CONFIG_PATH,
        "--port", str(LITELLM_PORT),
        "--host", LITELLM_HOST,
        "--detailed_debug",
    ]
    print(f"[BOOT] LITELLM_BIN={LITELLM_BIN}", flush=True)
    print(f"[BOOT] cmd={' '.join(cmd)}", flush=True)
    print(f"[BOOT] env PORT={os.environ.get('PORT')} LITELLM_PORT={LITELLM_PORT} CONFIG_PATH={CONFIG_PATH}", flush=True)
    print(f"[BOOT] MASTER_KEY set: {'LITELLM_MASTER_KEY' in os.environ}", flush=True)
    print(f"[BOOT] DATABASE_URL set: {'DATABASE_URL' in os.environ}", flush=True)
    # Full environment for any providers that need it
    env = os.environ.copy()
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=1,
        universal_newlines=True,
        env=env,
    )
    def _forward(stream, prefix):
        for line in iter(stream.readline, ''):
            if not line:
                break
            print(f"[{prefix}] {line.rstrip()}", flush=True)
    import threading
    threading.Thread(target=_forward, args=(proc.stdout, "LITELLM-OUT"), daemon=True).start()
    threading.Thread(target=_forward, args=(proc.stderr, "LITELLM-ERR"), daemon=True).start()
    # Give it a moment to produce output or fail
    time.sleep(2)
    if proc.poll() is not None:
        print(f"[BOOT] LITELLM EXITED EARLY with code {proc.returncode}", flush=True)
    return proc

def start_admin():
    cmd = [
        sys.executable, "-m", "gunicorn", "-w", "1", "-b", f"127.0.0.1:{ADMIN_PORT}",
        "admin.app:app",
    ]
    return subprocess.Popen(cmd)

def wait_for(url, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(url, timeout=1)
            # Accept anything - the app might be up but not yet serving correctly
            if r.status_code < 600:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False

# ----------------------------------------------------------------------------
# Helpers: pull token + model info from a /v1/messages or /v1/chat/completions response
# ----------------------------------------------------------------------------
def extract_usage_from_response(body_text, req_path):
    """Return (input_tokens, output_tokens, model_group, upstream_model) or (None,)."""
    try:
        body = json.loads(body_text)
    except Exception:
        return None, None, None, None
    usage = body.get("usage") or {}
    inp = usage.get("prompt_tokens") or usage.get("input_tokens")
    out = usage.get("completion_tokens") or usage.get("output_tokens")
    model = body.get("model")
    return inp, out, model, None

def post_log(model_group, upstream_model, api_key_label, provider,
             input_tokens, output_tokens, duration_ms, status, error):
    try:
        requests.post(
            f"http://127.0.0.1:{ADMIN_PORT}/admin/log",
            json={
                "model_group": model_group,
                "upstream_model": upstream_model,
                "api_key_label": api_key_label,
                "provider": provider,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "duration_ms": duration_ms,
                "status": status,
                "error": error,
            },
            timeout=2,
        )
    except Exception:
        pass

# ----------------------------------------------------------------------------
# Router Flask app
# ----------------------------------------------------------------------------
router = Flask(__name__)

# gunicorn is launched against `admin.router:app`, so expose that name.
app = router

# Pass-through endpoints (no body inspection needed)
PASSTHROUGH_PREFIXES = ("/admin", "/health", "/metrics", "/v1/models")

def is_admin(path):
    return path.startswith("/admin") or path == "/admin"

def is_passive(path):
    return any(path.startswith(p) for p in PASSTHROUGH_PREFIXES if p != "/admin")

def get_x_litellm_model_id(headers):
    """LiteLLM sets x-litellm-model-id in response headers."""
    for k, v in headers.items():
        if k.lower() == "x-litellm-model-id":
            return v
    return None

def parse_key_label_from_path(path):
    """Not always present, fallback to upstream model name."""
    m = re.search(r"/deployments/([^/]+)/", path)
    if m:
        return m.group(1)
    return None

# Headers that must not be copied verbatim when proxying.
HOP_BY_HOP = {
    "content-encoding", "content-length", "transfer-encoding", "connection",
    "keep-alive", "proxy-authenticate", "proxy-authorization", "te",
    "trailers", "upgrade",
}

def _request_headers():
    return {k: v for k, v in request.headers
            if k.lower() not in ("host", "content-length")}

def _response_headers(resp):
    return [(k, v) for k, v in resp.headers.items()
            if k.lower() not in HOP_BY_HOP]

def _api_key_label(headers):
    for k, v in headers.items():
        if k.lower() in ("x-litellm-api-key", "x-litellm-key"):
            return v
    return None

def _usage_from_sse(tail):
    """Best-effort usage extraction from the tail of an SSE stream.

    Providers emit usage in the final chunks, so scanning a bounded tail is
    enough and avoids retaining the whole response.
    """
    inp = out = model = None
    for line in tail.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            obj = json.loads(payload)
        except Exception:
            continue
        if not isinstance(obj, dict):
            continue
        message = obj.get("message") if isinstance(obj.get("message"), dict) else {}
        model = obj.get("model") or message.get("model") or model
        usage = obj.get("usage") or {}
        if not usage:
            # Anthropic-style events nest usage under `message`.
            usage = message.get("usage") or {}
        if usage:
            inp = usage.get("prompt_tokens") or usage.get("input_tokens") or inp
            out = usage.get("completion_tokens") or usage.get("output_tokens") or out
    return inp, out, model

def _log_exchange(subpath, resp, body_text, duration_ms, streamed):
    try:
        upstream_id = get_x_litellm_model_id(resp.headers) or ""
        if streamed:
            inp, out, body_model = _usage_from_sse(body_text)
        else:
            inp, out, body_model, _ = extract_usage_from_response(body_text, subpath)
        if not upstream_id and body_model:
            upstream_id = body_model
        if "/" in upstream_id:
            provider = upstream_id.split("/")[0]
        elif upstream_id:
            provider = "openai"
        else:
            provider = "?"
        model_group = (upstream_id.split("/")[-1] if upstream_id else subpath) or "unknown"
        status = "ok" if resp.status_code < 400 else "error"
        err = "" if status == "ok" else f"http {resp.status_code}"
        post_log(model_group, upstream_id, _api_key_label(resp.headers) or "auto",
                 provider, inp, out, duration_ms, status, err)
    except Exception:
        pass

@router.route("/admin/<path:subpath>", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
@router.route("/admin/", methods=["GET"])
def admin_proxy(subpath=""):
    url = f"http://127.0.0.1:{ADMIN_PORT}/admin/{subpath}"
    if request.query_string:
        url += "?" + request.query_string.decode("utf-8")
    try:
        resp = requests.request(
            method=request.method,
            url=url,
            headers=_request_headers(),
            data=request.get_data(),
            allow_redirects=False,
            timeout=60,
        )
        return Response(resp.content, status=resp.status_code,
                        headers=_response_headers(resp))
    except Exception as e:
        return Response(f"admin proxy error: {e}", status=502)

@router.route("/", defaults={"subpath": ""}, methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
@router.route("/<path:subpath>", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
def litellm_proxy(subpath):
    url = f"http://{LITELLM_HOST}:{LITELLM_PORT}/{subpath}"
    if request.query_string:
        url += "?" + request.query_string.decode("utf-8")
    t0 = time.time()
    try:
        resp = requests.request(
            method=request.method,
            url=url,
            headers=_request_headers(),
            data=request.get_data(),
            allow_redirects=False,
            timeout=(10, 600),
            stream=True,
        )
    except Exception as e:
        duration = int((time.time() - t0) * 1000)
        post_log(subpath or "/", "?", "auto", "?", 0, 0, duration, "error", str(e)[:200])
        return Response(f"proxy error: {e}", status=502)

    headers = _response_headers(resp)
    is_sse = "text/event-stream" in resp.headers.get("content-type", "").lower()

    if is_sse:
        # Relay chunks as they arrive. Buffering the full body here would
        # defeat streaming for Claude Code, which consumes SSE incrementally.
        def relay():
            tail = ""
            try:
                for chunk in resp.iter_content(chunk_size=None):
                    if not chunk:
                        continue
                    tail = (tail + chunk.decode("utf-8", errors="replace"))[-16384:]
                    yield chunk
            finally:
                resp.close()
                _log_exchange(subpath, resp, tail,
                              int((time.time() - t0) * 1000), True)

        return Response(stream_with_context(relay()), status=resp.status_code,
                        headers=headers)

    body = resp.content
    _log_exchange(subpath, resp, body.decode("utf-8", errors="replace"),
                  int((time.time() - t0) * 1000), False)
    return Response(body, status=resp.status_code, headers=headers)

def _boot_backends(context):
    """Spawn the LiteLLM proxy and the admin API once."""
    print(f"Starting LiteLLM proxy in background ({context})...", flush=True)
    globals()["lt"] = start_litellm()
    print(f"Router listening on 0.0.0.0:{PUBLIC_PORT}", flush=True)

    import threading

    def boot_admin():
        print("Booting admin API...", flush=True)
        globals()["ad"] = start_admin()
        if not wait_for(f"http://127.0.0.1:{ADMIN_PORT}/health", 30):
            print("WARNING: admin API not ready", flush=True)

    threading.Thread(target=boot_admin, daemon=True).start()


def _acquire_boot_lock():
    """Return True if this process should own the backend subprocesses.

    Uses a non-blocking exclusive flock so that only one gunicorn worker
    spawns LiteLLM even if the worker count is raised later. The lock file
    handle is intentionally kept alive for the process lifetime. If fcntl is
    unavailable (Windows dev), fall back to owning the backends.
    """
    try:
        import fcntl
    except ImportError:
        return True

    lock_path = os.environ.get("ADMIN_DB_PATH", "/tmp/litellm_admin.db") + ".lock"
    try:
        lock_file = open(lock_path, "w")
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError):
        return False

    globals()["_boot_lock_file"] = lock_file
    return True


if __name__ == "__main__":
    # Direct script run (local dev)
    _boot_backends("local dev")
    app.run(host="0.0.0.0", port=PUBLIC_PORT, threaded=True, debug=False)
else:
    # Imported by gunicorn (`gunicorn admin.router:app`)
    if _acquire_boot_lock():
        _boot_backends("gunicorn worker")

#   F o r c e   r e d e p l o y   0 8 / 2 9 / 2 0 2 6   2 1 : 3 8 : 2 1  
 