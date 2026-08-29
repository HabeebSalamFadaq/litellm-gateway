"""
LiteLLM Gateway Admin API

Runs alongside the LiteLLM proxy. Reads the same config.yaml and
exposes admin endpoints for the Vercel dashboard to:
- View real-time request metrics (model used, key used, tokens)
- View model group health and cooldowns
- Change model priority
- Enable/disable/test API keys
- Show per-key quota usage

Lightweight: Flask + sqlite. No external dependencies beyond LiteLLM.
"""
import os
import json
import time
import sqlite3
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path

from flask import Flask, request, jsonify, abort
from flask_cors import CORS
import yaml

# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------
CONFIG_PATH = os.environ.get("LITELLM_CONFIG_PATH", "/app/config.yaml")
ADMIN_PORT = int(os.environ.get("ADMIN_PORT", "4001"))
ADMIN_KEY = os.environ.get("LITELLM_MASTER_KEY", "")
DB_PATH = os.environ.get("ADMIN_DB_PATH", "/tmp/litellm_admin.db")
RETENTION_MINUTES = int(os.environ.get("ADMIN_RETENTION_MINUTES", "60"))

app = Flask(__name__)
CORS(app, origins="*", supports_credentials=True, allow_headers=["*"])

# ----------------------------------------------------------------------------
# DB
# ----------------------------------------------------------------------------
_db_lock = threading.Lock()

def db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with db() as conn, _db_lock:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts INTEGER NOT NULL,
            model_group TEXT,
            upstream_model TEXT,
            api_key_label TEXT,
            provider TEXT,
            input_tokens INTEGER,
            output_tokens INTEGER,
            total_tokens INTEGER,
            duration_ms INTEGER,
            status TEXT,
            error TEXT
        );
        CREATE INDEX IF NOT EXISTS requests_ts ON requests(ts);
        CREATE INDEX IF NOT EXISTS requests_model ON requests(model_group);
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts INTEGER NOT NULL,
            kind TEXT NOT NULL,
            payload TEXT NOT NULL
        );
        """)

def log_request(model_group, upstream_model, api_key_label, provider,
                input_tokens, output_tokens, duration_ms, status, error):
    try:
        with db() as conn, _db_lock:
            conn.execute(
                """INSERT INTO requests
                (ts, model_group, upstream_model, api_key_label, provider,
                 input_tokens, output_tokens, total_tokens, duration_ms, status, error)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (int(time.time()), model_group, upstream_model, api_key_label,
                 provider, input_tokens or 0, output_tokens or 0,
                 (input_tokens or 0) + (output_tokens or 0),
                 duration_ms, status, error or ""))
    except Exception:
        pass

def log_event(kind, payload):
    try:
        with db() as conn, _db_lock:
            conn.execute(
                "INSERT INTO events (ts, kind, payload) VALUES (?, ?, ?)",
                (int(time.time()), kind, json.dumps(payload))
            )
    except Exception:
        pass

def cleanup_old():
    cutoff = int(time.time()) - RETENTION_MINUTES * 60
    with db() as conn, _db_lock:
        conn.execute("DELETE FROM requests WHERE ts < ?", (cutoff,))

# ----------------------------------------------------------------------------
# Config loading
# ----------------------------------------------------------------------------
def load_config():
    try:
        with open(CONFIG_PATH, "r") as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        return {"_error": str(e)}

def save_config(cfg):
    """Atomic write so LiteLLM never sees a half-written file."""
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w") as f:
        yaml.safe_dump(cfg, f, sort_keys=False, default_flow_style=False)
    os.replace(tmp, CONFIG_PATH)

# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
def auth():
    key = request.headers.get("x-api-key") or request.headers.get("Authorization", "").replace("Bearer ", "")
    if not ADMIN_KEY or key != ADMIN_KEY:
        abort(401, "unauthorized")

def now_iso():
    return datetime.now(timezone.utc).isoformat()

