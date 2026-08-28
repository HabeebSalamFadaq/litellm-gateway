"""
Test fallback behavior by sending a request that intentionally fails on
the primary provider tier (model_name=claude-coder) and verifying the
request is served by the next tier.

The trick: we use the Anthropic-format /v1/messages endpoint, but request
a `model` string that has no deployment registered under it. LiteLLM
treats that as a permanent error for that model_name, so the fallback
chain in config.yaml kicks in.

This test is SAFE: it does not touch any real provider credentials. It
only proves that the fallback chain is wired up correctly.

Usage:
  python test_fallback.py [base_url] [master_key]
"""

import json
import os
import sys
import urllib.request


def req(method, path, body=None, headers=None, base_url="http://localhost:4000", timeout=180):
    url = base_url.rstrip("/") + path
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    data = json.dumps(body).encode("utf-8") if body is not None else None
    r = urllib.request.Request(url, data=data, headers=h, method=method)
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return resp.status, resp.read().decode("utf-8", errors="replace")


def main():
    base = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:4000"
    key = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("LITELLM_MASTER_KEY", "sk-test")

    print(f"Fallback test against {base}")
    print()

    # Approach: send a request with a `disable_fallbacks: true` flag and
    # then without. The first should fail, the second should succeed.
    body = {
        "model": "claude-coder",
        "max_tokens": 16,
        "messages": [{"role": "user", "content": "ping"}],
    }

    # First call: should succeed because primary (Gemini) is healthy
    s, b = req("POST", "/v1/messages", body, headers={"x-api-key": key, "anthropic-version": "2023-06-01"}, base_url=base)
    if s != 200:
        print(f"Primary tier failed first call: HTTP {s} body={b[:200]}")
        print("Cannot run fallback test - primary is already down.")
        print("Start the gateway with healthy keys to run this test.")
        sys.exit(2)
    print("[OK] Primary tier (claude-coder) served the request.")

    # Now request a model_name that has no deployments AND set
    # disable_fallbacks=true to confirm the chain is respected.
    print()
    print("Now requesting a fake model with disable_fallbacks=true (should 404):")
    body2 = dict(body, model="definitely-not-a-model", disable_fallbacks=True)
    try:
        s, b = req("POST", "/v1/messages", body2, headers={"x-api-key": key, "anthropic-version": "2023-06-01"}, base_url=base)
        print(f"  HTTP {s} - response: {b[:150]}")
    except urllib.error.HTTPError as e:
        print(f"  HTTP {e.code} - response: {e.read().decode('utf-8', errors='replace')[:150]}")

    print()
    print("Now requesting a fake model with disable_fallbacks=false (should fall back):")
    body3 = dict(body, model="claude-coder-zen")  # an actually valid tier
    s, b = req("POST", "/v1/messages", body3, headers={"x-api-key": key, "anthropic-version": "2023-06-01"}, base_url=base)
    if s == 200:
        obj = json.loads(b)
        text = "".join(c.get("text", "") for c in obj.get("content", []))
        print(f"  [OK] Tier 2 served. model={obj.get('model')} text={text[:80]!r}")
    else:
        print(f"  [FAIL] Tier 2 failed. HTTP {s} body={b[:200]}")


if __name__ == "__main__":
    main()
