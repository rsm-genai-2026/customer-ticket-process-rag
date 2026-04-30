"""Use the OpenAI Codex OAuth route from Python.

This module implements the OpenAI Codex OAuth flow:

- OAuth authorize: https://auth.openai.com/oauth/authorize
- OAuth token: https://auth.openai.com/oauth/token
- Redirect callback: http://localhost:1455/auth/callback
- Model endpoint: https://chatgpt.com/backend-api/codex/responses

The high-level function is ``ask_gpt_with_oauth``. It logs in when needed,
refreshes expired tokens, stores credentials in a local JSON file, and returns
the streamed response text.
"""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import html
import json
import os
import platform
import secrets
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Iterable

CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
AUTHORIZE_URL = "https://auth.openai.com/oauth/authorize"
TOKEN_URL = "https://auth.openai.com/oauth/token"
REDIRECT_URI = "http://localhost:1455/auth/callback"
SCOPE = "openid profile email offline_access"
CODEX_RESPONSES_URL = "https://chatgpt.com/backend-api/codex/responses"
JWT_AUTH_CLAIM = "https://api.openai.com/auth"
_SSL_CONTEXT: ssl.SSLContext | None = None


@dataclasses.dataclass(frozen=True)
class OAuthCredentials:
    access: str
    refresh: str
    expires: int
    account_id: str

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "OAuthCredentials":
        return cls(
            access=_required_str(data, "access"),
            refresh=_required_str(data, "refresh"),
            expires=int(data["expires"]),
            account_id=_required_str(data, "account_id"),
        )

    def to_json(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def ask_gpt_with_oauth(
    prompt: str,
    *,
    model: str = "gpt-5.4",
    instructions: str | None = None,
    reasoning_effort: str | None = "high",
    text_verbosity: str = "medium",
    credentials_path: str | Path | None = None,
    session_id: str | None = None,
    timeout: float = 180.0,
) -> str:
    """Send a prompt to GPT through the OpenAI Codex OAuth route.

    This uses ChatGPT/Codex subscription OAuth, not an OpenAI Platform API key.
    On first run it opens a browser for sign-in, then stores refreshable
    credentials locally for future Python runs.
    """

    credentials = ensure_openai_codex_credentials(credentials_path=credentials_path)
    return create_codex_response(
        prompt,
        credentials=credentials,
        model=model,
        instructions=instructions,
        reasoning_effort=reasoning_effort,
        text_verbosity=text_verbosity,
        session_id=session_id,
        timeout=timeout,
    )


def ensure_openai_codex_credentials(
    *,
    credentials_path: str | Path | None = None,
    refresh_margin_seconds: int = 60,
) -> OAuthCredentials:
    """Load, refresh, or create OpenAI Codex OAuth credentials."""

    path = _resolve_credentials_path(credentials_path)
    credentials = load_openai_codex_credentials(path)

    if credentials is None:
        credentials = _load_codex_cli_credentials()

    now_ms = int(time.time() * 1000)
    if credentials and credentials.expires > now_ms + refresh_margin_seconds * 1000:
        save_openai_codex_credentials(credentials, path)
        return credentials

    if credentials:
        try:
            refreshed = refresh_openai_codex_token(credentials.refresh)
        except RuntimeError as error:
            if _should_reauthenticate_after_refresh_error(error):
                logged_in = login_openai_codex_oauth()
                save_openai_codex_credentials(logged_in, path)
                return logged_in
            raise
        save_openai_codex_credentials(refreshed, path)
        return refreshed

    logged_in = login_openai_codex_oauth()
    save_openai_codex_credentials(logged_in, path)
    return logged_in


def login_openai_codex_oauth(
    *,
    open_browser: bool = True,
    originator: str = "oauth_gpt",
    callback_timeout: float = 300.0,
) -> OAuthCredentials:
    """Run the browser PKCE OAuth flow."""

    verifier, challenge = _generate_pkce()
    state = secrets.token_hex(16)
    auth_url = _build_authorize_url(
        challenge=challenge,
        state=state,
        originator=originator,
    )

    server = _OAuthCallbackServer(state)
    server.start()

    print("OpenAI Codex OAuth URL:")
    print(auth_url)
    if open_browser:
        webbrowser.open(auth_url)

    code: str | None = None
    if server.started:
        code = server.wait_for_code(timeout=callback_timeout)
    server.close()

    if not code:
        pasted = input("Paste the authorization code or full redirect URL: ").strip()
        parsed_code, parsed_state = _parse_authorization_input(pasted)
        if parsed_state and parsed_state != state:
            raise RuntimeError("OAuth state mismatch")
        code = parsed_code

    if not code:
        raise RuntimeError("Missing authorization code")

    return _exchange_authorization_code(code=code, verifier=verifier)


def refresh_openai_codex_token(refresh_token: str) -> OAuthCredentials:
    """Refresh an OpenAI Codex OAuth token."""

    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": CLIENT_ID,
    }
    token_json = _post_form(TOKEN_URL, data)
    return _credentials_from_token_json(token_json)