# ----------------------------------------------------------------------------
# Routes
# ----------------------------------------------------------------------------
@app.route("/health")
def health():
    return jsonify({"status": "ok", "ts": now_iso()})

@app.route("/admin/config")
def get_config():
    auth()
    cfg = load_config()
    # Strip the master_key from response
    if "general_settings" in cfg and "master_key" in cfg["general_settings"]:
        cfg["general_settings"]["master_key"] = "***"
    return jsonify(cfg)

@app.route("/admin/models")
def get_models():
    """Flat list of model_name + provider + model + key_index."""
    auth()
    cfg = load_config()
    out = []
    for entry in cfg.get("model_list", []):
        mn = entry.get("model_name")
        lp = entry.get("litellm_params", {})
        model = lp.get("model", "")
        ak = lp.get("api_key", "")
        # Extract provider from model prefix (e.g. "gemini/..." -> "gemini")
        provider = model.split("/", 1)[0] if "/" in model else "openai"
        # Extract key label from "os.environ/FOO"
        key_label = ak.replace("os.environ/", "") if ak.startswith("os.environ/") else ak
        out.append({
            "model_name": mn,
            "model": model,
            "provider": provider,
            "key_label": key_label,
            "model_info": entry.get("model_info", {}),
        })
    return jsonify({"models": out, "router": cfg.get("router_settings", {}),
                    "litellm": cfg.get("litellm_settings", {}),
                    "fallbacks": cfg.get("litellm_settings", {}).get("fallbacks", [])})

@app.route("/admin/priority", methods=["POST"])
def set_priority():
    """Set the priority chain for a model_name. Body: {model_name, order: [key1, key2, ...]}."""
    auth()
    cfg = load_config()
    body = request.get_json(force=True)
    mn = body.get("model_name")
    order = body.get("order", [])
    if not mn or not isinstance(order, list):
        abort(400, "model_name and order[] required")
    deployments = [d for d in cfg.get("model_list", []) if d.get("model_name") == mn]
    if not deployments:
        abort(404, f"no deployments for {mn}")
    # Build a map of key_label -> deployment
    by_key = {}
    for d in deployments:
        ak = d.get("litellm_params", {}).get("api_key", "")
        key_label = ak.replace("os.environ/", "") if ak.startswith("os.environ/") else ak
        by_key[key_label] = d
    # Reorder: emit deployments in the new order
    new_deps = []
    for k in order:
        if k in by_key:
            new_deps.append(by_key.pop(k))
    # Append any keys not mentioned (preserve them at the end)
    new_deps.extend(by_key.values())
    # Replace in cfg
    new_list = [d for d in cfg["model_list"] if d.get("model_name") != mn] + new_deps
    cfg["model_list"] = new_list
    save_config(cfg)
    log_event("priority_change", {"model_name": mn, "new_order": order})
    return jsonify({"ok": True, "model_name": mn, "new_order": order})

@app.route("/admin/key", methods=["POST"])
def toggle_key():
    """Enable/disable a key for a model_name. Body: {model_name, key_label, enabled}."""
    auth()
    cfg = load_config()
    body = request.get_json(force=True)
    mn = body.get("model_name")
    key_label = body.get("key_label")
    enabled = body.get("enabled", True)
    changed = 0
    for d in cfg.get("model_list", []):
        if d.get("model_name") != mn:
            continue
        ak = d.get("litellm_params", {}).get("api_key", "")
        cur_label = ak.replace("os.environ/", "") if ak.startswith("os.environ/") else ak
        if cur_label == key_label:
            d.setdefault("model_info", {})["enabled"] = enabled
            changed += 1
    if changed == 0:
        abort(404, f"no key {key_label} for {mn}")
    save_config(cfg)
    log_event("key_toggle", {"model_name": mn, "key_label": key_label, "enabled": enabled})
    return jsonify({"ok": True, "model_name": mn, "key_label": key_label,
                    "enabled": enabled, "changed": changed})

