"""
Gunicorn config: brief readiness check, then serve.

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

# Brief readiness check. The watchdog thread (in router.py) keeps
# LiteLLM alive in the background, so we don't need to block gunicorn
# here. If LiteLLM is already up, we log and proceed; if not, we log
# and let the watchdog handle it once it can. Either way gunicorn
# binds to PORT within seconds so Railway's healthcheck sees a
# responsive service.
def on_starting(server):
    litellm_port = int(os.environ.get("LITELLM_PORT", "4002"))
    health_url = f"http://127.0.0.1:{litellm_port}/health/liveliness"
    deadline = time.time() + 15  # short - watchdog handles the long case
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
            f"litellm not ready after 15s. "
            f"last_err={last_err}. litellm procs={litellm_procs}. "
            f"Watchdog will restart it."
        )
    except Exception as diag_err:
        server.log.warning(
            f"litellm not ready after 15s. last_err={last_err}. "
            f"diagnose error: {diag_err}"
        )