def create_codex_response(
    prompt: str,
    *,
    credentials: OAuthCredentials,
    model: str = "gpt-5.4",
    instructions: str | None = None,
    reasoning_effort: str | None = "high",
    text_verbosity: str = "medium",
    session_id: str | None = None,
    timeout: float = 180.0,
) -> str:
    """Call ``/backend-api/codex/responses`` and return streamed text."""

    body: dict[str, Any] = {
        "model": model,
        "store": False,
        "stream": True,
        "instructions": instructions or "You are a helpful assistant.",
        "input": [
            {
                "role": "user",
                "content": [{"type": "input_text", "text": prompt}],
            }
        ],
        "text": {"verbosity": text_verbosity},
        "include": ["reasoning.encrypted_content"],
        "tool_choice": "auto",
        "parallel_tool_calls": True,
    }
    if reasoning_effort:
        body["reasoning"] = {"effort": reasoning_effort, "summary": "auto"}
    if session_id:
        body["prompt_cache_key"] = session_id

    headers = {
        "Authorization": f"Bearer {credentials.access}",
        "chatgpt-account-id": credentials.account_id,
        "originator": "oauth_gpt",
        "User-Agent": _user_agent(),
        "OpenAI-Beta": "responses=experimental",
        "accept": "text/event-stream",
        "content-type": "application/json",
    }
    if session_id:
        headers["session_id"] = session_id

    request = urllib.request.Request(
        CODEX_RESPONSES_URL,
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )

    try:
        with _urlopen(request, timeout=timeout) as response:
            chunks: list[str] = []
            completed_response: dict[str, Any] | None = None
            for event in _iter_sse_json(response):
                event_type = event.get("type")
                if event_type == "response.output_text.delta":
                    delta = event.get("delta")
                    if isinstance(delta, str):
                        chunks.append(delta)
                elif event_type in {"response.completed", "response.done", "response.incomplete"}:
                    response_obj = event.get("response")
                    if isinstance(response_obj, dict):
                        completed_response = response_obj
                elif event_type == "response.failed":
                    raise RuntimeError(_response_failed_message(event))
                elif event_type == "error":
                    raise RuntimeError(_event_error_message(event))
    except urllib.error.HTTPError as error:
        raise RuntimeError(_http_error_message(error)) from error
    except urllib.error.URLError as error:
        raise RuntimeError(_url_error_message(error)) from error

    if chunks:
        return "".join(chunks)
    if completed_response:
        return _extract_response_text(completed_response)
    return ""


def load_openai_codex_credentials(path: str | Path | None = None) -> OAuthCredentials | None:
    resolved = _resolve_credentials_path(path)
    try:
        data = json.loads(resolved.read_text(encoding="utf-8"))
        return OAuthCredentials.from_json(data)
    except FileNotFoundError:
        return None
    except Exception:
        return None


def save_openai_codex_credentials(
    credentials: OAuthCredentials,
    path: str | Path | None = None,
) -> None:
    resolved = _resolve_credentials_path(path)
    resolved.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    payload = json.dumps(credentials.to_json(), indent=2, sort_keys=True) + "\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    with os.fdopen(os.open(resolved, flags, 0o600), "w", encoding="utf-8") as file:
        file.write(payload)


class _OAuthCallbackServer:
    def __init__(self, expected_state: str) -> None:
        self.expected_state = expected_state
        self._code: str | None = None
        self._event = threading.Event()
        self._server: HTTPServer | None = None
        self.started = False

    def start(self) -> None:
        parent = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - stdlib method name
                parsed = urllib.parse.urlparse(self.path)
                query = urllib.parse.parse_qs(parsed.query)
                if parsed.path != "/auth/callback":
                    self._send_html(404, "Callback route not found.")
                    return
                if query.get("state", [None])[0] != parent.expected_state:
                    self._send_html(400, "State mismatch.")
                    return
                code = query.get("code", [None])[0]
                if not code:
                    self._send_html(400, "Missing authorization code.")
                    return
                parent._code = code
                parent._event.set()
                self._send_html(200, "OpenAI authentication completed. You can close this window.")

            def log_message(self, _format: str, *args: Any) -> None:
                return

            def _send_html(self, status: int, message: str) -> None:
                escaped = html.escape(message)
                body = f"<!doctype html><title>OAuth</title><p>{escaped}</p>".encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        try:
            self._server = HTTPServer(("127.0.0.1", 1455), Handler)
            self.started = True
        except OSError:
            self.started = False
            return

        thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        thread.start()

    def wait_for_code(self, timeout: float) -> str | None:
        if not self._event.wait(timeout=max(0.0, timeout)):
            return None
        return self._code

    def close(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()


def _generate_pkce() -> tuple[str, str]:
    verifier = _base64url(secrets.token_bytes(32))
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = _base64url(digest)
    return verifier, challenge


def _build_authorize_url(*, challenge: str, state: str, originator: str) -> str:
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPE,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true",
        "originator": originator,
    }
    return f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"


