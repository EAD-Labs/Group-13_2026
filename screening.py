"""Content screening against prompts/guidelines.md.

screen_input: one Gemini call over everything the teacher typed, after the
cheap checks and retrieval but before any lesson agent runs. Flagged fields
stop the request with a 422 (content_flagged) naming each field.

screen_output: one Gemini call over a generated plan or materials. If it
flags anything, the generating agent rewrites once with the findings. If the
rewrite is still flagged, the flagged items are removed and the caller warns
the teacher; if removal leaves an invalid lesson, the request fails instead.

Both fail closed: a screening agent that errors stops the request.
"""

import json
import logging

from pydantic import ValidationError

from agents import INPUT_SCREEN, OUTPUT_SCREEN, run_agent
from errors import AgentOutputError, ContentFlaggedError
from logging_setup import log_event

FLAGGED_MESSAGE = (
    "Some of the lesson details can't be used because they are not appropriate "
    "or not in scope for a Class 7 lesson. See the highlighted fields."
)
REMOVED_WARNING = (
    "Some content was removed because it did not meet the content guidelines. "
    "Read this carefully before you use it."
)

_REVIEW_NOTE = """

## Your previous answer was rejected by the content review

It was checked against the content guidelines above and these parts broke them:

{issues}

Write the complete answer again, in exactly the required JSON shape, without these
problems and without anything else that breaks the content guidelines.
"""


def screen_input(fields, grade, chapter):
    """Check the teacher's free-text fields ({dotted name: text}). Returns
    the agent step; raises ContentFlaggedError if anything is flagged."""
    data = {k: v for k, v in fields.items() if v}
    result, step = run_agent(
        INPUT_SCREEN,
        {"grade": grade, "chapter": chapter, "teacher_input": json.dumps(data, ensure_ascii=False, indent=2)},
    )
    issues = result["issues"]
    if not issues:
        log_event("screen.passed", INPUT_SCREEN.actor, stage="input", fields=sorted(data))
        return step

    # Categories only on the event; the reasons go to the session file.
    log_event(
        "validation.rejected",
        INPUT_SCREEN.actor,
        level=logging.WARNING,
        code=ContentFlaggedError.code,
        findings=[{"field": i["field"], "category": i["category"]} for i in issues],
        detail={"issues": issues},
    )
    raise ContentFlaggedError(
        FLAGGED_MESSAGE,
        details=[{"field": i["field"] if i["field"] in data else None, "message": i["reason"]} for i in issues],
    )


def screen_output(spec, output, steps, *, schema, grade, topic, kind, regenerate):
    """Review `output` from agent `spec`; `regenerate(note)` reruns that agent
    and returns (output, step). Appends every agent step to `steps`.

    Returns (output, removed) where `removed` is the number of flagged items
    taken out after the rewrite was still flagged (0 when it passed).
    """
    issues = _review(spec, output, steps, grade, topic, kind)
    if not issues:
        return output, 0

    output, step = regenerate(_REVIEW_NOTE.format(issues=_format_issues(issues)))
    steps.append(step)
    issues = _review(spec, output, steps, grade, topic, kind)
    if not issues:
        return output, 0

    removed, unresolved = remove_paths(output, [i["path"] for i in issues])
    try:
        if unresolved:
            raise ValueError(f"{unresolved} flagged item(s) could not be located or removed")
        output = schema.model_validate(output).model_dump()
    except (ValidationError, ValueError) as e:
        log_event(
            "agent.error",
            OUTPUT_SCREEN.actor,
            level=logging.ERROR,
            reason="unremovable_content",
            stage=spec.name,
            error=str(e)[:1000],
        )
        raise AgentOutputError(
            "The generated content did not pass the content review. Please try again or rephrase the request."
        ) from e
    log_event("content.removed", OUTPUT_SCREEN.actor, stage=spec.name, removed=removed, detail={"issues": issues})
    return output, removed


def _review(spec, output, steps, grade, topic, kind):
    review, step = run_agent(
        OUTPUT_SCREEN,
        {
            "grade": grade,
            "topic": topic,
            "kind": kind,
            "content_json": json.dumps(output, ensure_ascii=False, indent=2),
        },
        upstream={spec.name: output},
    )
    steps.append(step)
    issues = review["issues"]
    if issues:
        log_event(
            "screen.flagged",
            OUTPUT_SCREEN.actor,
            level=logging.WARNING,
            stage=spec.name,
            count=len(issues),
            paths=[_path_text(i["path"]) for i in issues],
            detail={"issues": issues},
        )
    else:
        log_event("screen.passed", OUTPUT_SCREEN.actor, stage=spec.name)
    return issues


def _format_issues(issues):
    return "\n".join(f"- {_path_text(i['path'])}: {i['problem']}" for i in issues)


def _path_text(path):
    return "".join(f"[{p}]" if isinstance(p, int) or str(p).isdigit() else f".{p}" for p in path).lstrip(".")


# --- Removing flagged items ------------------------------------------------------

_GONE = object()


def remove_paths(root, paths):
    """Remove the items at `paths` (lists of keys and indices) from `root` in
    place. A list item is deleted; a string field is emptied; a list field is
    emptied. Returns (removed, unresolved). Paths inside an item that was
    already removed count as removed."""
    removed = unresolved = 0
    seen = set()
    for path in sorted(paths, key=len):  # parents before children
        key = tuple(str(p) for p in path)
        if key in seen:
            continue
        seen.add(key)
        found = _locate(root, path)
        if found is _GONE:
            continue
        if found is None:
            unresolved += 1
            continue
        parent, k = found
        target = parent[k]
        if isinstance(parent, list):
            parent[k] = _GONE
        elif isinstance(target, list):
            parent[k] = []
        elif isinstance(target, str):
            parent[k] = ""
        else:
            unresolved += 1
            continue
        removed += 1
    _sweep(root)
    return removed, unresolved


def _locate(root, path):
    """(parent, key) of the item at `path`, _GONE if an ancestor was already
    removed, or None if the path does not exist."""
    node, parent, key = root, None, None
    for step in path:
        if node is _GONE:
            return _GONE
        if isinstance(node, list):
            try:
                index = int(step)
            except (TypeError, ValueError):
                return None
            if not 0 <= index < len(node):
                return None
            parent, key, node = node, index, node[index]
        elif isinstance(node, dict):
            if not isinstance(step, str) or step not in node:
                return None
            parent, key, node = node, step, node[step]
        else:
            return None
    if node is _GONE:
        return _GONE
    return (parent, key) if parent is not None else None


def _sweep(node):
    if isinstance(node, dict):
        for value in node.values():
            _sweep(value)
    elif isinstance(node, list):
        node[:] = [v for v in node if v is not _GONE]
        for value in node:
            _sweep(value)
