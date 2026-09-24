# AgentTPACK — multi-agent TPACK lesson-design proof-of-concept

A proof-of-concept of a multi-agent lesson-planning system built on the TPACK
framework.

A teacher works through a short wizard covering the lesson's content, the
learners and teaching approach, and the classroom technology. The system runs
a semantic search over NCERT Grade 7 biology chunks stored in ChromaDB. Three
foundational agents (Content, Pedagogical and Technological Knowledge) then
each work on the request within their own domain, and a TPACK integrator
agent combines their outputs into one lesson plan. Every content claim in the
plan carries a clickable citation that resolves to the textbook paragraph it
came from.

## Stack

| Piece | Choice |
| --- | --- |
| Vector store | ChromaDB `PersistentClient`, embedded, `./chroma_store` |
| Embeddings | `sentence-transformers` `all-MiniLM-L6-v2`, running locally |
| Chunking | `langchain-text-splitters` `RecursiveCharacterTextSplitter` |
| LLM | Gemini via its OpenAI-compatible endpoint, using the `openai` client |
| API | FastAPI + uvicorn |
| Frontend | one vanilla HTML file, no build step |

No PostgreSQL, no Docker, no LangChain chains/retrievers/vectorstores, no
LangGraph, no React, no auth, no PDF parsing.

## Setup

Run these in order.

**1. Create and activate a virtual environment** (Python 3.11+):

```bash
python -m venv venv
```

Windows PowerShell: `venv\Scripts\Activate.ps1` — macOS/Linux: `source venv/bin/activate`

**2. Install the dependencies:**

```bash
pip install -r requirements.txt
```

**3. Add your key.** Copy `.env.example` to `.env` if it is not already there,
then set `GEMINI_API_KEY` to a real key from Google AI Studio. `GEMINI_MODEL`
defaults to `gemini-3.6-flash` (`gemini-2.0-flash` has been retired and now
returns a 404).

**4. Replace `data/chapter01.txt` with the real NCERT chapter text.** The file
shipped here is clearly-marked placeholder text that exists only so the pipeline
runs before real content is added. The required format is:

```
## 1.1 Section Title
First paragraph.

Second paragraph.

## 1.2 Next Section Title
...
```

Two hashes, a space, the section number as `X.Y`, a space, then the title.
Paragraphs must be separated by a **blank line**. Also check that the chapter
numbers in `config.CHAPTERS` match your own PDF — NCERT renumbered chapters in
the 2023 rationalisation.

**5. Ingest:**

```bash
python ingest.py
```

The collection is deleted and rebuilt every run, so this is idempotent and the
chunk ids stay stable. It prints the final `collection.count()`.

**6. Run the tests** (no API key or index needed; the LLM and retrieval are faked):

```bash
pytest -q
```

**7. Start the server:**

```bash
uvicorn api:app --reload
```

**8. Open <http://localhost:8000>.**

## API

| Route | Purpose |
| --- | --- |
| `POST /api/generate` | Stage 1: runs the agents and returns the lesson plan. Body sections: `content`, `learners`, `pedagogy`, `technology`, optional `other_requests`. The Pydantic models in `api.py` define the fields, enums and limits. |
| `POST /api/materials` | Stage 2: returns the handouts and the exit-ticket quiz for a plan. Body: `{"request": <the /api/generate body>, "plan": <lesson from /api/generate>}`. |
| `GET /api/chunks/{chunk_id}` | Keyed lookup of one chunk. 422 for a malformed id, 404 if it does not exist. |
| `GET /` | The lesson-design wizard. Mounted last, as a catch-all. |

A lesson is generated in two stages so that a Gemini failure in the second
stage never loses the plan:

1. **Plan** (`/api/generate`): validation → retrieval → **CK → PK → TK →
   TPACK**. The CK, PK and TK agents each work only in their own domain. The
   TPACK agent receives all three outputs and writes a teacher run sheet: an
   `overview` (title, objectives, materials, prep) and `phases` (numbered
   teacher steps, student actions, board notes, questions with expected
   answers, misconceptions, an `if_tech_missing` fallback and `sources`).
   The response also contains `not_covered`, `missing_details` (questions
   for the teacher, each linked to a wizard field where one matches),
   `notes`, `warnings` and `agent_sequence`.
2. **Handouts and quiz** (`/api/materials`): one call to the materials agent,
   which returns printable `handouts` and a multiple-choice `exit_ticket`
   with answers, explanations and Bloom's levels.

Both outputs are validated against the Pydantic models in `lesson_schema.py`
(for example, a quiz answer must be one of its options). If the model returns
invalid JSON or the wrong shape, the call is retried once with the validation
errors; a second failure returns `agent_output_invalid` (502). Sources that
point at passages that were not retrieved are removed (counted in
`notes.removed_references`), and chunk ids that leak into the text are
stripped.

