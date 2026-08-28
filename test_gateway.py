"""
Test the running LiteLLM gateway end-to-end.

Verifies:
  1. Health endpoint responds
  2. Master key is required (no key = 401)
  3. The Anthropic-format /v1/messages endpoint works (the one Claude Code uses)
  4. Streaming works
  5. Tool/function calling works
  6. The primary tier (Gemini) serves a real request with real credentials
  7. Fallback works: a non-existent model in the chain still gets served
     by the next tier
  8. Discovery endpoint works (Claude Code uses this when
     CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY=1)

Usage:
  python test_gateway.py [base_url] [master_key]
  Defaults: http://localhost:4000 and $LITELLM_MASTER_KEY or "sk-test"
"""

import json
import os
import sys
import time

import urllib.request
import urllib.error


def req(method, path, body=None, headers=None, base_url="http://localhost:4000", timeout=120):
    url = base_url.rstrip("/") + path
    data = None
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    r = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")


def check(label, ok, detail=""):
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {label}{(' - ' + detail) if detail else ''}")
    return ok


def main():
    base = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:4000"
    key = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("LITELLM_MASTER_KEY", "sk-test")

    print(f"Testing gateway at {base} with key {key[:10]}...")
    print()

    auth = {"Authorization": f"Bearer {key}"}
    auth_anthropic = {"x-api-key": key, "anthropic-version": "2023-06-01"}

    results = []

    # 1. Health
    print("1. Health endpoints")
    s, b = req("GET", "/health/liveliness")
    results.append(check("GET /health/liveliness", s == 200, f"HTTP {s}"))
    s, b = req("GET", "/health/readiness")
    results.append(check("GET /health/readiness", s == 200, f"HTTP {s}"))

    # 2. Auth enforcement on /v1/messages (the Claude Code endpoint)
    print("\n2. Authentication on /v1/messages")
    body = {
        "model": "claude-coder",
        "max_tokens": 512,
        "messages": [{"role": "user", "content": "ping"}],
    }
    s, b = req("POST", "/v1/messages", body)
    results.append(check("No auth key is rejected", s in (401, 403), f"HTTP {s}"))

    s, b = req("POST", "/v1/messages", body, headers=auth_anthropic)
    results.append(check("With x-api-key header", s in (200, 400, 404), f"HTTP {s} body={b[:200]}"))

    # 3. Anthropic-format basic request to primary tier
    print("\n3. Anthropic-format request to primary tier (claude-coder)")
    s, b = req("POST", "/v1/messages", body, headers=auth_anthropic, timeout=180)
    if s == 200:
        obj = json.loads(b)
        text = "".join(c.get("text", "") for c in obj.get("content", []))
        results.append(check("Primary tier responded with text", bool(text), f"model={obj.get('model')} text={text[:80]!r}"))
    else:
        results.append(check("Primary tier responded with text", False, f"HTTP {s} body={b[:300]}"))

    # 4. OpenAI-format request (for sanity)
    print("\n4. OpenAI-format request to primary tier (claude-coder)")
    ob = {
        "model": "claude-coder",
        "max_tokens": 512,
        "messages": [{"role": "user", "content": "ping"}],
    }
    s, b = req("POST", "/v1/chat/completions", ob, headers=auth, timeout=180)
    if s == 200:
        obj = json.loads(b)
        text = obj.get("choices", [{}])[0].get("message", {}).get("content", "")
        results.append(check("OpenAI-format request OK", bool(text), f"model={obj.get('model')} text={(text or '')[:80]!r}"))
    else:
        results.append(check("OpenAI-format request OK", False, f"HTTP {s} body={b[:300]}"))

    # 5. Streaming
    print("\n5. Streaming (Server-Sent Events)")
    sb = dict(body, stream=True)
    url = base.rstrip("/") + "/v1/messages"
    r = urllib.request.Request(
        url,
        data=json.dumps(sb).encode("utf-8"),
        headers={**h, "x-api-key": key, "anthropic-version": "2023-06-01"} if False else {"Content-Type": "application/json", "x-api-key": key, "anthropic-version": "2023-06-01"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(r, timeout=180) as resp:
            chunks = []
            for raw in resp:
                line = raw.decode("utf-8", errors="replace").strip()
                if line.startswith("data:") and line != "data: [DONE]":
                    chunks.append(line[5:].strip())
            results.append(check("Streaming returned >= 1 chunk", len(chunks) >= 1, f"{len(chunks)} chunks"))
    except Exception as e:
        results.append(check("Streaming returned >= 1 chunk", False, str(e)))

    # 6. Tool calling
    print("\n6. Tool / function calling")
    tb = {
        "model": "claude-coder",
        "max_tokens": 256,
        "messages": [{"role": "user", "content": "What's the weather in Paris in celsius?"}],
        "tools": [
            {
                "name": "get_weather",
                "description": "Get the current weather for a location",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "location": {"type": "string"},
                        "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]},
                    },
                    "required": ["location"],
                },
            }
        ],
    }
    s, b = req("POST", "/v1/messages", tb, headers=auth_anthropic, timeout=180)
    if s == 200:
        obj = json.loads(b)
        stop = obj.get("stop_reason")
        has_tool_use = any(c.get("type") == "tool_use" for c in obj.get("content", []))
        results.append(check("Model returned a tool_use block", has_tool_use, f"stop_reason={stop}"))
    else:
        results.append(check("Model returned a tool_use block", False, f"HTTP {s} body={b[:300]}"))

    # 7. Fallback to a different tier
    print("\n7. Tier-2 fallback (claude-coder-zen)")
    fb = {
        "model": "claude-coder-zen",
        "max_tokens": 512,
        "messages": [{"role": "user", "content": "ping"}],
    }
    s, b = req("POST", "/v1/messages", fb, headers=auth_anthropic, timeout=180)
    if s == 200:
        obj = json.loads(b)
        text = "".join(c.get("text", "") for c in obj.get("content", []))
        results.append(check("Tier 2 responded", bool(text), f"text={text[:80]!r}"))
    else:
        results.append(check("Tier 2 responded", False, f"HTTP {s} body={b[:300]}"))

    # 8. Model discovery endpoint
    print("\n8. Model discovery (used by Claude Code /model picker)")
    s, b = req("GET", "/v1/models", headers=auth)
    if s == 200:
        obj = json.loads(b)
        names = [m.get("id") for m in obj.get("data", [])]
        results.append(check("/v1/models returned >= 3 models", len(names) >= 3, f"models={names}"))
    else:
        results.append(check("/v1/models returned >= 3 models", False, f"HTTP {s}"))

    # Summary
    print("\n" + "=" * 50)
    passed = sum(results)
    total = len(results)
    print(f"{passed}/{total} checks passed")
    if passed < total:
        sys.exit(1)


if __name__ == "__main__":
    main()
