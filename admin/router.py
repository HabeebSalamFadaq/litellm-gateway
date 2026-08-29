"""
Single-port entrypoint:
- /admin/*  -> Flask admin app
- everything else -> LiteLLM proxy (running as a subprocess on a localhost port)

This lets the Vercel dashboard talk to /admin/* on the same
https://<gateway>.up.railway.app host that Claude Code uses for
/v1/messages.
"""
import os
import subprocess
import time
import re
import json
import requests
from flask import Flask, request, Response
import gunicorn.app.base
import gunicorn.six

# Configuration
LITELLM_PORT = int(os.environ.get("LITELLM_PORT", "4002"))
ADMIN_PORT = int(os.environ.get("ADMIN_PORT", "4001"))
PUBLIC_PORT = int(os.environ.get("PORT", "4000"))
LITELLM_HOST = "127.0.0.1"
CONFIG_PATH = os.environ.get("LITELLM_CONFIG_PATH", "/app/config.yaml")
MASTER_KEY = os.environ.get("LITELLM_MASTER_KEY", "")

# ----------------------------------------------------------------------------
# Start LiteLLM as a subprocess, bound to a localhost port
# ----------------------------------------------------------------------------
def start_litellm():
    cmd = [
        "litellm",
        "--config", CONFIG_PATH,
        "--port", str(LITELLM_PORT),
        "--host", LITELLM_HOST,
    ]
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def start_admin():
    cmd = [
        "gunicorn", "-w", "1", "-b", f"127.0.0.1:{ADMIN_PORT}",
        "admin.app:app",
    ]
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def wait_for(url, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(url, timeout=1)
            if r.status_code < 500:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False

print("Starting LiteLLM proxy...")
lt = start_litellm()
if not wait_for(f"http://{LITELLM_HOST}:{LITELLM_PORT}/health/liveliness", 60):
    print("LiteLLM did not become healthy")
    lt.terminate()
    raise SystemExit(1)

print("Starting admin API...")
ad = start_admin()
if not wait_for(f"http://127.0.0.1:{ADMIN_PORT}/health", 20):
    print("Admin API did not become healthy")
    ad.terminate()
    raise SystemExit(1)

print(f"Both up. Router listening on 0.0.0.0:{PUBLIC_PORT}")

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
            headers={k: v for k, v in request.headers if k.lower() not in ("host", "content-length")},
            data=request.get_data(),
            allow_redirects=False,
            timeout=60,
        )
        excluded = {"content-encoding", "content-length", "transfer-encoding", "connection"}
        headers = [(k, v) for k, v in resp.headers.items() if k.lower() not in excluded]
        return Response(resp.content, status=resp.status_code, headers=headers)
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
            headers={k: v for k, v in request.headers if k.lower() not in ("host", "content-length")},
            data=request.get_data(),
            allow_redirects=False,
            timeout=300,
            stream=True,
        )
        body_chunks = []
        for chunk in resp.iter_content(chunk_size=4096):
            body_chunks.append(chunk)
        body = b"".join(body_chunks)
        duration = int((time.time() - t0) * 1000)
        # Post-process response: extract usage
        try:
            upstream_id = get_x_litellm_model_id(resp.headers) or ""
            provider = upstream_id.split("/")[0] if upstream_id else "?"
            # model_group: try from path or upstream_id
            model_group = (upstream_id.split("/")[-1] if upstream_id
                           else subpath) or "unknown"
            inp, out, body_model, _ = extract_usage_from_response(body.decode("utf-8", errors="replace"), subpath)
            # If LiteLLM didn't set x-litellm-model-id, fall back to body's model field
            if not upstream_id and body_model:
                upstream_id = body_model
                provider = body_model.split("/")[0] if "/" in body_model else "openai"
            # Pull api_key label from x-litellm-key header if present
            api_key_label = None
            for k, v in resp.headers.items():
                if k.lower() in ("x-litellm-api-key", "x-litellm-key"):
                    api_key_label = v
                    break
            # Status code
            status = "ok" if resp.status_code < 400 else "error"
            err = "" if status == "ok" else f"http {resp.status_code}"
            post_log(model_group, upstream_id, api_key_label or "auto",
                      provider, inp, out, duration, status, err)
        except Exception:
            pass
        excluded = {"content-encoding", "content-length", "transfer-encoding", "connection"}
        headers = [(k, v) for k, v in resp.headers.items() if k.lower() not in excluded]
        return Response(body, status=resp.status_code, headers=headers)
    except Exception as e:
        duration = int((time.time() - t0) * 1000)
        post_log(subpath, "?", "auto", "?", 0, 0, duration, "error", str(e)[:200])
        return Response(f"proxy error: {e}", status=502)

if __name__ == "__main__":
    router.run(host="0.0.0.0", port=PUBLIC_PORT, threaded=True)

