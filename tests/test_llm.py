"""Retry and fallback behaviour of llm.call_agent, with a scripted fake client."""

import types

import httpx
import openai
import pytest

import llm
from errors import LLMError


def _status_error(cls, status):
    request = httpx.Request("POST", "https://example.test")
    return cls("boom", response=httpx.Response(status, request=request), body=None)


def _ok(content='{"a": 1}'):
    message = types.SimpleNamespace(content=content)
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(message=message)],
        usage=types.SimpleNamespace(prompt_tokens=3, completion_tokens=2),
    )


@pytest.fixture
def scripted(monkeypatch):
    """script(outcomes) -> list of models called. Each outcome is either an
    exception to raise or a response to return, consumed in order."""
    sleeps = []
    monkeypatch.setattr(llm.time, "sleep", sleeps.append)
    monkeypatch.setattr(llm, "MODEL", "primary")
    monkeypatch.setattr(llm, "FALLBACK_MODELS", ["backup"])

    def script(outcomes):
        calls = []

        def create(model, **kwargs):
            calls.append(model)
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create)))
        monkeypatch.setattr(llm, "get_client", lambda: client)
        return calls

    script.sleeps = sleeps
    return script


def test_overload_then_success_on_same_model(scripted, events):
    calls = scripted([_status_error(openai.InternalServerError, 503), _ok()])
    parsed, usage = llm.call_agent("p", actor="agent:pk")
    assert parsed == {"a": 1}
    assert calls == ["primary", "primary"]
    assert usage["model"] == "primary"
    assert scripted.sleeps == [2]
    retries = [e for e in events if e["event"] == "llm.retry"]
    assert retries[0]["reason"] == "overloaded" and retries[0]["status"] == 503


def test_falls_back_after_primary_exhausted(scripted):
    overload = [_status_error(openai.InternalServerError, 503) for _ in range(4)]
    calls = scripted(overload + [_ok()])
    _, usage = llm.call_agent("p")
    assert calls == ["primary"] * 4 + ["backup"]
    assert usage["model"] == "backup"


def test_retired_model_skips_straight_to_fallback(scripted):
    calls = scripted([_status_error(openai.NotFoundError, 404), _ok()])
    _, usage = llm.call_agent("p")
    assert calls == ["primary", "backup"]
    assert usage["model"] == "backup"
    assert scripted.sleeps == []


def test_rate_limit_moves_to_next_model_without_waiting(scripted, events):
    calls = scripted([_status_error(openai.RateLimitError, 429), _ok()])
    _, usage = llm.call_agent("p")
    assert calls == ["primary", "backup"]
    assert usage["model"] == "backup"
    assert scripted.sleeps == []
    assert [e["reason"] for e in events if e["event"] == "llm.retry"] == ["rate_limited"]


def test_everything_overloaded_raises_friendly_error(scripted):
    scripted([_status_error(openai.InternalServerError, 503) for _ in range(8)])
    with pytest.raises(LLMError) as exc:
        llm.call_agent("p")
    assert "overloaded" in exc.value.message


def test_auth_error_is_not_retried(scripted):
    calls = scripted([_status_error(openai.AuthenticationError, 401)])
    with pytest.raises(LLMError):
        llm.call_agent("p")
    assert calls == ["primary"]


def test_bad_request_is_not_retried(scripted):
    calls = scripted([_status_error(openai.BadRequestError, 400)])
    with pytest.raises(LLMError):
        llm.call_agent("p")
    assert calls == ["primary"]
