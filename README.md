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
| `POST /api/generate` | Body: `topic` (required), `chapter`, `chapter_num`, `grade`, `duration`. Runs the Content Knowledge agent. |
| `GET /api/chunks/{chunk_id}` | Keyed lookup of one chunk. 404 if the id does not exist. |
| `GET /` | The static frontend. Mounted last, as a catch-all. |

Chunk ids look like `g7_ch01_s1.2_000` — grade, chapter, section, index within
the section. `run_ck` attaches `_retrieved_ids` to its result so the model's
citations can be checked against what was actually retrieved.

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