The result screen shows Plan, Handouts and Quiz tabs. **Download teacher
pack** opens the browser's print dialog ("Save as PDF"): the plan, each
handout on its own page, a student copy of the quiz, and the answer key on a
separate "Teacher copy" page.

Chunk ids look like `g7_ch01_s1.2_000`: grade, chapter, section, then the
index within the section.

### Errors

Every error returns one envelope:

```json
{"error": {"code": "unsafe_topic", "message": "...", "details": [{"field": "content.topic", "message": "..."}], "trace_id": "..."}}
```

| Code | Status | Meaning |
| --- | --- | --- |
| `invalid_input` | 422 | Malformed JSON, a missing or unknown field, a bad enum, or a value out of range |
| `unsafe_topic` | 422 | Unsafe content or a prompt-injection attempt in a free-text field |
| `incomplete_constraints` | 422 | Contradictory classroom details (for example, 1:1 devices with no student devices) |
| `topic_not_in_source` | 422 | The topic isn't covered by the chapter (retrieval distance above `RELEVANCE_MAX_DISTANCE`) |
| `not_found` | 404 | Unknown chunk id or API path |
| `llm_failure` / `agent_output_invalid` | 502 | Gemini failed or returned unusable output |
| `index_unavailable` | 503 | `python ingest.py` has not been run |
| `internal_error` | 500 | Anything else. The traceback is written to the log only. |

## Interaction logs

Every request gets a trace id, which is returned in the `X-Trace-Id` header
and in the response body. Each event is written as one JSON line to
`logs/interactions.jsonl`, with a `seq` number that increases within the
trace. The events cover the human request, validation, retrieval, each
agent's start and end (with timings and token counts), and every
`agent.handoff` between agents. Citation clicks from the page are logged
under the trace that produced the lesson.

```bash
python trace.py --last        # the most recent request, step by step
python trace.py --list        # recent trace ids with their status
python trace.py <trace_id>    # one trace (a prefix is enough)
```

To also log full prompts and raw model output, set `LOG_FULL_PAYLOADS = True`
in `config.py`.

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `ValueError: No section headings found` from `ingest.py` | The chapter file has no `## X.Y Title` lines, or the headings do not start at the beginning of a line. | Reformat the headings exactly as `## 1.2 Section Title`. A tab or leading space before `##` breaks the `re.MULTILINE` match. |
| Far fewer chunks than expected, or one giant chunk per section | Paragraphs are not separated by blank lines, so the splitter's first separator (`"\n\n"`) never fires. | Put a genuine blank line between paragraphs. Chunks under 100 characters are also skipped by design. |
| All distances cluster around the same value; retrieval feels random | Chunks are too long, so every embedding averages out to roughly the same vector. | Lower `CHUNK_SIZE` in `config.py` (try 500–700), re-run `python ingest.py`. |
| `agent_output_invalid` with "prompt placeholder" (`agent.error reason=prompt_placeholder` in the log) | A literal `{` or `}` in a `prompts/*.md` file that is not a real placeholder (most often the JSON example), or a new placeholder that `run.py` does not fill. | Double every literal brace: `{{` and `}}`. Make sure every single-brace placeholder is passed in by `run_pipeline`. |
| "The AI service is overloaded right now" (502 `llm_failure`) | Gemini returned 503 (high demand) or 429 (quota used up) for every configured model. Each request makes 4 calls, so free-tier quotas run out fast. | Run `python trace.py --last`: `llm.retry` lines show each model and status. Wait a few minutes, set `GEMINI_FALLBACK_MODELS` in `.env` (comma-separated) to other models your key can use, or enable billing on the key for higher limits. |
| Valid chapter topics rejected with `topic_not_in_source` | `RELEVANCE_MAX_DISTANCE` is too strict for your chapter text. | Look at the `retrieval.done` distances in `python trace.py --last` and adjust the threshold in `config.py`. |
| `NotFoundError` / model-not-found from the Gemini call | `GEMINI_MODEL` names a model your key cannot reach. | List what the key can see: `python -c "from llm import client; print([m.id for m in client.models.list()])"`, then set `GEMINI_MODEL` to one of them. |
| `POST /api/generate` returns 404 | The `StaticFiles` mount is registered before the API routes, so its catch-all swallows them. | `app.mount("/", ...)` must be the **last** line of `api.py`. |
| `index_unavailable` (503) from `/api/generate` | `ingest.py` has not been run against this `chroma_store`. | Run `python ingest.py`, then restart the server. |
