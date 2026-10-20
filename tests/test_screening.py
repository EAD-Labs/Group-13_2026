"""Content screening (screening.py): guidelines reach every agent, flagged
input stops the request, flagged output is rewritten once and then removed.
All LLM output is mocked."""

import copy

import pytest

import agents
import llm
from conftest import FAKE_OUTPUTS, valid_request
from errors import AgentOutputError
from screening import REMOVED_WARNING, remove_paths

PLAN = FAKE_OUTPUTS["agent:tpack"]
MATERIALS = FAKE_OUTPUTS["agent:materials"]
CLEAN = {"verdict": "clean", "issues": []}


@pytest.fixture
def scripted(monkeypatch):
    """script({actor: [outcome, ...]}) makes each listed actor return (or
    raise) its outcomes in turn; every other agent uses the normal fakes.
    Returns {"calls": [actor, ...], "prompts": {actor: [prompt, ...]}}."""
    record = {"calls": [], "prompts": {}}

    def script(outcomes):
        queues = {a: list(o) for a, o in outcomes.items()}

        def fake(prompt, actor="llm"):
            record["calls"].append(actor)
            record["prompts"].setdefault(actor, []).append(prompt)
            if actor in queues:
                outcome = queues[actor].pop(0)
                if isinstance(outcome, Exception):
                    raise outcome
                return copy.deepcopy(outcome), {}
            return copy.deepcopy(FAKE_OUTPUTS[actor]), {}

        monkeypatch.setattr(llm, "call_agent", fake)
        monkeypatch.setattr("run.retrieve", lambda topic, chapter_num=None: copy.deepcopy(_chunks()))
        return record

    return script


def _chunks():
    from conftest import FAKE_CHUNKS
    return FAKE_CHUNKS


def _flag(*paths, problem="Asks students to taste an unknown liquid."):
    return {"verdict": "flagged", "issues": [{"path": list(p), "problem": problem} for p in paths]}


# --- Guidelines reach every agent ------------------------------------------------


def test_every_prompt_has_the_guidelines_placeholder():
    for spec in agents.ALL_AGENTS:
        assert "{guidelines}" in agents._PROMPTS[spec.name], spec.name


def test_guidelines_are_sent_to_every_agent(client, scripted):
    rec = scripted({})
    assert client.post("/api/generate", json=valid_request()).status_code == 200
    plan = client.post("/api/generate", json=valid_request()).json()["lesson"]
    plan.pop("missing_details")
    client.post("/api/materials", json={"request": valid_request(), "plan": plan})
    assert set(rec["prompts"]) == {s.actor for s in agents.ALL_AGENTS}
    for actor, prompts in rec["prompts"].items():
        assert all("Legitimate science is fine" in p for p in prompts), actor
        assert all("{guidelines}" not in p for p in prompts), actor


def test_guidelines_title_is_dropped_and_headings_nest():
    assert not agents.GUIDELINES.startswith("# ")
    assert "\n### Out of scope" in agents.GUIDELINES
    assert "\n## " not in agents.GUIDELINES


# --- Input screening ------------------------------------------------------------


def test_flagged_input_stops_before_any_lesson_agent(client, scripted, events):
    rec = scripted({"agent:input_screen": [{
        "verdict": "flag",
        "issues": [{"field": "content.topic", "category": "harmful", "reason": "Asks how to injure a person."}],
    }]})
    req = valid_request()
    req["content"]["topic"] = "how to do an acid attack on someone"
    r = client.post("/api/generate", json=req)
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "content_flagged"
    assert err["details"] == [{"field": "content.topic", "message": "Asks how to injure a person."}]
    assert rec["calls"] == ["agent:input_screen"]
    rejected = [e for e in events if e["event"] == "validation.rejected"]
    assert rejected[0]["findings"] == [{"field": "content.topic", "category": "harmful"}]


def test_teacher_text_is_given_to_the_screener_as_data(client, scripted):
    rec = scripted({})
    req = valid_request()
    req["other_requests"] = "Use examples from cricket"
    client.post("/api/generate", json=req)
    prompt = rec["prompts"]["agent:input_screen"][0]
    assert '"content.topic": "photosynthesis"' in prompt
    assert '"other_requests": "Use examples from cricket"' in prompt
    assert '"learners.accessibility_needs"' not in prompt  # empty fields are left out


def test_issue_on_unknown_field_is_still_flagged(client, scripted):
    scripted({"agent:input_screen": [{
        "verdict": "allow",  # contradictory: issues present means flagged
        "issues": [{"field": "whole request", "category": "out_of_scope", "reason": "Not a school lesson."}],
    }]})
    r = client.post("/api/generate", json=valid_request())
    assert r.status_code == 422
    assert r.json()["error"]["details"] == [{"field": None, "message": "Not a school lesson."}]


