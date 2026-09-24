"""Checks that run before any agent sees the request.

Pydantic (api.py) handles types, enums and bounds. This module handles what a
schema cannot: text normalisation, unsafe or injected content, contradictory
classroom constraints, and topics the source chapter does not cover.
"""

import re
import unicodedata

import config
from errors import (
    IncompleteConstraintsError,
    OffTopicError,
    UnsafeTopicError,
)
from logging_setup import log_event

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f​-‏ -‮﻿]")
_WS_RE = re.compile(r"\s+")

_UNSAFE = {
    name: re.compile(pattern, re.IGNORECASE)
    for name, pattern in config.UNSAFE_PATTERNS.items()
}
_INJECTION = [re.compile(p, re.IGNORECASE | re.MULTILINE) for p in config.INJECTION_PATTERNS]


def sanitize_text(value):
    """NFKC-normalise, drop control/zero-width characters, collapse whitespace."""
    if value is None:
        return value
    value = unicodedata.normalize("NFKC", value)
    value = _CONTROL_RE.sub("", value)
    return _WS_RE.sub(" ", value).strip()


def check_safety(fields):
    """Reject unsafe or prompt-injection text. `fields` maps a dotted field
    name to its text. The matched term is logged by category only and never
    echoed back to the user."""
    problems = []
    categories = []
    for field, text in fields.items():
        if not text:
            continue
        for pattern in _INJECTION:
            if pattern.search(text):
                problems.append(
                    {
                        "field": field,
                        "message": "This looks like an instruction to the AI rather than lesson information. Please describe the lesson only.",
                    }
                )
                categories.append((field, "prompt_injection"))
                break
        else:
            for category, pattern in _UNSAFE.items():
                if pattern.search(text):
                    problems.append(
                        {
                            "field": field,
                            "message": "This contains content that is not appropriate for a Class 7 lesson plan.",
                        }
                    )
                    categories.append((field, category))
                    break

    if problems:
        log_event(
            "validation.rejected",
            "validator",
            code=UnsafeTopicError.code,
            findings=[{"field": f, "category": c} for f, c in categories],
        )
        raise UnsafeTopicError(
            "Some of the lesson details can't be used. See the highlighted fields.",
            details=problems,
        )


def check_consistency(req):
    """Catch contradictory or incomplete classroom constraints. Returns a list
    of non-blocking warnings; raises for contradictions that would make the
    plan meaningless."""
    problems = []
    warnings = []
    tech = req.technology

    if tech.infrastructure == "1to1" and tech.student_devices == "none":
        problems.append(
            {
                "field": "technology.student_devices",
                "message": "1:1 student devices was chosen as the setup, but student devices is set to none.",
            }
        )
    if tech.infrastructure == "lab" and tech.student_devices == "none":
        problems.append(
            {
                "field": "technology.student_devices",
                "message": "A computer lab was chosen as the setup, but student devices is set to none.",
            }
        )
    # Only a contradiction when both are explicitly "no"; None means not specified.
    if (
        tech.infrastructure == "projector"
        and tech.projector_available is False
        and tech.smart_board_available is False
    ):
        problems.append(
            {
                "field": "technology.projector_available",
                "message": "A projector / smart board setup was chosen, but neither is marked as available.",
            }
        )
    if tech.tools and tech.internet_access == "none":
        warnings.append(
            "Tools were listed but there is no internet access; online tools will be marked unavailable."
        )

    chapter_nums = {info["num"] for info in config.CHAPTERS.values()}
    if req.content.chapter_num not in chapter_nums:
        problems.append(
            {
                "field": "content.chapter_num",
                "message": f"Chapter {req.content.chapter_num} has not been loaded. Available: {sorted(chapter_nums)}.",
            }
        )

    if problems:
        log_event(
            "validation.rejected",
            "validator",
            code=IncompleteConstraintsError.code,
            fields=[p["field"] for p in problems],
        )
        raise IncompleteConstraintsError(
            "Some classroom details contradict each other. See the highlighted fields.",
            details=problems,
        )
    return warnings


def check_relevance(chunks, chapter_num):
    """Reject topics the chapter does not cover, judged by retrieval distance."""
    best = min((c["distance"] for c in chunks), default=None)
    if best is None or best > config.RELEVANCE_MAX_DISTANCE:
        log_event(
            "validation.rejected",
            "validator",
            code=OffTopicError.code,
            best_distance=best,
            threshold=config.RELEVANCE_MAX_DISTANCE,
        )
        raise OffTopicError(
            f"This topic doesn't appear to be covered by Chapter {chapter_num}. "
            "Try a topic from the chapter, or check the spelling.",
            details=[{"field": "content.topic", "message": "Not found in the source chapter."}],
        )
    return best


def chapter_name(chapter_num):
    for info in config.CHAPTERS.values():
        if info["num"] == chapter_num:
            return info["name"]
    return None
