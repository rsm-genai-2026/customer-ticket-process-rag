"""Tests for ``utils.connect``.

All default tests are fully mocked so they run without a TritonAI API key. The
integration test at the bottom is gated behind ``@pytest.mark.integration`` and
is skipped unless ``TRITONAI_API_KEY`` is set.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from utils import connect


def _fake_response(text: str) -> SimpleNamespace:
    """Build a stand-in for ``OpenAI.chat.completions.create`` return value."""
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))]
    )


def _fake_client(text: str = "hello") -> MagicMock:
    client = MagicMock()
    client.chat.completions.create.return_value = _fake_response(text)
    return client


def test_get_client_missing_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TRITONAI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="TRITONAI_API_KEY"):
        connect.get_client()


def test_get_client_accepts_explicit_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TRITONAI_API_KEY", raising=False)
    client = connect.get_client(api_key="abc")
    assert client.api_key == "abc"
    assert str(client.base_url).startswith(connect.BASE_URL)


def test_ask_string_prompt_wraps_system_and_user() -> None:
    client = _fake_client("hi there")
    connect.ask("hello world", client=client)
    kwargs = client.chat.completions.create.call_args.kwargs
    assert kwargs["messages"] == [
        {"role": "system", "content": connect.DEFAULT_SYSTEM},
        {"role": "user", "content": "hello world"},
    ]


def test_ask_custom_system_message() -> None:
    client = _fake_client("ok")
    connect.ask("hi", system="Speak like a pirate.", client=client)
    messages = client.chat.completions.create.call_args.kwargs["messages"]
    assert messages[0] == {"role": "system", "content": "Speak like a pirate."}


def test_ask_list_prompt_passes_through() -> None:
    client = _fake_client("ok")
    msgs = [
        {"role": "system", "content": "You are a sales rep."},
        {"role": "user", "content": "How much?"},
        {"role": "assistant", "content": "$100"},
        {"role": "user", "content": "Too expensive."},
    ]
    connect.ask(msgs, client=client)
    assert client.chat.completions.create.call_args.kwargs["messages"] == msgs


def test_ask_returns_content() -> None:
    client = _fake_client("the answer is 42")
    assert connect.ask("what is the meaning of life?", client=client) == "the answer is 42"


def test_ask_passes_model_temperature_maxtokens() -> None:
    client = _fake_client("ok")
    connect.ask(
        "hi",
        model="gemini-3-flash",
        temperature=0.9,
        max_tokens=123,
        client=client,
    )
    kwargs = client.chat.completions.create.call_args.kwargs
    assert kwargs["model"] == "gemini-3-flash"
    assert kwargs["temperature"] == 0.9
    assert kwargs["max_tokens"] == 123


def test_ask_md_true_renders_and_returns_none() -> None:
    client = _fake_client("# heading")
    with patch.object(connect, "_display_markdown") as mock_display:
        result = connect.ask("hi", md=True, client=client)
    assert result is None
    mock_display.assert_called_once_with("# heading")


def test_ask_json_returns_parsed_dict() -> None:
    client = _fake_client('{"x": 1, "y": "two"}')
    out = connect.ask_json("give me json", client=client)
    assert out == {"x": 1, "y": "two"}
    kwargs = client.chat.completions.create.call_args.kwargs
    assert kwargs["response_format"] == {"type": "json_object"}


def test_ask_json_disables_thinking_via_chat_template_kwargs() -> None:
    client = _fake_client('{"x": 1}')
    connect.ask_json("give me json", client=client)
    kwargs = client.chat.completions.create.call_args.kwargs
    assert kwargs["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}


def test_ask_json_with_pydantic_schema() -> None:
    from pydantic import BaseModel

    class Reply(BaseModel):
        answer: str
        confidence: float

    client = _fake_client('{"answer": "yes", "confidence": 0.8}')
    out = connect.ask_json("question?", schema=Reply, client=client)
    assert isinstance(out, Reply)
    assert out.answer == "yes"
    assert out.confidence == 0.8


def test_ask_json_appends_schema_hint_for_pydantic() -> None:
    from pydantic import BaseModel

    class Reply(BaseModel):
        answer: str

    client = _fake_client('{"answer": "yes"}')
    connect.ask_json("question?", schema=Reply, client=client)
    user_msg = client.chat.completions.create.call_args.kwargs["messages"][-1]
    assert "JSON object that matches" in user_msg["content"]


def test_list_models_sorts_and_labels() -> None:
    client = MagicMock()
    client.models.list.return_value = SimpleNamespace(
        data=[
            SimpleNamespace(id="gemini-3-flash"),
            SimpleNamespace(id="api-llama-4-scout"),
            SimpleNamespace(id="text-embed-3-small"),
            SimpleNamespace(id="mistral-ocr-latest"),
        ]
    )
    result = connect.list_models(client=client)
    assert [m["id"] for m in result] == [
        "api-llama-4-scout",
        "gemini-3-flash",
        "mistral-ocr-latest",
        "text-embed-3-small",
    ]
    labels = {m["id"]: m["type"] for m in result}
    assert labels["api-llama-4-scout"] == "chat"
    assert labels["gemini-3-flash"] == "chat"
    assert labels["text-embed-3-small"] == "embeddings"
    assert labels["mistral-ocr-latest"] == "ocr"


def test_md_calls_display_markdown() -> None:
    with patch.object(connect, "_display_markdown") as mock_display:
        connect.md("# hello")
    mock_display.assert_called_once_with("# hello")


def test_module_constants_expose_supported_models() -> None:
    assert "api-llama-4-scout" in connect.CHEAP_MODELS
    assert "gemini-3-flash" in connect.CHEAP_MODELS
    assert "gpt-5.4" in connect.EXPENSIVE_MODELS
    assert "claude-opus-4-6-v1" in connect.EXPENSIVE_MODELS
    assert "oauth-gpt" in connect.OAUTH_MODELS
    assert connect.BASE_URL == "https://tritonai-api.ucsd.edu/v1"


# ------------------------------------------------------------------
# OAuth routing — mocked, offline
# ------------------------------------------------------------------


def test_ask_routes_oauth_gpt_model_through_oauth(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []

    def fake_oauth(text, *, model, instructions, **kw):
        calls.append({"text": text, "model": model, "instructions": instructions})
        return "oauth reply"

    monkeypatch.setattr("utils.oauth_gpt.ask_gpt_with_oauth", fake_oauth)
    result = connect.ask("What is 2+2?", model="oauth-gpt")
    assert result == "oauth reply"
    assert len(calls) == 1
    assert calls[0]["model"] == connect.OAUTH_BACKEND_MODEL
    assert calls[0]["text"] == "What is 2+2?"
    assert calls[0]["instructions"] == connect.DEFAULT_SYSTEM


def test_ask_oauth_flattens_messages_list(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []

    def fake_oauth(text, *, model, instructions, **kw):
        calls.append({"text": text, "instructions": instructions})
        return "ok"

    monkeypatch.setattr("utils.oauth_gpt.ask_gpt_with_oauth", fake_oauth)
    msgs = [
        {"role": "system", "content": "You are a helpful sales rep."},
        {"role": "user", "content": "How much?"},
        {"role": "assistant", "content": "$100"},
        {"role": "user", "content": "Too much."},
    ]
    connect.ask(msgs, model="oauth-gpt")
    assert calls[0]["instructions"] == "You are a helpful sales rep."
    # System message is extracted into instructions; transcript is role-labelled
    transcript = calls[0]["text"]
    assert "USER: How much?" in transcript
    assert "ASSISTANT: $100" in transcript
    assert "USER: Too much." in transcript
    assert "SYSTEM:" not in transcript


def test_ask_json_routes_oauth_gpt_model_through_oauth(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[dict] = []

    def fake_oauth(text, *, model, instructions, **kw):
        captured.append({"text": text, "instructions": instructions})
        return '{"x": 1, "y": "ok"}'

    monkeypatch.setattr("utils.oauth_gpt.ask_gpt_with_oauth", fake_oauth)
    result = connect.ask_json("give me json", model="oauth-gpt")
    assert result == {"x": 1, "y": "ok"}
    # JSON-only instruction should be appended to the system
    assert "JSON object" in captured[0]["instructions"]


def test_ask_json_oauth_with_pydantic_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    from pydantic import BaseModel

    class Reply(BaseModel):
        answer: str

    def fake_oauth(text, **kw):
        assert "JSON object that matches" in text   # schema hint appended
        return '{"answer": "yes"}'

    monkeypatch.setattr("utils.oauth_gpt.ask_gpt_with_oauth", fake_oauth)
    result = connect.ask_json("q?", schema=Reply, model="oauth-gpt")
    assert isinstance(result, Reply)
    assert result.answer == "yes"


# ------------------------------------------------------------------
# system + temperature — same parameters on both paths
# ------------------------------------------------------------------


def test_ask_oauth_forwards_custom_system_as_instructions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict] = []

    def fake_oauth(text, *, model, instructions, **kw):
        captured.append({"text": text, "instructions": instructions})
        return "ok"

    monkeypatch.setattr("utils.oauth_gpt.ask_gpt_with_oauth", fake_oauth)
    connect.ask("hi", model="oauth-gpt", system="Speak like a pirate.")
    assert captured[0]["instructions"] == "Speak like a pirate."


def test_ask_oauth_accepts_temperature_without_forwarding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OAuth endpoint has no temperature — we accept the kwarg silently."""
    captured_kwargs: list[dict] = []

    def fake_oauth(text, **kw):
        captured_kwargs.append(kw)
        return "ok"

    monkeypatch.setattr("utils.oauth_gpt.ask_gpt_with_oauth", fake_oauth)
    # Should not raise even with non-default temperature
    connect.ask("hi", model="oauth-gpt", temperature=0.9)
    assert len(captured_kwargs) == 1
    assert "temperature" not in captured_kwargs[0]   # not forwarded to the API