def _exchange_authorization_code(*, code: str, verifier: str) -> OAuthCredentials:
    token_json = _post_form(
        TOKEN_URL,
        {
            "grant_type": "authorization_code",
            "client_id": CLIENT_ID,
            "code": code,
            "code_verifier": verifier,
            "redirect_uri": REDIRECT_URI,
        },
    )
    return _credentials_from_token_json(token_json)


def _credentials_from_token_json(token_json: dict[str, Any]) -> OAuthCredentials:
    access = _required_str(token_json, "access_token")
    refresh = _required_str(token_json, "refresh_token")
    expires_in = token_json.get("expires_in")
    if not isinstance(expires_in, (int, float)):
        raise RuntimeError(f"Token response missing numeric expires_in: {token_json!r}")
    account_id = _extract_account_id(access)
    expires = int(time.time() * 1000 + float(expires_in) * 1000)
    return OAuthCredentials(access=access, refresh=refresh, expires=expires, account_id=account_id)


def _post_form(url: str, data: dict[str, str]) -> dict[str, Any]:
    encoded = urllib.parse.urlencode(data).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=encoded,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with _urlopen(request, timeout=60) as response:
            parsed = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raise RuntimeError(_http_error_message(error)) from error
    except urllib.error.URLError as error:
        raise RuntimeError(_url_error_message(error)) from error
    if not isinstance(parsed, dict):
        raise RuntimeError(f"Unexpected token response: {parsed!r}")
    return parsed


def _iter_sse_json(response: Any) -> Iterable[dict[str, Any]]:
    data_lines: list[str] = []
    for raw_line in response:
        line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
        if not line:
            event = _parse_sse_data_lines(data_lines)
            data_lines.clear()
            if event is not None:
                yield event
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].strip())
    event = _parse_sse_data_lines(data_lines)
    if event is not None:
        yield event


def _parse_sse_data_lines(data_lines: list[str]) -> dict[str, Any] | None:
    if not data_lines:
        return None
    payload = "\n".join(data_lines).strip()
    if not payload or payload == "[DONE]":
        return None
    try:
        event = json.loads(payload)
    except json.JSONDecodeError:
        return None
    return event if isinstance(event, dict) else None


def _extract_response_text(response: dict[str, Any]) -> str:
    output_text = response.get("output_text")
    if isinstance(output_text, str):
        return output_text

    parts: list[str] = []
    output = response.get("output")
    if not isinstance(output, list):
        return ""
    for item in output:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            text = block.get("text")
            if isinstance(text, str) and block.get("type") in {"output_text", "text"}:
                parts.append(text)
    return "".join(parts)


def _event_error_message(event: dict[str, Any]) -> str:
    code = event.get("code")
    message = event.get("message")
    if isinstance(message, str) and message:
        return f"Codex error: {message}"
    if isinstance(code, str) and code:
        return f"Codex error: {code}"
    return f"Codex error: {event!r}"


def _response_failed_message(event: dict[str, Any]) -> str:
    response = event.get("response")
    if isinstance(response, dict):
        error = response.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            code = error.get("code")
            if isinstance(message, str) and message:
                return message
            if isinstance(code, str) and code:
                return code
    return f"Codex response failed: {event!r}"


def _http_error_message(error: urllib.error.HTTPError) -> str:
    body = error.read().decode("utf-8", errors="replace")
    try:
        parsed = json.loads(body)
        message = parsed.get("error", {}).get("message")
        if isinstance(message, str) and message:
            return f"HTTP {error.code}: {message}"
    except Exception:
        pass
    suffix = f": {body}" if body else ""
    return f"HTTP {error.code}{suffix}"


def _parse_authorization_input(value: str) -> tuple[str | None, str | None]:
    trimmed = value.strip()
    if not trimmed:
        return None, None
    try:
        parsed = urllib.parse.urlparse(trimmed)
        if parsed.scheme and parsed.netloc:
            params = urllib.parse.parse_qs(parsed.query)
            return params.get("code", [None])[0], params.get("state", [None])[0]
    except Exception:
        pass
    if "#" in trimmed:
        code, state = trimmed.split("#", 1)
        return code or None, state or None
    if "code=" in trimmed:
        params = urllib.parse.parse_qs(trimmed)
        return params.get("code", [None])[0], params.get("state", [None])[0]
    return trimmed, None


