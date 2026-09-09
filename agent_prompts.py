"""Shared prompt loading for every agent.

Every agent prompt is prefixed with the same guardrails block from
prompts/guardrails.md, so no agent (Context, CK, and future PK/TK) can be
run without it — this is the one place that inserts it, rather than relying
on one agent to relay it to another in free text.
"""

with open("prompts/guardrails.md", encoding="utf-8") as f:
    GUARDRAILS = f.read()


def load_agent_prompt(path):
    """Read an agent's own prompt template and prepend the shared guardrails."""
    with open(path, encoding="utf-8") as f:
        template = f.read()
    return GUARDRAILS + "\n\n" + template
