# oauth_gpt

Small dependency-free Python helper for calling GPT through the OpenAI Codex OAuth route. It first reuses existing local OAuth credentials when available, stores refreshable credentials locally, and refreshes expired tokens automatically.

This is not the OpenAI Platform API-key path. It uses ChatGPT/Codex OAuth and sends requests to the Codex GPT backend.

## Preferred entry point — just use `utils.connect.ask`

In this project you rarely call `oauth_gpt` directly. Instead, set
`MODEL = "oauth-gpt"` and use the regular helper:

```python
from utils.connect import ask

# Uses your ChatGPT/Codex subscription via OAuth — no TritonAI API key needed
print(ask("Explain decorators in one sentence.", model="oauth-gpt"))
```

`utils.connect.ask` (and `ask_json`) route every request whose `model` is in
`OAUTH_MODELS` through `oauth_gpt`; everything else goes through the normal
TritonAI proxy. Students change one line at the top of their file and the
rest of the code is identical.

## Files

- `oauth_gpt.py`: OAuth login, token refresh, credential storage, and GPT response helper.
- `../tests/test_oauth_gpt.py`: focused unit tests for URL construction, redirect parsing, JWT parsing, SSE parsing, and credential JSON.

## Direct usage (when you need fine-grained control)

From the project root:

```bash
uv run python - <<'PY'
from utils.oauth_gpt import ask_gpt_with_oauth

response = ask_gpt_with_oauth("Reply with one short sentence about Python.")
print(response)
PY
```

If you already have Codex CLI OAuth credentials at `~/.codex/auth.json`, those are used first. Otherwise, a browser window opens. Sign in, allow the callback to `http://localhost:1455/auth/callback`, and the script will continue. If the callback does not complete automatically, paste the redirect URL or authorization code into the terminal.

Credentials are stored at:

```text
~/.oauth_gpt/openai_codex_oauth.json
```

You can override that location:

```python
from oauth_gpt import ask_gpt_with_oauth

response = ask_gpt_with_oauth(
    "Give me three bullet points about OAuth.",
    credentials_path="my_codex_oauth.json",
)
print(response)
```

Or with an environment variable:

```bash
export OPENAI_CODEX_OAUTH_PATH="$HOME/.config/oauth_gpt/credentials.json"
```

## Simple Examples

Ask a direct question:

```python
from utils.oauth_gpt import ask_gpt_with_oauth

answer = ask_gpt_with_oauth("What is a Python decorator? Keep it brief.")
print(answer)
```

Use instructions:

```python
from utils.oauth_gpt import ask_gpt_with_oauth

answer = ask_gpt_with_oauth(
    "Explain logistic regression.",
    instructions="Use plain language for business students.",
)
print(answer)
```

Use a lower reasoning setting for faster simple prompts:

```python
from utils.oauth_gpt import ask_gpt_with_oauth

answer = ask_gpt_with_oauth(
    "Give me a title for a short lesson on SQL joins.",
    reasoning_effort="low",
)
print(answer)
```

Manage credentials separately:

```python
from utils.oauth_gpt import create_codex_response, ensure_openai_codex_credentials

credentials = ensure_openai_codex_credentials()
answer = create_codex_response(
    "Summarize the difference between authentication and authorization.",
    credentials=credentials,
)
print(answer)
```

## Test

```bash
uv run pytest tests/test_oauth_gpt.py -v
```

## Notes

- The helper uses Python standard-library modules only.
- It reuses an existing Codex CLI `~/.codex/auth.json` login when available.
- Expired access tokens are refreshed automatically.
- Refresh tokens for this OAuth route are rotated. If refresh fails because the refresh token is invalid or reused, the helper starts a new browser sign-in. Network and TLS failures are reported directly instead of forcing re-authentication.
- If your Python install cannot verify TLS certificates, install/update your CA certificates, install `certifi`, or set `SSL_CERT_FILE` to a valid CA bundle.
