"""FastAPI wrapper around the single-agent RAG pipeline."""

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from retrieval import get_chunk
from run import run_ck

app = FastAPI(title="AgentTPACK — Content Knowledge PoC")


class GenerateRequest(BaseModel):
    topic: str
    chapter: str = "Nutrition in Plants"
    chapter_num: int = 1
    grade: int = 7
    duration: int = 45


@app.post("/api/generate")
def generate(body: GenerateRequest):
    try:
        return run_ck(
            topic=body.topic,
            chapter=body.chapter,
            chapter_num=body.chapter_num,
            grade=body.grade,
            duration=body.duration,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/chunks/{chunk_id}")
def read_chunk(chunk_id: str):
    chunk = get_chunk(chunk_id)
    if chunk is None:
        raise HTTPException(status_code=404, detail=f"No chunk with id {chunk_id}")
    return chunk


# MUST be last: this mount is a catch-all, so any route registered after it is
# unreachable.
app.mount("/", StaticFiles(directory="static", html=True), name="static")
