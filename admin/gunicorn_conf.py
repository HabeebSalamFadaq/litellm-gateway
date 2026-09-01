"""
Gunicorn config: blocks worker boot until LiteLLM is healthy.

Railway injects $PORT (default 8080) as the public-facing port.
The router listens on that port and reverse-proxies to:
  - LiteLLM proxy on $LITELLM_PORT (default 4002)
  - Admin API on $ADMIN_PORT (default 4001)
"""
import os
import subprocess
import time
import requests

bind = f"0.0.0.0:{os.environ.get('PORT', '4000')}"
workers = 1
worker_class = "gthread"
threads = 16
timeout = 600
graceful_timeout = 30
accesslog = "-"

# Block server boot until LiteLLM is ready.
# Without this hook, workers come up immediately and a request hitting
# the router before LiteLLM is listening gets a 502 "Connection refused".
def on_starting(server):
    litellm_port = int(os.environ.get("LITELLM_PORT", "4002"))
    health_url = f"http://127.0.0.1:{litellm_port}/health/liveliness"
    deadline = time.time() + 60
    last_err = None
    while time.time() < deadline:
        try:
            r = requests.get(health_url, timeout=2)
            if r.status_code < 500:
                server.log.info(f"litellm ready on :{litellm_port} (status={r.status_code})")
                return
        except Exception as e:
            last_err = e
        time.sleep(0.5)
    # Diagnose: is the litellm process even running?
    try:
        out = subprocess.run(
            ["ps", "-ef"], capture_output=True, text=True, timeout=5
        ).stdout
        litellm_procs = [l for l in out.splitlines() if "litellm" in l.lower()]
        server.log.warning(
            f"litellm not ready after 60s. "
            f"last_err={last_err}. litellm procs={litellm_procs}"
        )
    except Exception as diag_err:
        server.log.warning(
            f"litellm not ready after 60s. last_err={last_err}. "
            f"diagnose error: {diag_err}"
        )