def test_ask_json_triton_forwards_system_and_temperature() -> None:
    client = _fake_client('{"ok": true}')
    connect.ask_json(
        "give me json",
        model="api-llama-4-scout",
        system="You are a strict schema robot.",
        temperature=0.1,
        client=client,
    )
    kwargs = client.chat.completions.create.call_args.kwargs
    assert kwargs["temperature"] == 0.1
    assert kwargs["messages"][0] == {
        "role": "system",
        "content": "You are a strict schema robot.",
    }


def test_ask_json_oauth_forwards_system_and_appends_json_instruction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict] = []

    def fake_oauth(text, *, model, instructions, **kw):
        captured.append({"text": text, "instructions": instructions, "kwargs": kw})
        return '{"ok": true}'

    monkeypatch.setattr("utils.oauth_gpt.ask_gpt_with_oauth", fake_oauth)
    connect.ask_json(
        "give me json",
        model="oauth-gpt",
        system="You are a strict schema robot.",
    )
    instr = captured[0]["instructions"]
    assert instr.startswith("You are a strict schema robot.")
    assert "Return ONLY a valid JSON object" in instr


def test_ask_json_oauth_accepts_temperature_without_forwarding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_kwargs: list[dict] = []

    def fake_oauth(text, **kw):
        captured_kwargs.append(kw)
        return '{"ok": true}'

    monkeypatch.setattr("utils.oauth_gpt.ask_gpt_with_oauth", fake_oauth)
    connect.ask_json("give me json", model="oauth-gpt", temperature=0.9)
    assert len(captured_kwargs) == 1
    assert "temperature" not in captured_kwargs[0]


