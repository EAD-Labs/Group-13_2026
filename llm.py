"""Gemini call through its OpenAI-compatible endpoint."""

import json
import logging
import os
import time

import openai
from dotenv import load_dotenv
from openai import OpenAI

import config
from errors import AgentOutputError, LLMError
from logging_setup import log_event

load_dotenv()

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
# gemini-2.0-flash was retired (404 as of 2026-09); this is the replacement
# Google's error message names.
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
# Tried in order when MODEL stays overloaded after its retries.
FALLBACK_MODELS = [
    m.strip()
    for m in os.getenv(
        "GEMINI_FALLBACK_MODELS", "gemini-3.5-flash,gemini-flash-latest,gemini-3.1-flash-lite"
    ).split(",")
    if m.strip() and m.strip() != MODEL
]

# Overload (503/500/529) and rate limits (429) are usually brief spikes.
_TRANSIENT_STATUS = {429, 500, 502, 503, 504, 529}

_client = None


def get_client():
    """Build the client on first use so a missing key fails a request, not import."""
    global _client
    if _client is None:
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise LLMError("The AI service is not configured on the server (GEMINI_API_KEY is missing).")
        _client = OpenAI(
            api_key=api_key,
            base_url=GEMINI_BASE_URL,
            timeout=config.LLM_TIMEOUT_S,
            # Retries are done in _complete() so each one is logged and can
            # back off longer than the client's built-in retry would.
            max_retries=0,
        )
    return _client


def _complete(prompt, actor):
    """Call the model, retrying transient failures with backoff and then
    falling back to the next model. Returns (response, model_used)."""
    last_reason = None
    for model in [MODEL] + FALLBACK_MODELS:
        for attempt, delay in enumerate((0,) + tuple(config.LLM_RETRY_BACKOFF_S)):
            if delay:
                time.sleep(delay)
            try:
                response = get_client().chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.3,
                    response_format={"type": "json_object"},
                )
                return response, model
            except openai.AuthenticationError as e:
                _log_llm_error(actor, "auth", e, model)
                raise LLMError("The AI service rejected the server's credentials.") from e
            except openai.NotFoundError as e:
                _log_llm_error(actor, "model_not_found", e, model)
                last_reason = "model_not_found"
                break  # this model will never work; try the next one
            except openai.APITimeoutError as e:
                # Waiting LLM_TIMEOUT_S again on the same model rarely helps.
                _log_llm_error(actor, "timeout", e, model)
                last_reason = "timeout"
                break
            except openai.APIStatusError as e:
                if e.status_code not in _TRANSIENT_STATUS:
                    _log_llm_error(actor, "api_error", e, model)
                    raise LLMError("The AI service returned an error. Please try again.") from e
                last_reason = "rate_limited" if e.status_code == 429 else "overloaded"
                _log_retry(actor, model, attempt, last_reason, e)
                if e.status_code == 429:
                    # Quotas are per model and per minute or day, so waiting
                    # a few seconds on the same model rarely helps.
                    break
            except openai.APIConnectionError as e:
                last_reason = "connection"
                _log_retry(actor, model, attempt, last_reason, e)

    log_event(
        "agent.error",
        actor,
        level=logging.ERROR,
        reason="llm_exhausted",
        last_reason=last_reason,
        models=[MODEL] + FALLBACK_MODELS,
    )
    if last_reason == "model_not_found":
        raise LLMError(f"None of the configured AI models ({', '.join([MODEL] + FALLBACK_MODELS)}) are available.")
    if last_reason == "timeout":
        raise LLMError("The AI service took too long to respond. Please try again.")
    if last_reason == "connection":
        raise LLMError("The AI service could not be reached. Please try again.")
    raise LLMError("The AI service is overloaded right now. Please try again in a minute.")


def call_agent(prompt: str, actor: str = "llm"):
    """Send one prompt, expect a JSON object back.

    Returns (parsed_dict, usage) where usage is {"model", "prompt_tokens",
    "completion_tokens"} (token counts only if the endpoint reported them).
    """
    response, model = _complete(prompt, actor)

    raw = response.choices[0].message.content or ""
    text = raw.strip()

    # Some models wrap JSON in a fenced block despite response_format.
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        if text.endswith("```"):
            text = text[: -len("```")]
        text = text.strip()

    if config.LOG_FULL_PAYLOADS:
        log_event("llm.raw_response", actor, level=logging.DEBUG, raw=raw)

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        log_event("agent.error", actor, level=logging.ERROR, reason="invalid_json", error=str(e), raw_preview=raw[:500])
        raise AgentOutputError("The AI returned a response that could not be read. Please try again.") from e

    if not isinstance(parsed, dict):
        log_event("agent.error", actor, level=logging.ERROR, reason="not_an_object", raw_preview=raw[:500])
        raise AgentOutputError("The AI returned a response in the wrong format. Please try again.")

    usage = {"model": model}
    if getattr(response, "usage", None):
        usage["prompt_tokens"] = response.usage.prompt_tokens
        usage["completion_tokens"] = response.usage.completion_tokens
    return parsed, usage


def _log_llm_error(actor, reason, exc, model=None):
    log_event(
        "agent.error",
        actor,
        level=logging.ERROR,
        reason=reason,
        model=model,
        error_type=type(exc).__name__,
        error=str(exc)[:500],
    )


def _log_retry(actor, model, attempt, reason, exc):
    log_event(
        "llm.retry",
        actor,
        level=logging.WARNING,
        model=model,
        attempt=attempt + 1,
        reason=reason,
        status=getattr(exc, "status_code", None),
    )
