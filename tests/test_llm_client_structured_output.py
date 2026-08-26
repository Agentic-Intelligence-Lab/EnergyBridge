from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from energybridge.llm import client as client_module
from energybridge.llm.client import LLMCallError, LLMClient
from energybridge.utils.config import LLMConfig


def _config() -> LLMConfig:
    return LLMConfig(
        use_llm=True,
        provider="openai_compatible",
        base_url="https://example.invalid/v1",
        api_key="test-only-key",
        model="test-model",
        temperature=0.3,
        max_tokens=256,
        timeout_seconds=5.0,
        api_key_pool=["test-only-key"],
    )


def _response(text: str, *, finish_reason: str = "stop") -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(content=text),
            finish_reason=finish_reason,
        )],
        usage=SimpleNamespace(
            prompt_tokens=10,
            completion_tokens=4,
            total_tokens=14,
        ),
    )


def _install_fake_openai(monkeypatch: pytest.MonkeyPatch, create) -> list[dict]:
    calls: list[dict] = []

    class FakeCompletions:
        def create(self, **kwargs):
            calls.append(dict(kwargs))
            return create(kwargs)

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(client_module, "OpenAI", FakeOpenAI)
    return calls


def test_json_response_format_is_transport_only_and_audited(monkeypatch) -> None:
    calls = _install_fake_openai(
        monkeypatch,
        lambda _kwargs: _response('{"choice":"model_owned"}'),
    )
    llm = LLMClient(config=_config())

    result = llm.chat_with_metrics(
        "system",
        "user",
        response_format={"type": "json_object"},
        validate_fn=lambda text: json.dumps(json.loads(text)),
    )

    assert json.loads(result["text"]) == {"choice": "model_owned"}
    assert calls[0]["response_format"] == {"type": "json_object"}
    assert result["metrics"]["response_format_requested"] == "json_object"
    assert result["metrics"]["response_format_active"] is True
    assert result["metrics"]["response_format_fallback"] is False
    assert result["metrics"]["validation_failures"] == 0


def test_unsupported_json_mode_downgrades_without_changing_prompt(monkeypatch) -> None:
    def create(kwargs: dict):
        if "response_format" in kwargs:
            raise ValueError("response_format json_object is unsupported")
        return _response('{"ok":true}')

    calls = _install_fake_openai(monkeypatch, create)
    llm = LLMClient(config=_config())

    result = llm.chat_with_metrics(
        "same system",
        "same user",
        response_format={"type": "json_object"},
    )

    assert len(calls) == 2
    assert calls[0]["messages"] == calls[1]["messages"]
    assert "response_format" in calls[0]
    assert "response_format" not in calls[1]
    assert result["metrics"]["response_format_active"] is False
    assert result["metrics"]["response_format_fallback"] is True
    assert result["metrics"]["response_format_fallback_type"] == "ValueError"
    assert result["metrics"]["retries"] == 0


def test_validator_failure_is_counted_separately_from_provider_retry(monkeypatch) -> None:
    responses = iter([
        _response("not-json", finish_reason="length"),
        _response('{"ok":true}'),
    ])
    calls = _install_fake_openai(monkeypatch, lambda _kwargs: next(responses))
    llm = LLMClient(config=_config())

    result = llm.chat_with_metrics(
        "system",
        "user",
        max_retries=2,
        retry_base_delay=0.0,
        response_format={"type": "json_object"},
        validate_fn=lambda text: json.dumps(json.loads(text)),
    )

    assert len(calls) == 2
    assert all(call["response_format"] == {"type": "json_object"} for call in calls)
    assert result["metrics"]["retries"] == 1
    assert result["metrics"]["validation_failures"] == 1
    assert result["metrics"]["provider_failures"] == 0
    assert result["metrics"]["length_truncation_failures"] == 1
    assert result["metrics"]["attempt_finish_reasons"] == ["length", "stop"]
    assert result["metrics"]["token_usage"] == {
        "prompt_tokens": 20,
        "completion_tokens": 8,
        "total_tokens": 28,
    }


def test_exhausted_call_raises_safe_error_with_failure_telemetry(monkeypatch) -> None:
    _install_fake_openai(
        monkeypatch,
        lambda _kwargs: _response("not-json", finish_reason="length"),
    )
    llm = LLMClient(config=_config())

    with pytest.raises(LLMCallError) as caught:
        llm.chat_with_metrics(
            "system",
            "user",
            max_retries=2,
            retry_base_delay=0.0,
            response_format={"type": "json_object"},
            validate_fn=lambda text: json.dumps(json.loads(text)),
        )

    error = caught.value
    assert "test-only-key" not in str(error)
    assert error.failure_type == "JSONDecodeError"
    assert error.metrics["used"] is False
    assert error.metrics["exhausted"] is True
    assert error.metrics["exhausted_calls"] == 1
    assert error.metrics["attempts"] == 2
    assert error.metrics["validation_failures"] == 2
    assert error.metrics["length_truncation_failures"] == 2
    assert error.metrics["token_usage"]["total_tokens"] == 28
