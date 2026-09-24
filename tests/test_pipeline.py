from conftest import valid_request


def test_agents_run_in_order(client, fake_backends):
    client.post("/api/generate", json=valid_request())
    assert fake_backends == ["agent:ck", "agent:pk", "agent:tk", "agent:tpack"]


def test_handoffs_go_from_foundation_agents_to_tpack(client, fake_backends, events):
    client.post("/api/generate", json=valid_request())
    handoffs = [(e["from"], e["to"]) for e in events if e["event"] == "agent.handoff"]
    assert handoffs == [
        ("agent:ck", "agent:tpack"),
        ("agent:pk", "agent:tpack"),
        ("agent:tk", "agent:tpack"),
    ]


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
