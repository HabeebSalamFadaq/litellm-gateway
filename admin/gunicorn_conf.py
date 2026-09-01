"""
Gunicorn config: blocks worker boot until LiteLLM is healthy.

Railway injects $PORT (default 8080) as the public-facing port.
The router listens on that port and reverse-proxies to:
  - LiteLLM proxy on $LITELLM_PORT (default 4002)
  - Admin API on $ADMIN_PORT (default 4001)
"""
import os
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
    deadline = time.time() + 90
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
    server.log.warning(f"litellm not ready after 90s (last_err={last_err}); serving anyway")
