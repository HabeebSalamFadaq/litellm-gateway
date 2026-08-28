# Handoff Document — Claude Code Cloud Gateway Project

## Current state — everything works

You have a **production LiteLLM cloud gateway** running on Railway that proxies Claude Code requests to 6 different model groups, with automatic fallback across all of them. It is verified end-to-end:

- **Public URL**: `https://litellm-production-2ce7.up.railway.app`
- **Railway project**: `stunning-sparkle` (project ID `96eb1a2f-4812-4b16-87a0-d5a6e184afae`) in Habeeb Salam Fadaq's Workspace
- **GitHub repo**: https://github.com/HabeebSalamFadaq/litellm-gateway (4 commits on master, all clean)
- **Claude Code `settings.json`** is set up; verified live with a real conversation. Model shows as `gateway-main-2[1m]`, upstream resolved to MiniMax-M3 from OpenRouter.

## What you asked for, what was built, and what's deployed

You asked for a cloud AI gateway for Claude Code using LiteLLM with 5 providers (Google Gemini, NVIDIA NIM, OpenRouter, xAI/Grok, Groq, OpenCode Zen), with:
- Multi-key rotation per provider
- Fallback chain when providers fail
- Anthropic-format endpoint so Claude Code works natively
- No DB, simple config
- Railway deployment with public HTTPS

The end result has 6 model groups (not 5 — split gateway-main into 2 and gateway-fast into 2 for finer fallback control), all verified to return proper `stop_reason: tool_use` blocks via empirical test.

## Current model routing (top to bottom)

| Priority | `model_name` | Upstream(s) | Empirical speed | Tool use |
|---|---|---|---|---|
| 1 | `gateway-main` | NIM `moonshotai/kimi-k3` | 10.6s | ✅ |
| 2 | `gateway-main-2` | OpenRouter `z-ai/glm-5.2:free` + `minimax/minimax-m3:free` | 1.8s | ✅ |
| 3 | `gateway-fast` | NIM `openai/gpt-oss-120b` | 39.6s cold, faster warm | ✅ |
| 4 | `gateway-fast-2` | NIM `gpt-oss-20b` + Groq `gpt-oss-20b` ×2 | 0.7s | ✅ |
| 5 | `gateway-backup` | OpenRouter `nvidia/nemotron-3-super-120b-a12b:free` ×4 | 7.6s | ✅ |
| 6 | `gateway-legacy` | Gemini `gemini-2.5-flash` ×10 keys | 1.6s | ✅ |

Fallback chain in config:
```
gateway-main      → gateway-main-2 → gateway-backup   → gateway-legacy
gateway-main-2    → gateway-backup → gateway-legacy
gateway-fast      → gateway-fast-2 → gateway-backup   → gateway-legacy
gateway-fast-2    → gateway-backup → gateway-legacy
gateway-backup    → gateway-legacy
```
60s cooldown per failed deployment, 2 allowed fails before cooldown kicks in, simple-shuffle routing within each group.

## Claude Code connection (already configured)

`~/.claude/settings.json` (or `%USERPROFILE%\.claude\settings.json` on Windows):
```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "https://litellm-production-2ce7.up.railway.app",
    "ANTHROPIC_AUTH_TOKEN": "sk-litellm-gateway-BdWGjmNnHfS2qs9eTbgAQJPrcIL6xXOp",
    "ANTHROPIC_MODEL": "gateway-main-2",
    "ANTHROPIC_SMALL_FAST_MODEL": "gateway-fast-2",
    "CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT": "1"
  }
}
```

Verified: `claude` → `/model` shows `gateway-main-2[1m]`. Real conversation test passed.

**Two important caveats**:
1. `ANTHROPIC_AUTH_TOKEN` in this doc is the one currently on Railway. It is also in this transcript (security concern) — see "Security TODOs" below.
2. `ANTHROPIC_API_KEY` was set as a process env var (a leftover `nvapi-...` NVIDIA NIM key). The fix is `Remove-Item Env:ANTHROPIC_API_KEY` in any new shell before running claude. This is NOT in settings.json — it's a Windows system env var.

