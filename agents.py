"""Agent registry and runner.

Each agent is one prompt template plus either the keys its JSON output must
contain or a Pydantic schema it must match. run_agent() logs the agent's
lifecycle and every hand-off it receives from an upstream agent, which is how
agent-to-agent interaction shows up in the log.
"""

import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

from pydantic import ValidationError

import config
import llm
from errors import AgentOutputError
from lesson_schema import LessonMaterials, LessonPlan
from logging_setup import log_event


@dataclass(frozen=True)
class AgentSpec:
    name: str
    prompt_path: str
    required_keys: tuple = ()
    # When set, the output must validate against this model. One retry is
    # made with the validation errors appended to the prompt.
    schema: Optional[type] = None

    @property
    def actor(self):
        return f"agent:{self.name}"


CK = AgentSpec("ck", "prompts/ck_agent.md", ("blocks", "coverage_gaps"))
PK = AgentSpec(
    "pk",
    "prompts/pk_agent.md",
    ("approach_summary", "activity_sequence", "assessment", "missing_inputs"),
)
TK = AgentSpec(
    "tk",
    "prompts/tk_agent.md",
    ("technology_profile", "requested_tools_status", "missing_inputs"),
)
TPACK = AgentSpec("tpack", "prompts/tpack_agent.md", schema=LessonPlan)
MATERIALS = AgentSpec("materials", "prompts/tpack_materials_agent.md", schema=LessonMaterials)

ALL_AGENTS = (CK, PK, TK, TPACK, MATERIALS)

_HERE = os.path.dirname(os.path.abspath(__file__))
_PROMPTS = {}
for _spec in ALL_AGENTS:
    with open(os.path.join(_HERE, _spec.prompt_path), encoding="utf-8") as _f:
        _PROMPTS[_spec.name] = _f.read()

_RETRY_NOTE = """

## Your previous answer was rejected

It did not match the required JSON shape:

{errors}

Return the complete corrected JSON object, in exactly the required shape, and nothing else.
"""


def run_agent(spec, inputs, upstream=None):
    """Run one agent.

    `inputs` fills the prompt's placeholders. `upstream` maps an agent name to
    that agent's output; each one is logged as a hand-off and injected into
    the prompt as `{<name>_output}` (pretty-printed JSON).

    Returns (output_dict, step) where step is {"agent", "ms", "status"} for
    the response's agent_sequence. With a schema, output_dict is the
    validated model dumped back to a dict.
    """
    upstream = upstream or {}
    for source, output in upstream.items():
        payload = json.dumps(output, ensure_ascii=False)
        log_event(
            "agent.handoff",
            spec.actor,
            **{"from": f"agent:{source}", "to": spec.actor},
            payload_keys=sorted(output.keys()),
            payload_chars=len(payload),
            detail={"payload": output},
        )

    fill = dict(inputs)
    for source, output in upstream.items():
        fill[f"{source}_output"] = json.dumps(output, ensure_ascii=False, indent=2)

    try:
        prompt = _PROMPTS[spec.name].format(**fill)
    except (KeyError, IndexError) as e:
        log_event(
            "agent.error",
            spec.actor,
            level=logging.ERROR,
            reason="prompt_placeholder",
            missing=str(e),
        )
        raise AgentOutputError(
            f"The {spec.name.upper()} agent could not be prepared (prompt placeholder {e} has no value)."
        ) from e

    detail = {"inputs": inputs}
    if config.LOG_FULL_PAYLOADS:
        detail["prompt"] = prompt
    log_event(
        "agent.start",
        spec.actor,
        prompt_chars=len(prompt),
        upstream=[f"agent:{s}" for s in upstream],
        detail=detail,
    )

    started = time.perf_counter()
    try:
        if spec.schema is not None:
            output, usage = _call_with_schema(spec, prompt)
        else:
            output, usage = llm.call_agent(prompt, actor=spec.actor)
            _check_required_keys(spec, output)
    except Exception:
        log_event("agent.end", spec.actor, level=logging.ERROR, status="error", duration_ms=_elapsed_ms(started))
        raise

    ms = _elapsed_ms(started)
    log_event(
        "agent.end",
        spec.actor,
        status="ok",
        duration_ms=ms,
        output_keys=sorted(output.keys()),
        **usage,
        detail={"output": output},
    )
    return output, {"agent": spec.name, "ms": ms, "status": "ok"}


def _call_with_schema(spec, prompt):
    """Call the model and validate against spec.schema, retrying once with the
    errors if the answer is unreadable or the wrong shape."""
    feedback = None
    for attempt in (1, 2):
        full_prompt = prompt if feedback is None else prompt + _RETRY_NOTE.format(errors=feedback)
        try:
            raw, usage = llm.call_agent(full_prompt, actor=spec.actor)
            return spec.schema.model_validate(raw).model_dump(), usage
        except ValidationError as e:
            errors = _format_validation_errors(e)
            reason = "schema_mismatch"
        except AgentOutputError as e:  # invalid JSON, already logged by llm.py
            errors = f"- The response was not a valid JSON object ({e.message})"
            reason = "invalid_json"

        log_event(
            "agent.error",
            spec.actor,
            level=logging.WARNING if attempt == 1 else logging.ERROR,
            reason=reason,
            attempt=attempt,
            errors=errors[:1500],
        )
        feedback = errors

    raise AgentOutputError(
        f"The {spec.name.upper()} agent returned a lesson in the wrong format twice. Please try again."
    )


def _format_validation_errors(exc, limit=12):
    lines = []
    for err in exc.errors()[:limit]:
        loc = ".".join(str(p) for p in err.get("loc", ())) or "(root)"
        lines.append(f"- {loc}: {err.get('msg')}")
    if len(exc.errors()) > limit:
        lines.append(f"- ... and {len(exc.errors()) - limit} more")
    return "\n".join(lines)


def _check_required_keys(spec, output):
    missing = [k for k in spec.required_keys if k not in output]
    if missing:
        log_event(
            "agent.error",
            spec.actor,
            level=logging.ERROR,
            reason="missing_keys",
            missing=missing,
            output_keys=sorted(output.keys()),
        )
        raise AgentOutputError(
            f"The {spec.name.upper()} agent returned an incomplete answer (missing {', '.join(missing)}). Please try again."
        )


def _elapsed_ms(started):
    return round((time.perf_counter() - started) * 1000)
