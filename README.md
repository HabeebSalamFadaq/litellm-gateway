# LiteLLM Cloud Gateway for Claude Code

A single HTTPS endpoint that Claude Code talks to, with automatic fallback
across 5 tiers of free AI providers. Runs in the cloud, independent of
your laptop, and never exposes provider API keys to the client.

```
Claude Code
    ↓ ANTHROPIC_BASE_URL=https://your-gateway.up.railway.app
    ↓ ANTHROPIC_AUTH_TOKEN=<your-gateway-key>
HTTPS endpoint (Railway)
    ↓
LiteLLM proxy
    ↓
Tier 1: Google Gemini      (6 free keys cycled)
    ↓ on 429 / 5xx
Tier 2: OpenCode Zen        (2 confirmed free models)
    ↓ on 429 / 5xx
Tier 3: OpenRouter          (4 keys, free models)
    ↓ on 429 / 5xx
Tier 4: NVIDIA NIM          (1 key, free coding models)
    ↓ on 429 / 5xx
Tier 5: KiraAI              (paid, last resort, disabled by default)
```

## 1. Deploy to Railway

### One-click
The repo includes a `railway.toml` that makes it a one-click deploy. Click
the button below and set the environment variables in step 2.

[![Deploy on Railway](https://railway.com/button.svg)](https://railway.com/deploy/HabeebSalamFadaq/litellm-gateway?referralCode=)

### Manual
1. Push the repo to your GitHub (already done in this case: `HabeebSalamFadaq/litellm-gateway`).
2. Open Railway → New Project → Deploy from GitHub Repo → pick this repo.
3. Wait for the first build. The Dockerfile installs LiteLLM and starts
   the proxy on port 4000.
4. In **Variables**, set the secrets listed in section 2 below.
5. After deploy, Railway gives you a public HTTPS URL like
   `https://litellm-gateway-production-xxxx.up.railway.app`.
6. The proxy is now live. The container restarts automatically on crash.

## 2. Required environment variables

Set these as Railway service Variables. None of them need quotes.

### Gateway auth (you define this; Claude Code uses it as its bearer token)
```
LITELLM_MASTER_KEY=sk-<generate-something-with-at-least-32-random-characters>
```

### Tier 1 - Google Gemini (6 free keys, all required to enable rotation)
```
GEMINI_API_KEY_1=AIza...
GEMINI_API_KEY_2=AIza...
GEMINI_API_KEY_3=AIza...
GEMINI_API_KEY_4=AIza...
GEMINI_API_KEY_5=AIza...
GEMINI_API_KEY_6=AIza...
```

### Tier 2 - OpenCode Zen (1 key, 2 free models)
```
OPENCODE_ZEN_API_KEY=sk-...
```

### Tier 3 - OpenRouter (4 keys, 4 free models)
```
OPENROUTER_API_KEY_1=sk-or-v1-...
OPENROUTER_API_KEY_2=sk-or-v1-...
OPENROUTER_API_KEY_3=sk-or-v1-...
OPENROUTER_API_KEY_4=sk-or-v1-...
```

### Tier 4 - NVIDIA NIM (1 key, 5 free coding models)
```
NVIDIA_NIM_API_KEY=nvapi-...
```

### Tier 5 - KiraAI (OPTIONAL, mostly paid, leave empty to disable)
```
KIRAAI_API_KEY=kira-...
```

See `.env.example` for the canonical template.

## 3. Connect Claude Code

Set the two environment variables below in your shell or in
`~/.claude/settings.json` (see the [official docs](https://docs.anthropic.com/en/docs/claude-code/llm-gateway-connect)).

### PowerShell (Windows)
```powershell
$env:ANTHROPIC_BASE_URL = "https://<your-railway-url>.up.railway.app"
$env:ANTHROPIC_AUTH_TOKEN = "sk-<your-LITELLM_MASTER_KEY>"
claude
```

### Bash / Zsh (macOS / Linux / WSL)
```bash
export ANTHROPIC_BASE_URL="https://<your-railway-url>.up.railway.app"
export ANTHROPIC_AUTH_TOKEN="sk-<your-LITELLM_MASTER_KEY>"
claude
```

### Or in `~/.claude/settings.json` (recommended for persistence)
```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "https://<your-railway-url>.up.railway.app",
    "ANTHROPIC_AUTH_TOKEN": "sk-<your-LITELLM_MASTER_KEY>"
  }
}
```

### Enable model discovery (optional)
If you want Claude Code's `/model` picker to show the gateway models
(`claude-coder`, `claude-coder-zen`, etc.) as **From gateway** entries, also set:
```bash
export CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY=1
```

## 4. Model priority and what happens on failure

Within each tier, multiple API keys and multiple models share the same
`model_name`. LiteLLM uses `simple-shuffle` routing to spread traffic
evenly, and a deployment that fails `allowed_fails=2` times in 60s is
"cooled down" for 60s before being tried again. This is what makes
"if one key hits its limit, use the next key in the same provider"
work — exhausted keys get temporarily removed from the pool until
they recover.

When **all** deployments in a tier are in cooldown, the `fallbacks` chain
moves to the next tier.

### Tier details

| Tier | Provider | Models (in order) | Keys cycled | Pricing |
|------|----------|-------------------|-------------|---------|
| 1 | Google Gemini | `gemini-2.5-flash` (6x), `gemini-3.5-flash` (2x) | 6 free keys | Free tier |
| 2 | OpenCode Zen | `hy3-free`, `nemotron-3-ultra-free` | 1 key | Free |
| 3 | OpenRouter | `nemotron-3-super-120b-a12b:free` (4x), `north-mini-code:free` (2x), `minimax-m3:free` (2x), `nemotron-3-ultra-550b-a55b:free` (2x) | 4 keys | Free (50/day per key) |
| 4 | NVIDIA NIM | `nemotron-3-ultra-550b-a55b`, `nemotron-3-super-120b-a12b`, `kimi-k3`, `nemotron-3-nano-30b-a3b`, `gemma-4-31b-it` | 1 key | Free |
| 5 | KiraAI | `kira-mini-1.0`, `glm-5.3-flash`, `qwen3.8-flash`, `deepseek-v4-flash-free`, `hy3`, `mimo-v2.5`, `deepseek-v4-flash-vision-exp` | 1 key | **PAID** (disabled by default) |

All model names in this config were verified at deploy time to actually
respond with content on the supplied API keys. Models that returned
404, 401 (no payment), or 500 on the supplied key have been removed.

### Failure handling per error type

| Provider error | Action |
|---|---|
| HTTP 429 (rate limit) | Key cooled down 60s, request moves to next key in same tier |
| HTTP 5xx (server error) | Same as 429 |
| Connection timeout / refused | Same as 429 |
| Context window exceeded | Pre-call check fires; falls back to next tier with larger context |
| Content-policy violation | Falls back to next tier |
| Invalid API key (401) | Key is permanently broken, key cooled down longer (3x cooldown) |
| Model not found (404) | Deployment removed from pool permanently, fall to next tier |

## 5. Local development

```bash
# Install
pip install 'litellm[proxy]'

# Set up secrets in .env (copy from .env.example, fill in your keys)
cp .env.example .env
# Edit .env with real keys

# Run on port 4000
litellm --config config.yaml --port 4000 --host 0.0.0.0
```

### Run the test suite (against a running local proxy)
```bash
python test_gateway.py http://localhost:4000 sk-test-gateway-key-32-chars-min-please-change
```

The test suite validates:
1. Health endpoints (`/health/liveliness`, `/health/readiness`)
2. Auth enforcement (`/v1/messages` rejects unauthenticated requests)
3. Anthropic-format request to the primary tier (Claude Code's native format)
4. OpenAI-format request (for OpenAI SDK users)
5. Streaming (Server-Sent Events)
6. Tool/function calling (required for Claude Code)
7. Tier 2 fallback (verifies the next tier also serves requests)
8. Model discovery (Claude Code's `/model` picker)

## 6. Operations

### View logs
In Railway: open the service → click **Logs** in the sidebar. Logs
include which deployment served each request (`x-litellm-model-id` in
response headers) and which deployments are in cooldown.

### Restart the service
Railway restarts the container automatically on crash. To manually
restart: open the service → **Settings** → **Restart**.

### Rotate the gateway key
1. Generate a new strong random string (32+ characters).
2. In Railway Variables, update `LITELLM_MASTER_KEY` to the new value.
3. Wait for the container to redeploy (Railway does this automatically
   on env-var change).
4. Update the `ANTHROPIC_AUTH_TOKEN` on every Claude Code client.

The old key stops working the moment the new container starts, so all
clients must update within the deploy window.

### Add a new provider
1. Add a new tier block in `config.yaml` with its own `model_name`
   (e.g. `claude-coder-groq`).
2. Add the deployments and credentials.
3. Wire it into the `fallbacks`, `content_policy_fallbacks`, and
   `context_window_fallbacks` chains at the right position.
4. Push to GitHub → Railway auto-deploys.

### Add a new API key to an existing tier
Add a new deployment under the existing tier's `model_name` (e.g.
`claude-coder`) with the new `api_key: os.environ/NEW_KEY` and a unique
`model_info.id`. LiteLLM will include it in the simple-shuffle rotation
automatically.

### Remove a provider
Set the relevant env var to empty in Railway Variables. The deployment
will fail to start, and LiteLLM will skip it (and fall through to the
next tier).

## 7. File layout

```
.
├── config.yaml          # The whole gateway configuration
├── Dockerfile           # Builds from the official LiteLLM stable image
├── railway.toml         # Railway service configuration
├── .env.example         # Template for the environment variables
├── .gitignore           # Excludes .env and local test artifacts
├── start_local.cmd      # Windows helper that loads .env then starts proxy
├── test_gateway.py      # End-to-end test suite
├── test_fallback.py     # Validates the fallback chain
└── README.md            # This file
```

## 8. Security guarantees

* **Provider keys never leave the server.** The LiteLLM proxy is the only
  process that knows them; they live as Railway Variables and as `os.environ`
  in the container. Claude Code only ever sees the gateway master key.
* **The gateway requires authentication on every request.** Without the
  `Authorization: Bearer <LITELLM_MASTER_KEY>` header, the proxy returns
  a non-200 response. The Claude Code client always sends this.
* **Keys are never logged.** The proxy config sets
  `disable_spend_logs: true` and LiteLLM's default behavior is to mask
  API keys in any error log lines.
* **Git history is clean.** The first commit accidentally included real
  keys in `start_local.cmd`; this was rewritten via `git filter-branch`
  so the public history is key-free.
* **All secrets are stored in the cloud platform's secret manager**
  (Railway Variables), not in the repo or in the Docker image.

## 9. Limitations

* **LiteLLM can only translate, not invent.** It can take Anthropic-format
  requests and forward them to non-Anthropic providers, but it cannot
  guarantee that every Claude Code feature (e.g. adaptive thinking on
  Claude 4.6+) is supported by the downstream model.
* **Free tiers are not free forever.** Google free keys, OpenRouter
  free-model quotas (50/day/key), and OpenCode Zen's free model list
  can change at any time. The config is built to absorb these changes
  through the fallback chain, but you may need to re-verify the model
  list periodically.
* **Single-region.** Railway deploys to a single region. For
  multi-region failover, you would need to add a load balancer and
  multiple LiteLLM instances (which would also require Redis for
  cross-instance cooldown state).
* **No request-level cost tracking.** The proxy does not store spend
  in a database, so per-key cost attribution is not available. For
  paid providers, track usage on the provider's own dashboard.
* **kiraAI is disabled by default.** All models on that provider are
  paid in VND. If you uncomment the block in `config.yaml`, every
  request that exhausts the free tiers will incur charges.
