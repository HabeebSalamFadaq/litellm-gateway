"""
Empirical tool-use test for each candidate model.
"""
import json
import os
import sys
import time
import urllib.request
import urllib.error

BASE = "https://litellm-production-2ce7.up.railway.app"
KEY = "sk-litellm-gateway-BdWGjmNnHfS2qs9eTbgAQJPrcIL6xXOp"
TEMPLATE_PATH = "test_tools.json"
TIMEOUT = 90

MODELS = [
    "t-kimi-k3",
    "t-glm-5.2",
    "t-nemotron-3-ultra",
    "t-minimax-m3",
    "t-deepseek-v4-flash",
    "t-nemotron-3-super",
    "t-gpt-oss-120b",
    "t-minimax-m2.7",
    "t-north-mini-code",
    "t-gpt-oss-20b",
    "t-nemotron-3-nano",
]


def load_template():
    with open(TEMPLATE_PATH, "r") as f:
        return f.read()


def post(path, body, timeout=TIMEOUT):
    url = BASE.rstrip("/") + path
    data = json.dumps(body).encode("utf-8")
    h = {"Content-Type": "application/json", "x-api-key": KEY, "anthropic-version": "2023-06-01"}
    r = urllib.request.Request(url, data=data, headers=h, method="POST")
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except Exception as e:
        return 0, str(e)


def main():
    template = load_template()
    results = []
    for m in MODELS:
        body_text = template.replace("MODELNAME", m)
        body = json.loads(body_text)
        t0 = time.time()
        s, b = post("/v1/messages", body)
        dt = round(time.time() - t0, 1)
        if s != 200:
            obj = json.loads(b) if b.startswith("{") else {"raw": b[:200]}
            err = obj.get("error", {}).get("message", b[:200])
            print(f"  FAIL  {m:30s}  HTTP={s}  {dt}s  {str(err)[:120]}")
            results.append((m, False, s, str(err)[:80], dt))
            continue
        obj = json.loads(b)
        stop = obj.get("stop_reason")
        upstream = obj.get("model")
        blocks = obj.get("content", [])
        ctypes = ",".join(c.get("type") for c in blocks)
        tool_block = next((c for c in blocks if c.get("type") == "tool_use"), None)
        tool_input = json.dumps(tool_block["input"]) if tool_block else None
        passed = (stop == "tool_use") and (tool_block is not None)
        mark = "PASS" if passed else "FAIL"
        print(f"  {mark}  {m:30s}  HTTP=200  {dt}s  stop={stop}  upstream={upstream}")
        if tool_input:
            print(f"        tool_input: {tool_input}")
        results.append((m, passed, s, stop, dt))

    print()
    passed = sum(1 for _, p, _, _, _ in results if p)
    print(f"=== {passed}/{len(results)} models return stop_reason=tool_use ===")
    print()
    print("Ranking by tool_use success:")
    for m, p, s, stop, dt in results:
        mark = "[X]" if p else "[ ]"
        print(f"  {mark}  {m:30s}  {dt}s  stop={stop}")


if __name__ == "__main__":
    main()