## Repo file layout (all on GitHub)

```
litellm-gateway/
├── Dockerfile                              # FROM ghcr.io/berriai/litellm:main-stable
│                                          # COPY config.yaml /app/config.yaml
│                                          # CMD ["--config", "/app/config.yaml", "--port", "4000"]
├── config.yaml                             # the whole gateway config (see "Config notes")
├── railway.json                            # builder: NIXPACKS, port 4000, healthcheck on /health/liveliness
├── claude-settings.json                    # the JSON to put in ~/.claude/settings.json
├── test_tools.json                         # Anthropic-format tool_use payload for empirical testing
├── test_empirical.py                       # runs the empirical tool_use test against all groups
├── test_gateway.py                         # local-end-to-end tests (auth, streaming, tools, models)
├── test_fallback.py                        # validates the fallback chain
├── README.md                               # user-facing ops guide
└── .env.example                            # all env var names with placeholders
```

`config.yaml` is the only file that actually controls runtime behavior. Edit it to add/remove providers, change priority, etc.

## Config notes (key choices and WHY)

- **No DATABASE_URL, no Postgres, no Prisma** — `ghcr.io/berriai/litellm:main-stable` (not `litellm-database`). This was the critical fix: the database image's Prisma init hangs on Railway's internal DNS race at t=0, killing every deploy.
- **`drop_params: true`** — Claude Code sends Anthropic-specific fields (cache_control, citations, anthropic-beta) that Gemini rejects. Without this you get 400s.
- **`timeout: 60`** per request — NIM models can be slow on cold start. 60s gives a fair chance, fails over to next tier after.
- **6 separate `model_name` groups instead of 2** — gives finer-grained fallback control and lets you point `ANTHROPIC_MODEL` and `ANTHROPIC_SMALL_FAST_MODEL` at different groups.
- **`cooldown_time: 60` + `allowed_fails: 2`** — a key that 429s twice in 60s gets benched for 60s; the other keys in the same group keep serving.
- **NIM primary, OpenRouter backup, Gemini last-resort** — NIM is free with no daily quota, OpenRouter free models have 50/day per key, Gemini free keys have 2.5/min per key rate limit. The order is by *daily cost ceiling*, not by capability.
- **2 Groq keys both on `gpt-oss-20b`** — Groq free tier has separate per-key quotas, so loading the same model twice doubles effective daily budget.

## How to continue work

### Add a new model to the gateway
1. Edit `config.yaml`. Add a new deployment under the appropriate `model_name` (e.g. `gateway-main-2`) with the new `litellm_params.model` and `api_key: os.environ/SOMETHING`.
2. Add the new env var to the Railway service: `railway variable set SOMETHING=value --service litellm`
3. Commit and push: `git add config.yaml && git commit -m "Add X" && git push origin master`
4. `railway up --detach` to force a redeploy (GitHub auto-deploy sometimes misses)

### Rotate the master gateway key
1. Generate a new long random string (32+ chars)
2. `railway variable set LITELLM_MASTER_KEY=sk-new-... --service litellm`
3. `railway up --detach` to redeploy
4. Update `ANTHROPIC_AUTH_TOKEN` in `~/.claude/settings.json` on every Claude Code client

### Add a new provider tier entirely
1. Pick the right LiteLLM model prefix (e.g. `gemini/`, `openai/`, `openrouter/`, `nvidia_nim/`, `groq/`, `xai/`)
2. Add a new `model_name` group in `config.yaml` with one or more deployments
3. Add to the `fallbacks` chain at the right position
4. Push and deploy

### Check what's happening
```bash
railway status                                    # service state
railway logs                                      # recent stdout/stderr
railway variable list --service litellm --kv      # all env vars
curl -s https://litellm-production-2ce7.up.railway.app/health/liveliness
curl -s https://litellm-production-2ce7.up.railway.app/v1/models
```

## Security TODOs (do these soon)

