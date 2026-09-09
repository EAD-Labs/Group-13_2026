# AgentTPACK — Content Knowledge RAG proof-of-concept

One slice of a larger multi-agent lesson-planning system, deliberately reduced to
a **single agent** (Content Knowledge) so the architecture can be verified end to
end before it is scaled up.

A teacher types a lesson topic in the browser. The system runs a semantic search
over NCERT Grade 7 biology chunks stored in ChromaDB, injects the retrieved
passages into the agent prompt, calls Gemini, and returns a structured
recommendation in which every claim carries a clickable citation that resolves
back to the actual textbook paragraph it came from.

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
defaults to `gemini-2.0-flash`.

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

**5b. Create the profile database:**

```bash
python db.py
```

Creates `./app.db` (SQLite) with the teacher-profile tables and `context_log`.
Every statement uses `IF NOT EXISTS`, so unlike `ingest.py` this never drops
existing data — safe to re-run any time. `api.py` also runs this
automatically on startup, so the manual step is mostly useful for inspecting
the schema up front.

**6. Smoke-test the pipeline from the command line:**

```bash
python run.py
```

**7. Start the server:**

```bash
uvicorn api:app --reload
```

**8. Open <http://localhost:8000>.**

## API

| Route | Purpose |
| --- | --- |
| `POST /api/generate` | Body: `topic` (required), `chapter`, `chapter_num`, `grade`, `duration`, `num_students`, `tech_availability`, `pedagogy`, `teacher_id`, `section_name`. Runs the Content Knowledge agent. If `teacher_id` + `section_name` match a saved profile, its `grade`/`num_students`/`tech_availability`/`pedagogy` override whatever the request body sent for them. |
| `GET /api/chunks/{chunk_id}` | Keyed lookup of one chunk. 404 if the id does not exist. |
| `POST /api/profile/{teacher_id}/extract` | Body: `section_name`, `narrative` (free text). Runs the Context Agent and returns a draft profile — never saved automatically. |
| `PUT /api/profile/{teacher_id}` | Body: `teacher`, `section`, `infra`, `pedagogy` (the same shape `/extract` returns, after the teacher has reviewed it). Upserts the profile. |
| `GET /api/profile/{teacher_id}?section=...` | Reads the saved profile for one teacher/section. 404 if nothing has been saved yet. |
| `GET /` | The static frontend. Mounted last, as a catch-all. |

Chunk ids look like `g7_ch01_s1.2_000` — grade, chapter, section, index within
the section. `run_ck` attaches `_retrieved_ids` to its result so the model's
citations can be checked against what was actually retrieved.

### Teacher profiles

`db.py` defines the schema (`teachers`, `sections`, `section_infra`,
`section_pedagogy`, `syllabus_progress`, `context_log`) and `teacher_profile.py`
is the only module that reads or writes it — see its docstrings for the
read/write functions. `context_agent.py` (prompt: `prompts/context_agent.md`)
turns a teacher's free-text narrative into a structured draft, using the
current saved profile as prior context so a short follow-up note updates the
profile instead of replacing it. Nothing is saved until the teacher confirms
via `PUT /api/profile/{teacher_id}`.

### Guardrails

`prompts/guardrails.md` is a shared constraints block — stay sensitive to
the cultural, political, and religious context actually given, never
produce content offensive or insensitive to a cultural, political, or
religious group or belief, and don't take a position on contested
political or religious questions. `agent_prompts.load_agent_prompt()` is
the only way an agent's `.md` template gets loaded, and it prepends this
block automatically, so every agent (Context, CK, and future PK/TK) carries
the identical instruction directly rather than depending on one agent to
relay it to another.

`context_log` is append-only and write-only by design: `llm.call_agent`
writes every prompt and raw response there when given a `teacher_id`, but no
prompt-building code anywhere reads it back. It exists purely as a human-
readable record of what was said and what was generated, not as context for
future generations.

Note: this module is named `teacher_profile.py`, not `profile.py` — a file
named `profile.py` shadows Python's own `profile`/`cProfile` stdlib module
for anything run from this directory.

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `ValueError: No section headings found` from `ingest.py` | The chapter file has no `## X.Y Title` lines, or the headings do not start at the beginning of a line. | Reformat the headings exactly as `## 1.2 Section Title`. A tab or leading space before `##` breaks the `re.MULTILINE` match. |
| Far fewer chunks than expected, or one giant chunk per section | Paragraphs are not separated by blank lines, so the splitter's first separator (`"\n\n"`) never fires. | Put a genuine blank line between paragraphs. Chunks under 100 characters are also skipped by design. |
| All distances cluster around the same value; retrieval feels random | Chunks are too long, so every embedding averages out to roughly the same vector. | Lower `CHUNK_SIZE` in `config.py` (try 500–700), re-run `python ingest.py`. |
| `KeyError` raised by `.format()` in `run.py` | A literal `{` or `}` in `prompts/ck_agent.md` that is not a real placeholder — most often the JSON example. | Double every literal brace: `{{` and `}}`. Only `{grade} {chapter} {topic} {duration} {chunks}` stay single. |
| `NotFoundError` / model-not-found from the Gemini call | `GEMINI_MODEL` names a model your key cannot reach. | List what the key can see: `python -c "from llm import client; print([m.id for m in client.models.list()])"`, then set `GEMINI_MODEL` to one of them. |
| `POST /api/generate` returns 404 | The `StaticFiles` mount is registered before the API routes, so its catch-all swallows them. | `app.mount("/", ...)` must be the **last** line of `api.py`. |
| `Collection ncert_g7_biology does not exist` on import of `retrieval.py` | `ingest.py` has not been run against this `chroma_store`. | Run `python ingest.py` first. |
