"""Single-agent RAG pipeline: retrieve -> format -> prompt -> Gemini."""

import json

from agent_prompts import load_agent_prompt
from llm import call_agent
from teacher_profile import record_syllabus_progress
from retrieval import format_chunks, retrieve

CK_PROMPT = load_agent_prompt("prompts/ck_agent.md")


def _format_list(values):
    """Render a list field for the prompt, or a clear placeholder if empty."""
    if not values:
        return "not specified"
    return ", ".join(values)


def run_ck(
    topic,
    chapter,
    chapter_num,
    grade=7,
    duration=45,
    num_students=None,
    tech_availability=None,
    pedagogy=None,
    teacher_id=None,
    section_id=None,
):
    """Run the Content Knowledge agent for one topic."""
    chunks = retrieve(topic, chapter_num=chapter_num)
    prompt = CK_PROMPT.format(
        grade=grade,
        chapter=chapter,
        topic=topic,
        duration=duration,
        num_students=num_students if num_students else "not specified",
        tech_availability=_format_list(tech_availability),
        pedagogy=_format_list(pedagogy),
        chunks=format_chunks(chunks),
    )
    result = call_agent(prompt, teacher_id=teacher_id, section_id=section_id, agent_name="ck_agent")
    # So callers can check the model's citations against what was actually given.
    result["_retrieved_ids"] = [c["id"] for c in chunks]

    if section_id is not None:
        record_syllabus_progress(section_id, chapter, topic)

    return result


if __name__ == "__main__":
    output = run_ck(
        topic="photosynthesis",
        chapter="Nutrition in Plants",
        chapter_num=1,
        num_students=40,
        tech_availability=["Projector / smart board"],
        pedagogy=["Discussion-based"],
    )
    print(json.dumps(output, indent=2))