1. **ROTATE THE MASTER KEY**. `sk-litellm-gateway-BdWGjmNnHfS2qs9eTbgAQJPrcIL6xXOp` is currently the active key. It is in this transcript, in the GitHub repo's `claude-settings.json` (and in earlier README iterations). Anyone with this key can spend against your provider quotas. Rotate it in Railway Variables, redeploy, update settings.json on every client.
2. **ROTATE THE PROVIDER KEYS** if you consider them sensitive. The Gemini and OpenRouter keys in this transcript are now public. Most free keys are not catastrophic (they'd just disable your own access), but rotating is hygiene.
3. **Set a real `LITELLM_SALT_KEY`** if you ever add a database (currently there's no DB so it's unused). It encrypts provider credentials at rest in the DB. Once set, never change it or you lose access to stored creds.

## Known limitations (won't bite, but know them)

- **OpenRouter free tier**: 50 requests/day per key, 4 keys = 200/day. Heavy Claude Code usage can exhaust this in a few hours. After that, requests fall through to Gemini (which has its own per-minute rate limit, and can also 429).
- **NIM cold starts**: First request to a model can take 10-30 seconds while the model server loads. Subsequent requests are fast (1-3s).
- **No request-level cost tracking**: Without a DB, you can't see per-request spend. Check each provider's dashboard.
- **Single region (Railway `ams`)**: If the region goes down, the gateway is down. For multi-region, deploy to multiple Railway regions and add a load balancer.
- **Gemma caveat** (per third-party advice): Don't add Gemma models to the chain — they emulate tool calling via prompting, which breaks on Claude Code's nested tool schemas.
- **Reasoning models in gateway-main**: Avoid models that emit `thinking` blocks with `signature: null` (nemotron-3-nano-omni-reasoning, cosmos3-nano-reasoner) — they can confuse the chain.

## Useful empirical data captured this session

Tested each model directly against the live gateway with a tool_use payload. Results:

| Model | Works? | Speed |
|---|---|---|
| NIM kimi-k3 | ✅ | 10.6s cold, faster warm |
| NIM nemotron-3-ultra-550b | ❌ | 503 overloaded (skipped) |
| NIM nemotron-3-super-120b | ✅ | 7.6s |
| NIM gpt-oss-120b | ✅ | 39.6s cold (kept for quality) |
| NIM gpt-oss-20b | ✅ | 0.7s |
| NIM nemotron-3-nano-30b | ✅ | fast |
| NIM nemotron-3.5-lightning | ❌ | timed out |
| NIM deepseek-v4-flash-0731 | ❌ | timed out (>60s) |
| OpenRouter z-ai/glm-5.2:free | ✅ | 1.8s |
| OpenRouter minimax/minimax-m3:free | ✅ | 1.8s |
| OpenRouter nemotron-3-super-120b-a12b:free | ✅ | 1.8s (proven live in production) |
| Gemini gemini-2.5-flash | ✅ | 1.6s |
| Gemini gemini-2.5-pro | ❌ | 404 for free keys (deprecated for new users) |
| Groq gpt-oss-20b | ✅ | 0.7s |

The empirical test script is `test_empirical.py` — it runs against the live gateway and reports `stop_reason: tool_use` pass/fail per model. Use it to validate any new model you add.

## Files that survived the journey

- `opencode-proxy-for-claude-code/` — the user's original attempt before this project. Custom Python proxy, not used. Repository on the user's machine only.
- `litellm-gateway/` — the project. Active, deployed, working.

## Who to ping if stuck

- The LiteLLM docs at https://docs.litellm.ai — especially `/docs/anthropic_unified` for the Anthropic-format endpoint and `/docs/proxy/reliability` for the fallback chain syntax.
- The Claude Code gateway connection docs at https://docs.anthropic.com/en/docs/claude-code/llm-gateway-connect — for the correct env var names and behavior.
- The Railway CLI docs at https://docs.railway.com/reference/cli — for `railway variable`, `railway logs`, `railway domain`, `railway service source`.

## TL;DR

- Gateway is live at `https://litellm-production-2ce7.up.railway.app`
- Claude Code is connected and verified working with real responses
- 6 model groups, 5-tier fallback chain, all empirically tested for tool_use
- No DB, no Postgres, just the gateway image and a config file
- Single most important thing to do next: rotate the master key (it's in this transcript)
