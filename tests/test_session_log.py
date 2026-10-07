"""The per-session log file: one readable file per trace, with the agents'
inputs, outputs and hand-off payloads."""

import copy
import json
import os

import config
from conftest import FAKE_OUTPUTS, valid_request
from logging_setup import session_log_path


def _read_session(trace_id):
    path = session_log_path(trace_id)
    assert path and os.path.exists(path), "no session file was written"
    assert os.path.dirname(path) == os.path.join(config.LOG_DIR, config.LOG_SESSION_DIR)
    with open(path, encoding="utf-8") as f:
        return f.read()


def test_generate_writes_one_session_file_with_agent_io(client, fake_backends):
    r = client.post("/api/generate", json=valid_request())
    assert r.status_code == 200, r.text
    text = _read_session(r.headers["X-Trace-Id"])

    assert "POST /api/generate" in text
    for agent in ("ck", "pk", "tk", "tpack"):
        assert f"agent:{agent}" in text
    for source in ("ck", "pk", "tk"):
        assert f"agent:{source} --> agent:tpack" in text
    # The contents, not just the key names, are recorded.
    assert "| payload:" in text and "| output:" in text and "| inputs:" in text
    assert FAKE_OUTPUTS["agent:tpack"]["overview"]["title"] in text
    assert "pipeline.done" in text and "agent_sequence=" in text


def test_materials_request_appends_to_the_same_session(client, fake_backends):
    r = client.post("/api/generate", json=valid_request())
    trace_id = r.headers["X-Trace-Id"]
    plan = copy.deepcopy(FAKE_OUTPUTS["agent:tpack"])
    r2 = client.post(
        "/api/materials",
        json={"request": valid_request(), "plan": plan},
        headers={"X-Trace-Id": trace_id},
    )
    assert r2.status_code == 200, r2.text

    text = _read_session(trace_id)
    assert "POST /api/generate" in text and "POST /api/materials" in text
    assert "agent:tpack --> agent:materials" in text
    assert "materials.done" in text


def test_payloads_can_be_left_out_of_session_files(client, fake_backends, monkeypatch):
    monkeypatch.setattr(config, "LOG_SESSION_PAYLOADS", False)
    r = client.post("/api/generate", json=valid_request())
    text = _read_session(r.headers["X-Trace-Id"])
    assert "agent:ck --> agent:tpack" in text
    assert "| output:" not in text
    assert FAKE_OUTPUTS["agent:tpack"]["overview"]["title"] not in text


def test_payloads_stay_out_of_the_jsonl_log_by_default(client, fake_backends):
    r = client.post("/api/generate", json=valid_request())
    trace_id = r.headers["X-Trace-Id"]
    with open(os.path.join(config.LOG_DIR, config.LOG_FILE), encoding="utf-8") as f:
        entries = [json.loads(line) for line in f]
    ends = [e for e in entries if e.get("trace_id") == trace_id and e["event"] == "agent.end"]
    assert len(ends) == 4
    assert all("output" not in e for e in ends)
