"""Shared fixtures. No Gemini key or Chroma index is needed: the LLM and
retrieval are replaced with fakes."""

import copy
import logging
import os
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config  # noqa: E402

config.LOG_DIR = tempfile.mkdtemp(prefix="agenttpack-logs-")

import api  # noqa: E402
import llm  # noqa: E402
import run  # noqa: E402
from logging_setup import LOGGER_NAME  # noqa: E402

VALID_REQUEST = {
    "content": {"topic": "photosynthesis", "duration": 45, "chapter_num": 1},
    "learners": {"class_size": 40, "prior_knowledge": "new"},
    "pedagogy": {"method": "5E", "blooms_focus": "understand"},
    "technology": {
        "infrastructure": "projector",
        "projector_available": True,
        "student_devices": "none",
        "internet_access": "reliable",
        "tools": "PhET Simulations",
    },
}

FAKE_CHUNKS = [
    {
        "id": "g7_ch01_s1.2_000",
        "text": "Plants make their food by photosynthesis.",
        "metadata": {"chapter_num": 1, "section": "1.2", "chapter": "Nutrition in Plants"},
        "distance": 0.3,
    },
    {
        "id": "g7_ch01_s1.2_001",
        "text": "Chlorophyll captures sunlight.",
        "metadata": {"chapter_num": 1, "section": "1.2", "chapter": "Nutrition in Plants"},
        "distance": 0.4,
    },
]

FAKE_OUTPUTS = {
    "agent:ck": {
        "blocks": [{"title": "Core concepts", "content": "...", "cited_chunk_ids": ["g7_ch01_s1.2_000"]}],
        "coverage_gaps": ["stomata"],
    },
    "agent:pk": {
        "approach_summary": "5E",
        "activity_sequence": [],
        "assessment": {},
        "feasibility_notes": [],
        "missing_inputs": [],
    },
    "agent:tk": {
        "technology_profile": [],
        "requested_tools_status": [{"tool": "PhET Simulations", "status": "available"}],
        "missing_inputs": [],
    },
    "agent:tpack": {
        "overview": {
            "title": "How plants make their food",
            "duration_min": 45,
            "objectives": ["Students will explain how leaves make food."],
            "materials": ["8 potted plants"],
            "prep_before_class": ["Keep one plant in the dark for 2 days"],
        },
        "phases": [
            {
                "name": "Engage",
                "minutes": 10,
                "teacher_steps": ["Hold up a leaf and ask where plants get food."],
                "student_actions": ["Discuss with a partner."],
                "board_notes": "Where does a plant get its food?",
                "questions": [{"q": "Do plants eat soil?", "expected_answer": "No, they make food in leaves."}],
                "misconceptions": ["Plants eat soil -> leaves make food."],
                "if_tech_missing": "",
                "sources": ["g7_ch01_s1.2_000", "g7_ch99_s9.9_999"],
            },
            {
                "name": "Explore",
                "minutes": 35,
                "teacher_steps": ["Show the leaf-starch video (g7_ch01_s1.2_001).", "[g7_ch01_s1.2_001]"],
                "student_actions": ["Test leaves with iodine in groups of 5."],
                "board_notes": "",
                "questions": [],
                "misconceptions": [],
                "if_tech_missing": "Draw the test steps on the board instead.",
                "sources": ["g7_ch01_s1.2_001"],
            },
        ],
        "not_covered": [],
    },
    "agent:materials": {
        "handouts": [
            {
                "title": "Explore: Testing leaves",
                "for_phase": "Explore",
                "instructions": "Work in your group.",
                "items": ["What colour did the leaf turn?"],
            }
        ],
        "exit_ticket": [
            {
                "q": "Which part of the plant makes food?",
                "type": "mcq",
                "options": ["Roots", "Leaves", "Flowers"],
                "answer": "Leaves",
                "why": "Leaves have chlorophyll.",
                "bloom": "Remember",
            },
            {
                "q": "What does chlorophyll capture?",
                "type": "mcq",
                "options": ["Sunlight", "Water", "Soil"],
                "answer": "Sunlight",
                "why": "The passage says chlorophyll captures sunlight.",
                "bloom": "remember",
            },
            {
                "q": "Why did the leaf kept in the dark not turn blue-black?",
                "type": "mcq",
                "options": ["It had no starch", "It was too old", "It was wet"],
                "answer": "It had no starch",
                "why": "Without light, the leaf could not make starch.",
                "bloom": "Analyze",
            },
        ],
    },
}


def valid_request():
    return copy.deepcopy(VALID_REQUEST)


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.events = []

    def emit(self, record):
        fields = getattr(record, "event_fields", None)
        if fields:
            self.events.append(dict(fields))


@pytest.fixture
def events():
    handler = _Capture()
    logger = logging.getLogger(LOGGER_NAME)
    logger.addHandler(handler)
    yield handler.events
    logger.removeHandler(handler)


@pytest.fixture
def fake_backends(monkeypatch):
    """Replace retrieval and the LLM. Returns the list of agent actors called."""
    calls = []

    def fake_call_agent(prompt, actor="llm"):
        calls.append(actor)
        return copy.deepcopy(FAKE_OUTPUTS[actor]), {"prompt_tokens": 10, "completion_tokens": 5}

    monkeypatch.setattr(llm, "call_agent", fake_call_agent)
    monkeypatch.setattr(run, "retrieve", lambda topic, chapter_num=None: copy.deepcopy(FAKE_CHUNKS))
    return calls


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    return TestClient(api.app, raise_server_exceptions=False)
