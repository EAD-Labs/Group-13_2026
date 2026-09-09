"""Extracts this lesson's request from a teacher's free-text description —
the topic, and any one-off pedagogy/tech preference for this lesson only.

Nothing here is saved to the profile; it only pre-fills the lesson form for
the teacher to review before Generate.
"""

from agent_prompts import load_agent_prompt
from llm import call_agent

LESSON_REQUEST_PROMPT = load_agent_prompt("prompts/lesson_request_agent.md")


def run_lesson_request_agent(narrative, teacher_id=None, section_id=None):
    prompt = LESSON_REQUEST_PROMPT.format(narrative=narrative)
    return call_agent(
        prompt,
        teacher_id=teacher_id,
        section_id=section_id,
        agent_name="lesson_request_agent",
    )
