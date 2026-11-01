# perplexity-direct

Call Perplexity from Python using your paid web session cookie.
Supports **chat**, **image generation**, and **video generation**.

| Feature | Status |
|---------|--------|
| Chat | Working |
| Image generation | Working |
| Video generation | Working |

Uses the same web ask stream the browser uses (`/rest/sse/perplexity_ask`).
This is unofficial and can break when Perplexity changes their web app.

## Setup

Python must already be installed. Then create a project-local virtualenv
(recommended so dependencies stay inside this folder):

```bash
python -m venv .venv
```

Activate it:

```bash
# Windows PowerShell
.venv/Scripts/Activate.ps1

# Windows cmd
.venv/Scripts/activate.bat

# macOS / Linux
source .venv/bin/activate
```

Install deps and create `.env`:

```bash
pip install -r requirements.txt
cp .env.example .env   # Windows: copy .env.example .env
```

You *can* skip the venv and install into your global/user Python, but a venv
avoids package conflicts with other projects.

Edit `.env` and set `PPLX_COOKIE` to the **value only** of:

`__Secure-next-auth.session-token`

How to copy it:

1. Open https://www.perplexity.ai and stay logged in
2. DevTools → **Application** → **Cookies** → `https://www.perplexity.ai`
3. Select `__Secure-next-auth.session-token`
4. Copy **Value** into `.env`

Check auth:

```bash
python cli.py whoami
```

## Parameters (`.env`)

| Variable | What it does | Can cookie detect it? |
|----------|--------------|------------------------|
| `PPLX_COOKIE` | Session token value used for auth | Required input |
| `PPLX_MODEL` | Default `model_preference` sent on each ask | **No saved default exposed.** Cookie/session tells tier + quotas; model is chosen per request by this client |
| `PPLX_MODE` | Ask path: `concise` or `copilot` | **No.** Also sent per request |
| `PPLX_LANGUAGE` | Language tag (e.g. `en-US`) | No |
| `PPLX_INCOGNITO` | `is_incognito` flag on asks | No |
| `PPLX_API_VERSION` | `params.version` string | No |
| `PPLX_IMPERSONATE` | TLS fingerprint target for `curl_cffi` | No |

### What the cookie *can* identify

From session + rate-limit endpoints (shown by `python cli.py whoami`):

- whether the cookie is authenticated
- `subscription_status` / `subscription_tier` (e.g. active Pro)
- remaining quotas such as `remaining_pro`, `remaining_research`, `remaining_labs`

It does **not** return a single "currently selected chat model" that this client can auto-adopt.
In the web UI, model choice is attached to each ask as `model_preference`.

### `PPLX_MODEL`

Internal Perplexity id for `params.model_preference`.

Common starting points:

| Value | Typical meaning |
|-------|-----------------|
| `pplx_pro` | Default / Perplexity Best |
| `experimental` | Experimental / Sonar-style path |
| `gpt56_terra`, `claude46sonnet`, `gemini31pro_high`, … | Specific model family ids (change over time) |

Override one call without editing `.env`:

```bash
python cli.py --model claude46sonnet chat "..."
```

After a call, the CLI prints `[model] ...` from the upstream `display_model` field, which is useful for confirming what Perplexity actually used.

### `PPLX_MODE`

Maps to `params.mode`:

| Value | Behavior |
|-------|----------|
| `concise` | Lighter / auto-ish ask path |
| `copilot` | Pro Search style path (deeper search; consumes Pro Search quota) |

Note: if `PPLX_MODEL` is `pplx_pro` / `turbo` / `auto` and you do not pass `--mode`, the client forces `concise`.

```bash
python cli.py --mode concise chat "..."
python cli.py --mode copilot chat "..."
```

### `PPLX_INCOGNITO`

Default is `true`.

| Value | Effect |
|-------|--------|
| `true` | Ask runs as Incognito — **not** saved to your normal web Library / thread history |
| `false` | Ask is a normal thread — should appear in the Perplexity web UI history |

The cookie still authenticates you and Pro quota still counts. Incognito only controls history visibility.

To keep threads visible on the website:

```env
PPLX_INCOGNITO=false
```

## Usage

Run commands from the repo root.

```bash
python cli.py whoami
python cli.py chat "Summarize today's top AI news in 5 bullets"
python cli.py image "a red paper crane on a rainy window sill, cinematic"
python cli.py video "an 8 second drone shot over misty mountains at sunrise"
```

### Outputs

| Command | Terminal | Saved under `out/` |
|---------|----------|--------------------|
| `chat` | Prints the answer | `out/chat_<stamp>.md` |
| `image` | Prints media URL(s) | downloaded image file(s) |
| `video` | Prints media URL(s) | downloaded video file(s) |
| `ask` | Prints answer + media | only with `--download` / `--raw` |

Useful flags:

```bash
python cli.py chat "..." --no-save          # stdout only
python cli.py image "..." --no-download     # do not write files
python cli.py video "..." --no-download
python cli.py image "..." --raw             # also dump SSE JSON to out/
python cli.py ask "any prompt" --raw --download
```

## Layout

```text
.
├── README.md
├── .env.example
├── .gitignore
├── requirements.txt
├── pplx_client.py      # session + ask + media parsing
├── cli.py              # command-line entrypoint
├── examples/
│   └── smoke_chat.py
└── out/                # local outputs (gitignored)
```

`cli.py` stays at the repo root on purpose — this project is small enough that a `bin/` or `scripts/` folder adds little value.

## Notes

- Keep `.env` local. Never commit cookies.
- Cookie expiry / rotation will break `whoami` until you paste a fresh value.
- Image and video use your Perplexity subscription quotas.
- Endpoint shapes / model ids can change; use `--raw` when parsing fails.