@app.route("/admin/usage")
def get_usage():
    """Recent request log + per-model-group + per-key rollup."""
    auth()
    cleanup_old()
    minutes = int(request.args.get("minutes", 60))
    cutoff = int(time.time()) - minutes * 60
    with db() as conn:
        rows = conn.execute(
            """SELECT * FROM requests WHERE ts >= ? ORDER BY ts DESC LIMIT 500""",
            (cutoff,)).fetchall()
        requests = [dict(r) for r in rows]
        # Per-group rollup
        group_stats = {}
        for r in requests:
            g = r["model_group"] or "unknown"
            k = r["api_key_label"] or "unknown"
            group_stats.setdefault(g, {"model_group": g, "total_requests": 0,
                                       "success": 0, "error": 0,
                                       "input_tokens": 0, "output_tokens": 0,
                                       "keys": {}})
            group_stats[g]["total_requests"] += 1
            if r["status"] == "ok":
                group_stats[g]["success"] += 1
            else:
                group_stats[g]["error"] += 1
            group_stats[g]["input_tokens"] += r["input_tokens"] or 0
            group_stats[g]["output_tokens"] += r["output_tokens"] or 0
            group_stats[g]["keys"].setdefault(k, {"requests": 0,
                                                    "input_tokens": 0,
                                                    "output_tokens": 0})
            group_stats[g]["keys"][k]["requests"] += 1
            group_stats[g]["keys"][k]["input_tokens"] += r["input_tokens"] or 0
            group_stats[g]["keys"][k]["output_tokens"] += r["output_tokens"] or 0
    return jsonify({
        "requests": requests,
        "groups": list(group_stats.values()),
        "window_minutes": minutes,
    })

@app.route("/admin/keys/test", methods=["POST"])
def test_key():
    """Test a provider API key by calling a cheap endpoint. Body: {provider, api_key}."""
    auth()
    import urllib.request
    import urllib.error
    body = request.get_json(force=True)
    provider = body.get("provider")
    key = body.get("api_key")
    if not provider or not key:
        abort(400, "provider and api_key required")
    t0 = time.time()
    try:
        if provider == "gemini":
            url = f"https://generativelanguage.googleapis.com/v1beta/models?key={key}"
        elif provider == "openrouter":
            url = "https://openrouter.ai/api/v1/auth/key"
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"})
        elif provider == "groq":
            url = "https://api.groq.com/openai/v1/models"
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"})
        elif provider == "opencode":
            url = "https://opencode.ai/zen/v1/models"
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"})
        elif provider == "nvidia_nim":
            url = "https://integrate.api.nvidia.com/v1/models"
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"})
        else:
            return jsonify({"ok": False, "error": f"unsupported provider: {provider}"})
        with urllib.request.urlopen(req, timeout=10) as r:
            elapsed = int((time.time() - t0) * 1000)
            return jsonify({"ok": True, "provider": provider,
                            "status": r.status, "latency_ms": elapsed})
    except urllib.error.HTTPError as e:
        elapsed = int((time.time() - t0) * 1000)
        return jsonify({"ok": False, "provider": provider,
                        "status": e.code, "latency_ms": elapsed,
                        "error": e.read().decode("utf-8", errors="replace")[:300]})
    except Exception as e:
        elapsed = int((time.time() - t0) * 1000)
        return jsonify({"ok": False, "provider": provider,
                        "latency_ms": elapsed, "error": str(e)})

