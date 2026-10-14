from conftest import valid_request


def test_agents_run_in_order(client, fake_backends):
    client.post("/api/generate", json=valid_request())
    assert fake_backends == [
        "agent:ck", "agent:pk", "agent:tk", "agent:pck", "agent:tck", "agent:tpk", "agent:tpack",
    ]


def test_handoffs_go_through_intersection_agents_to_tpack(client, fake_backends, events):
    client.post("/api/generate", json=valid_request())
    handoffs = [(e["from"], e["to"]) for e in events if e["event"] == "agent.handoff"]
    assert handoffs == [
        ("agent:ck", "agent:pck"),
        ("agent:pk", "agent:pck"),
        ("agent:ck", "agent:tck"),
        ("agent:tk", "agent:tck"),
        ("agent:pk", "agent:tpk"),
        ("agent:tk", "agent:tpk"),
        ("agent:ck", "agent:tpack"),
        ("agent:pk", "agent:tpack"),
        ("agent:tk", "agent:tpack"),
        ("agent:pck", "agent:tpack"),
        ("agent:tck", "agent:tpack"),
        ("agent:tpk", "agent:tpack"),
    ]


def test_trade_offs_are_logged_as_decisions(client, fake_backends, events):
    body = client.post("/api/generate", json=valid_request()).json()
    decisions = [e for e in events if e["event"] == "agent.decision"]
    assert [e["actor"] for e in decisions] == ["agent:pck", "agent:tpk", "agent:tpack"]
    pck = decisions[0]
    assert pck["chosen"] == "investigate one idea"
    assert pck["because"] == "duration: 45; prior_knowledge: new"
    assert decisions[2]["rejected"] == "Each student runs it"
    assert body["lesson"]["design_decisions"][0]["decision"] == "Simulation use"
    assert body["notes"]["trade_offs"]["tpk"][0]["chosen"] == "teacher on the projector"


def test_pk_feasibility_and_unavailable_tools_are_logged_as_decisions(client, fake_backends, events, monkeypatch):
    import copy
    from conftest import FAKE_OUTPUTS

    outputs = copy.deepcopy(FAKE_OUTPUTS)
    outputs["agent:pk"]["feasibility_notes"] = [
        {"requested": "Inquiry-based learning", "issue": "duration: 11", "alternative": "teacher-led demo"}
    ]
    outputs["agent:tk"]["requested_tools_status"] = [
        {"tool": "Youtube", "status": "unavailable", "reason": "policy forbids devices"}
    ]
    import llm
    monkeypatch.setattr(llm, "call_agent", lambda prompt, actor="llm": (copy.deepcopy(outputs[actor]), {}))
    client.post("/api/generate", json=valid_request())
    by_actor = {e["actor"]: e for e in events if e["event"] == "agent.decision"}
    assert by_actor["agent:pk"]["chosen"] == "teacher-led demo"
    assert by_actor["agent:tk"]["because"] == "policy forbids devices"


def test_seq_is_strictly_increasing_within_one_trace(client, fake_backends, events):
    r = client.post("/api/generate", json=valid_request())
    trace_events = [e for e in events if e["trace_id"] == r.headers["X-Trace-Id"]]
    seqs = [e["seq"] for e in trace_events]
    assert seqs == list(range(1, len(seqs) + 1))
    names = [e["event"] for e in trace_events]
    assert names[0] == "human.request"
    assert names[-1] == "human.response"
    assert names.index("validation.passed") < names.index("agent.start")


def test_invalid_citations_stripped_and_warned(client, fake_backends, events):
    r = client.post("/api/generate", json=valid_request())
    body = r.json()
    assert body["lesson"]["phases"][0]["sources"] == ["g7_ch01_s1.2_000"]
    assert body["notes"]["removed_references"] == 1
    assert not any("reference" in w for w in body["warnings"])  # internal, not a teacher alert
    assert any(e["event"] == "citation.invalid" for e in events)


def test_ck_coverage_gaps_carried_forward(client, fake_backends):
    body = client.post("/api/generate", json=valid_request()).json()
    assert "stomata" in body["lesson"]["not_covered"]


def test_real_prompts_format_with_pipeline_inputs(client, fake_backends, events):
    """Every placeholder in every real prompt file gets a value."""
    r = client.post("/api/generate", json=valid_request())
    assert r.status_code == 200
    assert not any(e.get("reason") == "prompt_placeholder" for e in events)