# ------------------------------------------------------------------
# Model identity / no-fallback guarantees + verbose reporting
# ------------------------------------------------------------------


def test_describe_model_for_triton_and_oauth() -> None:
    """describe_model() tells the truth about where a call will go."""
    triton_desc = connect.describe_model("api-llama-4-scout")
    assert "api-llama-4-scout" in triton_desc
    assert "TritonAI" in triton_desc
    assert connect.BASE_URL in triton_desc

    oauth_desc = connect.describe_model("oauth-gpt")
    assert "oauth-gpt" in oauth_desc
    assert "OAuth" in oauth_desc
    assert connect.OAUTH_BACKEND_MODEL in oauth_desc


def test_ask_verbose_reports_triton_route_and_server_model(
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = MagicMock()
    client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="hi"))],
        model="api-llama-4-scout",          # server echoes the model
    )
    connect.ask("hello", model="api-llama-4-scout", verbose=True, client=client)
    err = capsys.readouterr().err
    assert "api-llama-4-scout" in err
    assert "TritonAI" in err
    assert "server reported model: 'api-llama-4-scout'" in err


def test_ask_verbose_reports_oauth_route(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("utils.oauth_gpt.ask_gpt_with_oauth", lambda *a, **k: "ok")
    connect.ask("hello", model="oauth-gpt", verbose=True)
    err = capsys.readouterr().err
    assert "oauth-gpt" in err
    assert "OAuth" in err
    assert connect.OAUTH_BACKEND_MODEL in err       # mapped backend is disclosed


def test_ask_quiet_by_default_prints_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    client = _fake_client("ok")
    connect.ask("hi", client=client)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_ask_does_not_fallback_when_triton_errors() -> None:
    """If the API rejects the model, the error propagates — no silent switch."""
    client = MagicMock()
    client.chat.completions.create.side_effect = RuntimeError(
        "404 model not found: pretend-model"
    )
    with pytest.raises(RuntimeError, match="404 model not found"):
        connect.ask("hi", model="pretend-model", client=client)
    # Confirm the broken model was actually what got attempted (not a fallback)
    assert client.chat.completions.create.call_args.kwargs["model"] == "pretend-model"


def test_ask_does_not_fallback_when_oauth_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*_a, **_kw):
        raise RuntimeError("HTTP 401: team not allowed to access model")

    monkeypatch.setattr("utils.oauth_gpt.ask_gpt_with_oauth", fail)
    with pytest.raises(RuntimeError, match="team not allowed"):
        connect.ask("hi", model="oauth-gpt")


def test_ask_json_does_not_fallback_when_oauth_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*_a, **_kw):
        raise RuntimeError("boom")

    monkeypatch.setattr("utils.oauth_gpt.ask_gpt_with_oauth", fail)
    with pytest.raises(RuntimeError, match="boom"):
        connect.ask_json("give me json", model="oauth-gpt")


# ------------------------------------------------------------------
# Integration test — hits the real TritonAI API. Skipped by default.
# Run with: uv run pytest -m integration
# ------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(
    not os.environ.get("TRITONAI_API_KEY"),
    reason="TRITONAI_API_KEY not set",
)
def test_ask_hits_real_api() -> None:
    reply = connect.ask(
        "Reply with just the word 'pong'.",
        model="api-llama-4-scout",
        max_tokens=10,
        temperature=0.0,
    )
    assert isinstance(reply, str)
    assert len(reply) > 0