@app.route("/admin/keys")
def get_keys():
    """List all configured API keys with their status, usage, and priority."""
    auth()
    cfg = load_config()
    cleanup_old()
    minutes = int(request.args.get("minutes", 60))
    cutoff = int(time.time()) - minutes * 60
    
    # Get usage stats from DB
    with db() as conn:
        rows = conn.execute(
            """SELECT * FROM requests WHERE ts >= ? ORDER BY ts DESC LIMIT 500""",
            (cutoff,)).fetchall()
        requests = [dict(r) for r in rows]
    
    # Build key usage map
    key_usage = {}
    for r in requests:
        key = r["api_key_label"] or "unknown"
        key_usage.setdefault(key, {"requests": 0, "errors": 0, "input_tokens": 0, "output_tokens": 0, "last_used": None})
        key_usage[key]["requests"] += 1
        if r["status"] != "ok":
            key_usage[key]["errors"] += 1
        key_usage[key]["input_tokens"] += r["input_tokens"] or 0
        key_usage[key]["output_tokens"] += r["output_tokens"] or 0
        ts = r["ts"]
        if key_usage[key]["last_used"] is None or ts > key_usage[key]["last_used"]:
            key_usage[key]["last_used"] = ts
    
    # Build key list from config
    out = []
    for entry in cfg.get("model_list", []):
        lp = entry.get("litellm_params", {})
        ak = lp.get("api_key", "")
        if not ak or not ak.startswith("os.environ/"):
            continue
        key_label = ak.replace("os.environ/", "")
        model_name = entry.get("model_name", "")
        provider = (lp.get("model", "").split("/", 1)[0] if "/" in lp.get("model", "") else "openai")
        priority = entry.get("model_info", {}).get("rank", 0)
        enabled = entry.get("model_info", {}).get("enabled", True)
        mi = entry.get("model_info", {})
        quota_limit = mi.get("quota_limit")
        quota_remaining = mi.get("quota_remaining")
        
        # Merge with usage
        usage = key_usage.get(key_label, {"requests": 0, "errors": 0, "input_tokens": 0, "output_tokens": 0, "last_used": None})
        
        # Determine status
        if not enabled:
            status = "error"
        elif quota_limit and quota_remaining is not None and quota_remaining <= 0:
            status = "exhausted"
        elif usage["errors"] > usage["requests"] * 0.5 and usage["requests"] > 10:
            status = "rate_limited"
        elif quota_limit and quota_remaining is not None and quota_remaining < quota_limit * 0.1:
            status = "low"
        elif usage["requests"] == 0:
            status = "unused"
        else:
            status = "healthy"
        
        out.append({
            "key_id": key_label[:8] + "..." + key_label[-4:] if len(key_label) > 12 else key_label,
            "provider": provider,
            "label": key_label,
            "status": status,
            "usage_pct": (100 - round(quota_remaining * 100 / quota_limit)) if quota_limit and quota_remaining is not None else None,
            "requests_24h": usage["requests"],
            "errors_24h": usage["errors"],
            "last_used": datetime.fromtimestamp(usage["last_used"], tz=timezone.utc).isoformat() if usage["last_used"] else None,
            "quota_limit": quota_limit,
            "quota_remaining": quota_remaining,
            "priority": priority,
            "enabled": enabled,
        })
    
    return jsonify({"keys": out})

@app.route("/admin/log", methods=["POST"])
def ingest_log():
    """Called by the LiteLLM callback to record a request. Body: {model_group, upstream_model, api_key_label, provider, input_tokens, output_tokens, duration_ms, status, error}."""
    body = request.get_json(force=True)
    log_request(
        model_group=body.get("model_group"),
        upstream_model=body.get("upstream_model"),
        api_key_label=body.get("api_key_label"),
        provider=body.get("provider"),
        input_tokens=body.get("input_tokens"),
        output_tokens=body.get("output_tokens"),
        duration_ms=body.get("duration_ms"),
        status=body.get("status"),
        error=body.get("error"),
    )
    return jsonify({"ok": True})

# ----------------------------------------------------------------------------
# Entrypoint
# ----------------------------------------------------------------------------
if __name__ == "__main__":
    init_db()
    cleanup_old()
    app.run(host="0.0.0.0", port=ADMIN_PORT, threaded=True)
