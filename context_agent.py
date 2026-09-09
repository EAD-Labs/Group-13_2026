"""Extracts a structured classroom profile from a teacher's free-text narrative.

Never writes to the database itself — the caller must show the draft back to
the teacher for verification and save it explicitly (see profile.py).
"""

import json

from agent_prompts import load_agent_prompt
from llm import call_agent

CONTEXT_PROMPT = load_agent_prompt("prompts/context_agent.md")


def run_context_agent(narrative, current_profile=None, teacher_id=None, section_id=None):
    """Extract a profile draft from `narrative`. `current_profile` (a dict in
    the same shape as profile.get_profile's return value, or None) is given
    to the model as prior context so an update only changes what the
    narrative actually addresses."""
    profile_text = (
        json.dumps(current_profile, indent=2)
        if current_profile
        else "none — first time"
    )
    prompt = CONTEXT_PROMPT.format(narrative=narrative, current_profile=profile_text)
    return call_agent(
        prompt,
        teacher_id=teacher_id,
        section_id=section_id,
        agent_name="context_agent",
    )