def test_input_screen_failure_fails_closed(client, scripted):
    rec = scripted({"agent:input_screen": [AgentOutputError("bad"), AgentOutputError("bad")]})
    r = client.post("/api/generate", json=valid_request())
    assert r.status_code == 502
    assert rec["calls"] == ["agent:input_screen", "agent:input_screen"]  # one retry, no lesson agents


# --- Output screening -----------------------------------------------------------


def test_flagged_plan_is_rewritten_with_the_findings(client, scripted, events):
    rec = scripted({"agent:output_screen": [_flag(("phases", 1, "student_actions", 0)), CLEAN]})
    r = client.post("/api/generate", json=valid_request())
    assert r.status_code == 200, r.text
    assert rec["calls"].count("agent:tpack") == 2
    rewrite = rec["prompts"]["agent:tpack"][1]
    assert "rejected by the content review" in rewrite
    assert "phases[1].student_actions[0]: Asks students to taste an unknown liquid." in rewrite
    assert REMOVED_WARNING not in r.json()["warnings"]
    assert any(e["event"] == "agent.start" and e.get("rewrite") for e in events)


def test_still_flagged_after_rewrite_is_removed_and_warned(client, scripted, events):
    flag = _flag(("phases", 1, "student_actions", 0))
    scripted({"agent:output_screen": [flag, flag]})
    r = client.post("/api/generate", json=valid_request())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["lesson"]["phases"][1]["student_actions"] == []
    assert REMOVED_WARNING in body["warnings"]
    removed = [e for e in events if e["event"] == "content.removed"]
    assert removed and removed[0]["removed"] == 1


def test_removal_that_breaks_the_plan_fails(client, scripted):
    flag = _flag(("phases", 0, "teacher_steps", 0))  # the only step of a required list
    scripted({"agent:output_screen": [flag, flag]})
    r = client.post("/api/generate", json=valid_request())
    assert r.status_code == 502
    assert r.json()["error"]["code"] == "agent_output_invalid"


def test_unlocatable_flag_fails_rather_than_passing(client, scripted):
    flag = _flag(("phases", 9, "teacher_steps", 0))
    scripted({"agent:output_screen": [flag, flag]})
    assert client.post("/api/generate", json=valid_request()).status_code == 502


def test_flagged_materials_are_rewritten_then_removed(client, scripted):
    flag = _flag(("handouts", 0, "items", 0), ("exit_ticket", 2))
    rec = scripted({"agent:output_screen": [flag, _flag(("exit_ticket", 0, "why"))]})
    plan = copy.deepcopy(PLAN)
    r = client.post("/api/materials", json={"request": valid_request(), "plan": plan})
    # The rewrite was still flagged on a required field, so removal breaks it.
    assert r.status_code == 502
    assert rec["calls"] == ["agent:materials", "agent:output_screen", "agent:materials", "agent:output_screen"]
    assert "handouts[0].items[0]" in rec["prompts"]["agent:materials"][1]


def test_materials_warning_after_removal(client, scripted):
    materials = copy.deepcopy(MATERIALS)
    materials["exit_ticket"].append(copy.deepcopy(materials["exit_ticket"][0]) | {"q": "An extra question?"})
    flag = _flag(("exit_ticket", 3))
    scripted({"agent:materials": [materials, materials], "agent:output_screen": [flag, flag]})
    r = client.post("/api/materials", json={"request": valid_request(), "plan": copy.deepcopy(PLAN)})
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["materials"]["exit_ticket"]) == 3
    assert body["warnings"] == [REMOVED_WARNING]


# --- remove_paths ----------------------------------------------------------------


def test_remove_paths_handles_indices_order_and_duplicates():
    data = {"a": ["x", "y", "z"], "b": {"c": "text", "d": [1, 2]}, "e": [{"f": ["p", "q"]}]}
    removed, unresolved = remove_paths(
        data,
        [["a", 0], ["a", "2"], ["a", 0], ["b", "c"], ["b", "d"], ["e", 0], ["e", 0, "f", 1], ["missing"]],
    )
    assert data == {"a": ["y"], "b": {"c": "", "d": []}, "e": []}
    assert removed == 5  # a[0], a[2], b.c, b.d, e[0]; e[0].f[1] went with its parent
    assert unresolved == 1  # "missing"


def test_remove_paths_refuses_non_removable_targets():
    data = {"overview": {"title": "T"}}
    assert remove_paths(data, [["overview"]]) == (0, 1)
    assert remove_paths(data, [[]]) == (0, 1)
    assert data == {"overview": {"title": "T"}}
