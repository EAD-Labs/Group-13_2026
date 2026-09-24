# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

AgentTPACK is a proof-of-concept of a multi-agent lesson-planning system built on the TPACK framework (Content, Pedagogical, and Technological Knowledge).

A teacher fills in a wizard in the browser. The backend validates the request, retrieves NCERT Grade 7 biology chunks from ChromaDB, and runs four Gemini agents in sequence. It returns a JSON lesson plan whose blocks cite chunk ids, and the frontend resolves each citation back to the source paragraph.

The stack is deliberately minimal: no LangChain chains, retrievers or vectorstores, no LangGraph, no database besides embedded ChromaDB and JSONL log files, no frontend build step, and no auth. Keep it that way unless asked otherwise.

## Commands

```bash
pip install -r requirements.txt
python ingest.py                   # (re)build ./chroma_store from data/*.txt, required before generating
uvicorn api:app --reload           # serve API + wizard at http://localhost:8000
pytest -q                          # all tests; no API key or index needed (LLM + retrieval are faked)
pytest tests/test_api.py::test_llm_failure_is_502 -q   # a single test
python trace.py --last             # print the most recent request's interaction sequence
```

The project's `venv/` is currently empty. The packages are installed in the Anaconda base Python, so plain `python` works.

`.env` needs `GEMINI_API_KEY`. `GEMINI_MODEL` defaults to `gemini-3.6-flash`; `gemini-2.0-flash` is retired and returns a 404.

## Architecture

A request to `POST /api/generate` flows through:

1. `api.py`: Pydantic models reject malformed input
2. `run.run_pipeline`
3. `validation.check_safety` / `check_consistency`
4. `retrieval.retrieve`, then `validation.check_relevance`
5. the agents via `agents.run_agent`: **CK → PK → TK → TPACK**, where TPACK returns a `LessonPlan`
6. post-processing: citation stripping on `phases[].sources`, scrubbing chunk ids from text, merging CK gaps into `not_covered`, and building `missing_details`

The browser then calls `POST /api/materials` (`run.run_materials`), which makes one `MATERIALS` agent call to produce handouts and the exit ticket (`LessonMaterials`). This is a separate stage so a Gemini failure there never loses the plan. The browser sends the plan back, so the server stores nothing between the two calls.

Details by module:

- **Agents (`agents.py`)**: each agent is an `AgentSpec`: a prompt file plus either `required_keys` or a Pydantic `schema`.
  - Schema agents (`TPACK`, `MATERIALS`, with models in `lesson_schema.py`) are validated and retried **once** with the validation errors appended to the prompt. A second failure raises `AgentOutputError`.
  - `lesson_schema.py` is the contract shared by the prompts, `api.py`'s response models and the Result screen. Change all of them together.
  - CK, PK and TK are foundational agents that never see each other's output. The PK and TK prompts forbid crossing into other domains.
  - TPACK receives all three via `upstream=`, and each one is injected into the prompt as `{ck_output}`, `{pk_output}` and `{tk_output}`.
  - `run.py` maps request enums to human-readable labels (`METHOD_LABELS`, etc.) and fills unspecified optional fields with `"Not specified"`.
- **Prompts (`prompts/*.md`)** are `str.format` templates. Literal braces must be doubled. If you add a placeholder, `run_pipeline` must fill it, or the request fails with `agent_output_invalid` (`reason=prompt_placeholder` in the log). `test_real_prompts_format_with_pipeline_inputs` catches this.
- **Errors (`errors.py`)**: every failure is an `AppError` subclass with a `code` and `http_status`. `api.py` renders all errors in one envelope: `{"error": {code, message, details: [{field, message}], trace_id}}`. `message` must be safe to show teachers; internals go to the log. Raise an `AppError` rather than `HTTPException`. The frontend maps `details[].field` (dotted, like `technology.student_devices`) back to the wizard input with the same `data-field`.
- **Logging (`logging_setup.py`)**: the middleware in `api.py` starts a trace for each request, reusing an incoming `X-Trace-Id` or `X-Request-ID` header.
  - `log_event(event, actor, **fields)` writes one JSON line to `logs/interactions.jsonl` with `trace_id` and a per-trace `seq`.
  - Actors are `human`, `api`, `validator`, `retriever` and `agent:<name>`. Agent-to-agent flow is recorded as `agent.handoff` events.
  - Keep new events on this vocabulary so `trace.py` output stays readable.
- **Retrieval (`retrieval.py`)** opens the collection lazily, so a missing index is a 503 at request time, not an import crash.
- **Ingestion (`ingest.py`)** rebuilds the collection on every run.
  - Chapter files need `## X.Y Title` headings with blank-line-separated paragraphs.
  - Chunk ids are `g{grade}_ch{NN}_s{X.Y}_{idx:03d}`.
  - `config.CHAPTERS` numbers must match the NCERT edition.
- **Relevance threshold**: `config.RELEVANCE_MAX_DISTANCE` was calibrated against the current chapter (the measurements are in the comment). Recalibrate it if the chapter text changes.
- **Safety patterns**: `config.UNSAFE_PATTERNS` must stay narrow, because biology vocabulary ("shoot", "sexual reproduction") is legitimate.
- **API mount order**: the `StaticFiles` mount at `/` is a catch-all and **must remain the last route** in `api.py`.
- **Frontend (`static/index.html`)** is a single vanilla HTML/JS wizard: Welcome → Content → Learners & pedagogy → Classroom technology → Review → Result.
  - The Result screen has Plan, Handouts and Quiz tabs, rendered from `state.result` (plan) and `state.materials` (stage 2).
  - `#print-pack` is rebuilt by `buildPrintPack()` and shown only under `@media print` (and only when `body.has-pack` is set). "Download teacher pack" calls `window.print()`, so there is no server-side PDF.
  - `missing_details[].field` is a wizard `data-field`. "Regenerate with this" writes the answer into that field and reruns `generate()`.
  - Build DOM with `textContent` only (the `el()` helper). Never put server data into `innerHTML`.
  - Client validation mirrors the server limits via input attributes. If you change a limit or enum in `api.py`, update the matching input.

## Tests

`tests/conftest.py` redirects `config.LOG_DIR` to a temp dir before importing `api`. The `fake_backends` fixture monkeypatches `llm.call_agent` and `run.retrieve` with canned per-agent outputs, and the `events` fixture captures structured log events for assertions.
