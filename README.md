# LiteLLM Cloud Gateway for Claude Code

A self-hosted, free, always-on HTTPS endpoint that Claude Code talks to.
Routes requests through a multi-provider fallback chain so you're never
locked out by a single provider's rate limit. Runs entirely in the
cloud — your laptop can be off, asleep, or gone.

```
Claude Code
    ↓  ANTHROPIC_BASE_URL=https://<your-gateway>.up.railway.app
    ↓  ANTHROPIC_AUTH_TOKEN=<your-gateway-key>
    ↓  ANTHROPIC_MODEL=gateway-main
    ↓  ANTHROPIC_SMALL_FAST_MODEL=gateway-fast
HTTPS endpoint (Railway)
    ↓
LiteLLM proxy (Docker, no DB, config-file based)
    ↓
simple-shuffle load-balancing across 2 model groups
    ↓
┌─ gateway-main    All smart models: NIM kimi-k3, OpenRouter GLM/m3/nemotron, Gemini flash (Tier S)
└─ gateway-fast    All fast models: NIM gpt-oss-120b/20b, Groq gpt-oss-20b, nemotron-nano (Tier A/B)

Each group is a self-contained fallback chain: any single model failing
just routes to the next one in the same group. No cross-group fallbacks
to keep it simple.
```

## Why this works

- **Free**: All providers used have free tiers (Google AI Studio, NVIDIA NIM, OpenRouter free, Groq free, OpenCode Zen free). The most expensive model in production is OpenRouter's free tier, billed at $0.
- **Reliable**: If one provider rate-limits, the request automatically falls back to the next. Tested empirically with a real tool-use payload: 6/6 model groups return proper `tool_use` blocks.
- **24/7**: Runs on Railway's infrastructure in their `ams` (Amsterdam) region. Your laptop can be off.
- **Cheap fallback chain**: Puts NIM (unlimited) first, then OpenRouter (200/day), then Gemini (1M tokens/day across 10 keys). Worst case you run out of OpenRouter for the day and fall through to Gemini.
- **No database**: Uses `ghcr.io/berriai/litellm:main-stable` (not `litellm-database`). The DB image's Prisma init hangs on Railway's internal DNS race at startup. The plain image boots in seconds.

## Prerequisites

- A GitHub account
- A Railway account (free tier works)
- API keys from at least one of: Google AI Studio (free), NVIDIA NIM (free), OpenRouter (free), Groq (free), OpenCode Zen (free), KiraAI
- Claude Code installed locally
- Basic terminal/PowerShell familiarity

Total time: ~30 minutes for a first-time deploy.

## Step 1 — Get API keys

You need at least 2 providers (the gateway has a fallback chain). Recommended first setup:

| Provider | Where to get it | Free tier |
|---|---|---|
| Google AI Studio | https://aistudio.google.com | 6 keys, ~2 req/min each |
| NVIDIA NIM | https://build.nvidia.com | unlimited (rate limited) |
| OpenRouter | https://openrouter.ai/keys | 50 req/day per key |
| Groq | https://console.groq.com | free tier |
| OpenCode Zen | https://opencode.ai/auth | free tier |

**Verified working model names** (as of this writing — confirm before depending on them):

**NVIDIA NIM** (best for free unlimited, but slow first request):
- `moonshotai/kimi-k3`
- `nvidia/nemotron-3-super-120b-a12b`
- `nvidia/nemotron-3-nano-30b-a3b`
- `openai/gpt-oss-120b`
- `openai/gpt-oss-20b`

**OpenRouter free models** (200/day total across all your keys):
- `z-ai/glm-5.2:free`
- `minimax/minimax-m3:free`
- `nvidia/nemotron-3-super-120b-a12b:free`
- `cohere/north-mini-code:free`

**Groq**:
- `openai/gpt-oss-20b`