def _load_codex_cli_credentials() -> OAuthCredentials | None:
    auth_path = _resolve_codex_home() / "auth.json"
    try:
        data = json.loads(auth_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    tokens = data.get("tokens")
    if data.get("auth_mode") not in {None, "chatgpt"}:
        return None
    if not isinstance(tokens, dict):
        return None
    access = tokens.get("access_token")
    refresh = tokens.get("refresh_token")
    if not isinstance(access, str) or not access:
        return None
    if not isinstance(refresh, str) or not refresh:
        return None
    account_id = tokens.get("account_id")
    if not isinstance(account_id, str) or not account_id:
        try:
            account_id = _extract_account_id(access)
        except RuntimeError:
            return None
    try:
        expires = _extract_expiry_ms(access) or 0
    except RuntimeError:
        expires = 0
    return OAuthCredentials(access=access, refresh=refresh, expires=expires, account_id=account_id)


def _resolve_codex_home() -> Path:
    configured = os.environ.get("CODEX_HOME", "").strip()
    if configured == "~":
        return Path.home()
    if configured.startswith("~/"):
        return Path.home() / configured[2:]
    if configured:
        return Path(configured).expanduser().resolve()
    return Path.home() / ".codex"


def _resolve_credentials_path(path: str | Path | None) -> Path:
    if path is not None:
        return Path(path).expanduser()
    configured = os.environ.get("OPENAI_CODEX_OAUTH_PATH", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".oauth_gpt" / "openai_codex_oauth.json"


def _extract_account_id(access_token: str) -> str:
    payload = _decode_jwt_payload(access_token)
    auth = payload.get(JWT_AUTH_CLAIM)
    if isinstance(auth, dict):
        account_id = auth.get("chatgpt_account_id")
        if isinstance(account_id, str) and account_id:
            return account_id
    raise RuntimeError("Failed to extract accountId from OpenAI Codex token")


def _extract_expiry_ms(access_token: str) -> int | None:
    payload = _decode_jwt_payload(access_token)
    exp = payload.get("exp")
    if isinstance(exp, (int, float)) and exp > 0:
        return int(exp * 1000)
    return None


def _decode_jwt_payload(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) != 3:
        raise RuntimeError("Invalid JWT token")
    padded = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        payload = json.loads(decoded)
    except Exception as error:
        raise RuntimeError("Invalid JWT payload") from error
    if not isinstance(payload, dict):
        raise RuntimeError("Invalid JWT payload")
    return payload


def _base64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _required_str(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"Missing string field {key!r}")
    return value


def _urlopen(request: urllib.request.Request, *, timeout: float) -> Any:
    return urllib.request.urlopen(request, timeout=timeout, context=_get_ssl_context())


def _get_ssl_context() -> ssl.SSLContext:
    global _SSL_CONTEXT
    if _SSL_CONTEXT is not None:
        return _SSL_CONTEXT

    cafile = _resolve_ca_bundle()
    if cafile:
        _SSL_CONTEXT = ssl.create_default_context(cafile=str(cafile))
    else:
        _SSL_CONTEXT = ssl.create_default_context()
    return _SSL_CONTEXT


def _resolve_ca_bundle() -> Path | None:
    for env_name in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"):
        value = os.environ.get(env_name, "").strip()
        if value:
            path = Path(value).expanduser()
            if path.is_file():
                return path

    try:
        import certifi  # type: ignore[import-not-found]

        path = Path(certifi.where())
        if path.is_file():
            return path
    except Exception:
        pass

    for candidate in (
        Path("/etc/ssl/certs/ca-certificates.crt"),
        Path("/etc/pki/tls/certs/ca-bundle.crt"),
        Path("/etc/ssl/cert.pem"),
        Path("/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem"),
    ):
        if candidate.is_file():
            return candidate
    return None


def _url_error_message(error: urllib.error.URLError) -> str:
    reason = getattr(error, "reason", error)
    if isinstance(reason, ssl.SSLCertVerificationError):
        return (
            "TLS certificate verification failed while contacting OpenAI. "
            "Install/update your Python CA certificates, install certifi, or set SSL_CERT_FILE "
            "to a valid CA bundle path. "
            f"Details: {reason}"
        )
    return f"Network error while contacting OpenAI: {reason}"


def _should_reauthenticate_after_refresh_error(error: RuntimeError) -> bool:
    message = str(error).lower()
    return (
        "invalid_grant" in message
        or "refresh_token_reused" in message
        or "refresh token" in message
        and (
            "expired" in message
            or "invalid" in message
            or "reused" in message
            or "already been used" in message      # OpenAI's literal wording
            or "please try signing in again" in message
        )
    )


def _user_agent() -> str:
    return f"oauth_gpt ({platform.system()} {platform.release()}; {platform.machine()})"


if __name__ == "__main__":
    answer = ask_gpt_with_oauth("Reply with exactly: OAuth is working.")
    print(answer)
