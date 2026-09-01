"""Single-agent RAG pipeline: retrieve -> format -> prompt -> Gemini."""

import json

from llm import call_agent
from retrieval import format_chunks, retrieve

with open("prompts/ck_agent.md", encoding="utf-8") as f:
    CK_PROMPT = f.read()


def run_ck(topic, chapter, chapter_num, grade=7, duration=45):
    """Run the Content Knowledge agent for one topic."""
    chunks = retrieve(topic, chapter_num=chapter_num)
    prompt = CK_PROMPT.format(
        grade=grade,
        chapter=chapter,
        topic=topic,
        duration=duration,
        chunks=format_chunks(chunks),
    )
    result = call_agent(prompt)
    # So callers can check the model's citations against what was actually given.
    result["_retrieved_ids"] = [c["id"] for c in chunks]
    return result


if __name__ == "__main__":
    output = run_ck(
        topic="photosynthesis",
        chapter="Nutrition in Plants",
        chapter_num=1,
    )
    print(json.dumps(output, indent=2))