**Google AI Studio free keys**:
- `gemini-2.5-flash` (works on free keys)
- `gemini-2.5-pro` (404 on free keys — doesn't work)
- `gemini-3.5-flash` (works on most new keys)

## Step 2 — Fork the repo

Go to https://github.com/HabeebSalamFadaq/litellm-gateway and click **Fork**. Name it whatever you want. This is your copy that Railway will deploy from.

If you want to start from scratch instead of forking, the files you need are:
- `Dockerfile` (just builds the LiteLLM image)
- `config.yaml` (the actual routing rules)
- `railway.json` (Railway service config)

Everything else (`test_*.py`, `handoff_for_claude_code.md`, `claude-settings.json`) is testing and documentation.

## Step 3 — Edit `config.yaml` in your fork

Open `config.yaml` in your fork. It will look like:

```yaml
model_list:
  - model_name: gateway-main
    litellm_params: {model: nvidia_nim/moonshotai/kimi-k3, api_key: os.environ/NVIDIA_NIM_API_KEY, timeout: 60}
  # ... 30+ more entries
```

The `os.environ/NVIDIA_NIM_API_KEY` syntax means "read this from the
environment variable named NVIDIA_NIM_API_KEY". You don't need to edit
the model names — just make sure the env var names you set in Step 5
match what's in `config.yaml`.

If you only have keys for one provider, you can simplify drastically. The minimal config for a working gateway with just one NVIDIA NIM key:

```yaml
model_list:
  - model_name: gateway-main
    litellm_params: {model: nvidia_nim/moonshotai/kimi-k3, api_key: os.environ/NVIDIA_NIM_API_KEY, timeout: 60}

router_settings:
  routing_strategy: simple-shuffle
  num_retries: 1
  cooldown_time: 60

general_settings:
  master_key: os.environ/LITELLM_MASTER_KEY
```

But you'll lose fallback. The full config in the repo has 6 model
groups with multi-provider fallback. Keep it as-is unless you have a
specific reason.

## Step 4 — Deploy to Railway

1. Go to https://railway.com/new/template/<your-github-username>/litellm-gateway
2. If that doesn't work, go to https://railway.com/dashboard, click **New Project** → **Deploy from GitHub repo** → select your forked repo
3. Railway will detect the `Dockerfile` and start building
4. The first build takes 2-3 minutes

## Step 5 — Set environment variables

In the Railway dashboard, click your service → **Variables** → **Raw Editor**. Paste:

```bash
# REQUIRED: master key for the gateway itself. Anything. 32+ chars.
LITELLM_MASTER_KEY=sk-<generate-something-long-and-random>

# OPTIONAL: salt key (only used if you add a database later; not used now)
# LITELLM_SALT_KEY=sk-salt-<generate-something-once-and-never-change>

# OPTIONAL: bind port (defaults to 4000)
# PORT=4000

# === TIER 1: Google Gemini (paste as many as you have) ===
# GEMINI_API_KEY_1=AIza...
# GEMINI_API_KEY_2=AIza...
# GEMINI_API_KEY_3=AIza...
# ... (up to 10)

# === TIER 2: OpenCode Zen (optional) ===
# OPENCODE_ZEN_API_KEY=sk-...

# === TIER 3: OpenRouter (paste as many as you have) ===
# OPENROUTER_API_KEY_1=sk-or-v1-...
# OPENROUTER_API_KEY_2=sk-or-v1-...
# ... (up to 4)

# === TIER 4: NVIDIA NIM (REQUIRED for the free unlimited tier) ===
NVIDIA_NIM_API_KEY=nvapi-...

# === TIER 5: Groq (optional but fast) ===
# GROQ_API_KEY_1=gsk-...
# GROQ_API_KEY_2=gsk-...

# === TIER 6: KiraAI (PAID, leave commented unless you want it) ===
# KIRAAI_API_KEY=kira-...
```

**Notes on keys**:
- `LITELLM_MASTER_KEY` is what Claude Code uses to authenticate to the gateway. Generate it once with a password manager and treat it like any other secret.
- For multi-key providers (Gemini, OpenRouter, Groq), uncomment and fill in as many as you have. The gateway will load-balance across them automatically.
- For single-key providers (NVIDIA NIM, OpenCode Zen), one entry each.
- You do NOT need keys for all providers. The fallback chain just won't have anywhere to fall through to for tiers you skip.

## Step 6 — Set the healthcheck

This is the most common place for the deploy to fail. The default
Railway healthcheck (port-only) can mark the service as failed while
the app is still booting.

In the Railway dashboard:
1. Click your service → **Settings** tab
2. Find **Healthcheck Path** under the Deploy section
3. Set it to: `/health/liveliness`
4. Set **Healthcheck Timeout** to: `300` (5 minutes — first boot can be slow on free tier)

## Step 7 — Generate a public URL

1. Click your service → **Settings** tab
2. Find **Networking** → **Public Networking** → click **Generate Domain**
3. Railway gives you a URL like `https://litellm-gateway-production-xxxx.up.railway.app`
4. **Copy this URL** — you'll need it for Claude Code

## Step 8 — Wait for green deploy

Watch the deploy log. You should see:
```
INFO:     Uvicorn running on http://0.0.0.0:4000
```
within 30-60 seconds. If you see "Application startup complete" → the
gateway is ready.

## Step 9 — Verify it works

Open PowerShell or any terminal and run:

```bash
# Replace with YOUR URL from Step 7
URL="https://litellm-gateway-production-xxxx.up.railway.app"

# 1. Health check
curl -s $URL/health/liveliness
# Expected: "I'm alive!"

# 2. Test a real Claude Code request
curl -s -X POST $URL/v1/messages \
  -H "x-api-key: $LITELLM_MASTER_KEY" \
  -H "anthropic-version: 2023-06-01" \
  -H "content-type: application/json" \
  -d '{
    "model": "gateway-main",
    "max_tokens": 100,
    "messages": [{"role": "user", "content": "say hi"}]
  }'
# Expected: JSON with "content": [{"type": "text", "text": "Hi! ..."}]
```

If both work, the gateway is ready.

## Step 10 — Connect Claude Code

Open `~/.claude/settings.json` (on Windows: `%USERPROFILE%\.claude\settings.json`).

If the file doesn't exist, create it. If it does, add/update the `env` block:

```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "https://litellm-gateway-production-xxxx.up.railway.app",
    "ANTHROPIC_AUTH_TOKEN": "sk-<your-LITELLM_MASTER_KEY>",
    "ANTHROPIC_MODEL": "gateway-main",
    "ANTHROPIC_SMALL_FAST_MODEL": "gateway-fast",
    "CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT": "1"
  }
}
```

Replace the URL and token with your actual values. The two model names
should match `model_name` entries in your `config.yaml`.

Save the file. Open a **new** PowerShell window (the settings only apply to new shells; close and reopen to be safe). Run `claude`.

## Step 11 — Verify Claude Code uses it

Inside Claude Code, type `/status`. You should see:

- **Anthropic base URL**: `https://litellm-gateway-production-xxxx.up.railway.app`
- **Auth token**: ANTHROPIC_AUTH_TOKEN
- **Model**: `gateway-main` (200k context window via `CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT`)

If you see the gateway URL, the connection works. Now type any
question — Claude Code will route through the gateway.

## Step 12 — Test the fallback chain

To prove the gateway actually falls back when one provider fails, run
the empirical test (after cloning the repo locally):

```bash
# In your fork, in Python:
pip install litellm
python test_empirical.py https://litellm-gateway-production-xxxx.up.railway.app sk-<your-key>
```

Expected output: all 6 model groups return `PASS` with `stop_reason:
tool_use` and a valid `tool_input`. If any show `FAIL`, that model
has a problem and the fallback chain will use the next group.

## Common problems and fixes

### "I'm alive!" works but `/v1/messages` returns 500

The gateway is up but can't reach a provider. Check the Railway logs:

```bash
railway logs
```

Look for:
- `litellm.AuthenticationError` → API key is wrong
- `litellm.RateLimitError` → all keys for that provider are rate-limited (gateway will fall through)
- `litellm.NotFoundError` → model name is wrong for that provider
- `ConnectError` → provider's API is down

### "Connection refused" on the gateway URL

Service crashed. Check `railway status` and `railway logs` for the reason.

Common causes:
- **Database init failure**: You have `DATABASE_URL` set but no working Postgres. Solution: delete the `DATABASE_URL` env var.
- **Bad model name in config.yaml**: The model name doesn't exist for that provider. Check the provider's `/v1/models` endpoint.
- **Healthcheck timeout**: First deploy is slow. Set healthcheck timeout to 300s.

### Claude Code says "Invalid API key"

Either:
- `ANTHROPIC_AUTH_TOKEN` in settings.json doesn't match `LITELLM_MASTER_KEY` in Railway Variables
- `ANTHROPIC_API_KEY` is also set somewhere (Windows env var, .bashrc, etc.) — it conflicts. Unset it: `Remove-Item Env:ANTHROPIC_API_KEY`

### "This model is no longer available to new users" (Gemini)

`gemini-2.5-pro` is 404 on free Google AI Studio keys. Use `gemini-2.5-flash` or `gemini-3.5-flash` instead. Update `config.yaml`.

### "Rate limit exceeded"

All keys for a provider are rate-limited. The gateway will automatically fall through to the next tier. If all tiers are exhausted, you get a 500. Either:
- Add more keys
- Switch the priority order in `config.yaml` to put the unrate-limited provider first
- Wait for the rate limit to reset (usually 1 minute for most providers)

### Claude Code says "model not recognized"

Add `CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT=1` to your settings.json. This tells Claude Code to not enforce its 200K default and let the gateway pick the real context window.

### "I gotta go, fix this"

Open Railway dashboard → service → Logs. Read the last 50 lines.
Common patterns:
- `ConnectionError: Cannot connect to host postgres.railway.internal` → delete `DATABASE_URL` env var
- `model not found` → fix the model name in `config.yaml`
- `AuthenticationError` → fix the API key
- `PrismaClientInitializationError` → the DB image is being used; switch to `main-stable` image

## Updating the config later

1. Edit `config.yaml` in your fork
2. Commit and push to your fork
3. Railway will auto-deploy on push (if GitHub auto-deploy is connected) OR you can `railway up --detach` from the CLI

To trigger a redeploy manually:
```bash
# From your fork directory, with railway CLI installed
railway login  # if not already logged in
railway link   # link to your project
railway up --detach
```

## Rotating the master gateway key

1. Generate a new strong random string (32+ chars)
2. Railway dashboard → your service → Variables → change `LITELLM_MASTER_KEY`
3. The container auto-restarts with the new key
4. Update `ANTHROPIC_AUTH_TOKEN` in `~/.claude/settings.json` on every Claude Code client

The old key stops working the moment the new container starts.

## Adding a new provider or model

1. Edit `config.yaml` in your fork
2. Add a new deployment under the appropriate `model_name` (e.g. `gateway-main-2`) with `litellm_params.model` set to the LiteLLM-format model name and `api_key: os.environ/NEW_KEY`
3. Add the new env var in Railway Variables
4. Commit and push — Railway auto-deploys

For LiteLLM model name format, see https://docs.litellm.ai/docs/providers/:
- Gemini: `gemini/gemini-2.5-flash`
- OpenAI: `openai/gpt-4o` (or just `gpt-4o`)
- Anthropic: `anthropic/claude-sonnet-4-5`
- OpenRouter: `openrouter/<upstream-name>` (e.g. `openrouter/anthropic/claude-sonnet-4-5`)
- NVIDIA NIM: `nvidia_nim/<model-id>` (e.g. `nvidia_nim/moonshotai/kimi-k3`)
- Groq: `groq/<model-name>` (e.g. `groq/llama-3.1-70b-versatile`)
- xAI: `xai/grok-2-latest`
- Together AI: `together_ai/<model>`

## File reference

- **`Dockerfile`** — pulls `ghcr.io/berriai/litellm:main-stable`, copies `config.yaml`, runs the proxy on port 4000
- **`config.yaml`** — the actual routing rules. Single source of truth for which models are used and in what order
- **`railway.json`** — Railway service config: Nixpacks builder, port 4000, healthcheck on `/health/liveliness`
- **`test_empirical.py`** — runs the tool_use test against all 6 model groups. Use to validate any config change
- **`test_gateway.py`** — local end-to-end tests (auth, streaming, tools, models). Run before deploying
- **`handoff_for_claude_code.md`** — session continuity doc. Read this first if you're continuing work from another Claude Code session
- **`claude-settings.json`** — the JSON to put in `~/.claude/settings.json`. Note: also contains the master gateway token. Don't commit if you treat that as secret.

## Architecture decisions and WHY

- **No database**: `litellm-database` image hangs on Prisma init due to Railway internal DNS race. The plain `main-stable` image boots in seconds without a DB.
- **`drop_params: true`**: Claude Code sends Anthropic-specific fields (cache_control, citations, anthropic-beta headers) that non-Anthropic providers reject. Without this, you get 400 errors.
- **6 model groups, not 2**: Lets `ANTHROPIC_MODEL` and `ANTHROPIC_SMALL_FAST_MODEL` point to different groups (slow-but-strong vs fast-but-lightweight). Also gives finer fallback granularity.
- **`cooldown_time: 60` + `allowed_fails: 2`**: A key that 429s twice in 60s gets benched. Other keys in the same group keep serving. This is what makes "if one key hits its limit, use the next" work.
- **NIM primary**: NVIDIA NIM free tier is the most generous (essentially unlimited, just rate-limited). OpenRouter has 200 req/day total. Gemini is last because free keys have tight per-minute limits.
- **`timeout: 60` per request**: NIM models are slow on cold start. 60s gives them a fair chance; they fall over to the next tier after that.

## License and contributions

This gateway configuration is a thin wrapper around LiteLLM
(https://github.com/BerriAI/litellm), which is Apache 2.0 licensed.
Fork it, modify it, redistribute it — no warranty, your keys, your
responsibility.

## Support and further reading

- LiteLLM docs: https://docs.litellm.ai
- Claude Code gateway connection: https://docs.anthropic.com/en/docs/claude-code/llm-gateway-connect
- Railway docs: https://docs.railway.com
- Anthropic Messages API format: https://docs.anthropic.com/en/api/messages
- This repo's empirical test results and known-good model list: `handoff_for_claude_code.md` (in the repo)
