"""FastAPI wrapper around the single-agent RAG pipeline."""

from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import db
from context_agent import run_context_agent
from lesson_request_agent import run_lesson_request_agent
from teacher_profile import get_profile, log_teacher_input, save_profile
from retrieval import get_chunk
from run import run_ck

db.init_db()

app = FastAPI(title="AgentTPACK — Content Knowledge PoC")


class GenerateRequest(BaseModel):
    topic: str
    chapter: str = "Nutrition in Plants"
    chapter_num: int = 1
    grade: int = 7
    duration: int = 45
    num_students: Optional[int] = None
    tech_availability: List[str] = []
    pedagogy: List[str] = []
    teacher_id: Optional[str] = None
    section_name: Optional[str] = None


@app.post("/api/generate")
def generate(body: GenerateRequest):
    try:
        grade = body.grade
        num_students = body.num_students
        tech_availability = body.tech_availability
        pedagogy = body.pedagogy
        section_id = None

        # A saved profile is the source of truth for classroom fields — it
        # overrides whatever the client sent for them, since the whole point
        # is the client stops asking for these once a profile exists.
        if body.teacher_id and body.section_name:
            profile = get_profile(body.teacher_id, body.section_name)
            if profile:
                section_id = profile["section"]["id"]
                grade = profile["section"]["grade"] or grade
                num_students = profile["section"]["num_students"] or num_students
                tech_availability = profile["infra"]["tech_available"] or tech_availability
                pedagogy = profile["pedagogy"]["preferred_styles"] or pedagogy

        return run_ck(
            topic=body.topic,
            chapter=body.chapter,
            chapter_num=body.chapter_num,
            grade=grade,
            duration=body.duration,
            num_students=num_students,
            tech_availability=tech_availability,
            pedagogy=pedagogy,
            teacher_id=body.teacher_id,
            section_id=section_id,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/chunks/{chunk_id}")
def read_chunk(chunk_id: str):
    chunk = get_chunk(chunk_id)
    if chunk is None:
        raise HTTPException(status_code=404, detail=f"No chunk with id {chunk_id}")
    return chunk


class ProfileExtractRequest(BaseModel):
    section_name: str
    narrative: str


class ProfileSaveRequest(BaseModel):
    teacher: Dict[str, Any] = {}
    section: Dict[str, Any]
    infra: Dict[str, Any] = {}
    pedagogy: Dict[str, Any] = {}


@app.post("/api/profile/{teacher_id}/extract")
def extract_profile(teacher_id: str, body: ProfileExtractRequest):
    """Extract a draft profile from free text. Does not save anything —
    the teacher must review the draft and call PUT /api/profile/{teacher_id}
    to confirm it."""
    try:
        current = get_profile(teacher_id, body.section_name)
        section_id = current["section"]["id"] if current else None

        log_teacher_input(
            teacher_id,
            section_id,
            {"section_name": body.section_name, "narrative": body.narrative},
        )

        return run_context_agent(
            narrative=body.narrative,
            current_profile=current,
            teacher_id=teacher_id,
            section_id=section_id,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/profile/{teacher_id}")
def put_profile(teacher_id: str, body: ProfileSaveRequest):
    """Save a teacher-confirmed profile. Called only after verification —
    never fed a draft directly from /extract without the teacher seeing it."""
    try:
        save_profile(teacher_id, body.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return get_profile(teacher_id, body.section["name"])


@app.get("/api/profile/{teacher_id}")
def read_profile(teacher_id: str, section: str):
    profile = get_profile(teacher_id, section)
    if profile is None:
        raise HTTPException(
            status_code=404,
            detail=f"No profile for teacher {teacher_id!r}, section {section!r}",
        )
    return profile


class LessonRequestExtractRequest(BaseModel):
    section_name: Optional[str] = None
    narrative: str


@app.post("/api/lesson-request/{teacher_id}/extract")
def extract_lesson_request(teacher_id: str, body: LessonRequestExtractRequest):
    """Extract topic + this-lesson-only pedagogy/tech preferences from free
    text. Never saved — only pre-fills the lesson form for review."""
    try:
        section_id = None
        if body.section_name:
            current = get_profile(teacher_id, body.section_name)
            section_id = current["section"]["id"] if current else None

        log_teacher_input(
            teacher_id,
            section_id,
            {"section_name": body.section_name, "narrative": body.narrative},
        )

        return run_lesson_request_agent(
            narrative=body.narrative,
            teacher_id=teacher_id,
            section_id=section_id,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# MUST be last: this mount is a catch-all, so any route registered after it is
# unreachable.
app.mount("/", StaticFiles(directory="static", html=True), name="static")
