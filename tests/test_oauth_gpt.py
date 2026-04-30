"""Unit tests for ``utils.oauth_gpt`` — fully offline (no network)."""

import base64
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from utils.oauth_gpt import (
    JWT_AUTH_CLAIM,
    OAuthCredentials,
    _build_authorize_url,
    _extract_account_id,
    _extract_response_text,
    _iter_sse_json,
    _load_codex_cli_credentials,
    _parse_authorization_input,
    _should_reauthenticate_after_refresh_error,
)


def fake_jwt(payload):
    def enc(data):
        raw = json.dumps(data, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    return f"{enc({'alg': 'none'})}.{enc(payload)}.signature"


class OAuthGptTests(unittest.TestCase):
    def test_parse_authorization_input_accepts_redirect_url(self):
        code, state = _parse_authorization_input(
            "http://localhost:1455/auth/callback?code=abc&state=xyz"
        )
        self.assertEqual(code, "abc")
        self.assertEqual(state, "xyz")

    def test_parse_authorization_input_accepts_code_hash_state(self):
        self.assertEqual(_parse_authorization_input("abc#xyz"), ("abc", "xyz"))

    def test_extract_account_id_from_codex_jwt(self):
        token = fake_jwt(
            {
                "exp": int(time.time()) + 3600,
                JWT_AUTH_CLAIM: {"chatgpt_account_id": "acct_123"},
            }
        )
        self.assertEqual(_extract_account_id(token), "acct_123")

    def test_authorize_url_contains_codex_parameters(self):
        url = _build_authorize_url(challenge="challenge", state="state", originator="oauth_gpt")
        self.assertIn("client_id=app_EMoamEEZ73f0CkXaXp7hrann", url)
        self.assertIn("redirect_uri=http%3A%2F%2Flocalhost%3A1455%2Fauth%2Fcallback", url)
        self.assertIn("code_challenge_method=S256", url)
        self.assertIn("codex_cli_simplified_flow=true", url)
        self.assertIn("originator=oauth_gpt", url)

    def test_extract_response_text_from_completed_response(self):
        response = {
            "output": [
                {
                    "type": "message",
                    "content": [
                        {"type": "output_text", "text": "hello"},
                        {"type": "output_text", "text": " world"},
                    ],
                }
            ]
        }
        self.assertEqual(_extract_response_text(response), "hello world")

    def test_iter_sse_json_flushes_final_event_without_blank_line(self):
        lines = [b'data: {"type":"response.output_text.delta","delta":"done"}\n']
        self.assertEqual(
            list(_iter_sse_json(lines)),
            [{"type": "response.output_text.delta", "delta": "done"}],
        )

    def test_credentials_json_round_trip_shape(self):
        creds = OAuthCredentials("access", "refresh", 123, "acct")
        self.assertEqual(OAuthCredentials.from_json(creds.to_json()), creds)

    def test_load_codex_cli_credentials_accepts_current_token_shape_without_auth_mode(self):
        exp = int(time.time()) + 3600
        token = fake_jwt(
            {
                "exp": exp,
                JWT_AUTH_CLAIM: {"chatgpt_account_id": "acct_456"},
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            auth_path = Path(tmp) / "auth.json"
            auth_path.write_text(
                json.dumps(
                    {
                        "tokens": {
                            "access_token": token,
                            "refresh_token": "refresh-token",
                            "account_id": "acct_456",
                        }
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch.dict(os.environ, {"CODEX_HOME": tmp}):
                self.assertEqual(
                    _load_codex_cli_credentials(),
                    OAuthCredentials(
                        access=token,
                        refresh="refresh-token",
                        expires=exp * 1000,
                        account_id="acct_456",
                    ),
                )

    def test_refresh_error_reauth_detection_does_not_match_tls_failures(self):
        self.assertTrue(_should_reauthenticate_after_refresh_error(RuntimeError("invalid_grant")))
        self.assertFalse(
            _should_reauthenticate_after_refresh_error(
                RuntimeError("TLS certificate verification failed")
            )
        )

    def test_refresh_error_reauth_detection_matches_openai_literal_wording(self):
        # Observed in production: OpenAI returns this exact 401 body when a
        # refresh token has been consumed.
        msg = (
            "HTTP 401: Your refresh token has already been used to generate a "
            "new access token. Please try signing in again."
        )
        self.assertTrue(_should_reauthenticate_after_refresh_error(RuntimeError(msg)))


if __name__ == "__main__":
    unittest.main()
