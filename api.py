"""FastAPI wrapper around the multi-agent TPACK pipeline.

Every error leaves this app in one envelope (see errors.py):

    {"error": {"code", "message", "details": [{"field", "message"}], "trace_id"}}

and every response carries an X-Trace-Id header that matches the trace_id in
logs/interactions.jsonl.
"""

import logging
import os
import time
from typing import Any, List, Literal, Optional

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

import config
from errors import AppError, NotFoundError
from logging_setup import configure_logging, current_trace_id, log_event, start_trace
from retrieval import get_chunk
from lesson_schema import LessonMaterials, LessonPlan
from run import run_materials, run_pipeline
from validation import sanitize_text

configure_logging()

app = FastAPI(title="AgentTPACK — Multi-agent TPACK PoC")


# --- Request models ----------------------------------------------------------


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @field_validator("*", mode="before")
    @classmethod
    def _clean_text(cls, value):
        if isinstance(value, str):
            value = sanitize_text(value)
            return value or None
        return value


FreeText = Optional[str]


class ContentKnowledge(_Strict):
    topic: str = Field(min_length=config.TOPIC_MIN_LEN, max_length=config.TOPIC_MAX_LEN)
    duration: int = Field(ge=config.DURATION_MIN, le=config.DURATION_MAX)
    chapter_num: int = Field(default=1, ge=1, le=20)
    grade: Literal[7] = 7


class Learners(_Strict):
    class_size: int = Field(ge=config.CLASS_SIZE_MIN, le=config.CLASS_SIZE_MAX)
    prior_knowledge: Literal["new", "partial", "revision"]
    classroom_format: Optional[Literal["whole_class", "small_groups", "pairs", "mixed"]] = None
    assessment_style: Optional[Literal["formative", "summative", "none"]] = None
    language_of_instruction: FreeText = Field(default=None, max_length=60)
    accessibility_needs: FreeText = Field(default=None, max_length=config.FREE_TEXT_MAX)
    continuity_notes: FreeText = Field(default=None, max_length=config.FREE_TEXT_MAX)


class PedagogicalKnowledge(_Strict):
    method: Literal["5E", "inquiry", "direct", "socratic"]
    blooms_focus: Literal["understand", "analyze", "create"]


class TechnologicalKnowledge(_Strict):
    infrastructure: Literal["projector", "1to1", "lab", "lowtech"]
    smart_board_available: Optional[bool] = None
    projector_available: Optional[bool] = None
    board_type: Optional[Literal["blackboard", "whiteboard", "none"]] = None
    student_devices: Literal["none", "shared", "1to1"]
    internet_access: Literal["reliable", "unreliable", "none"]
    tools: FreeText = Field(default=None, max_length=200)
    teacher_tech_comfort: Optional[Literal["low", "medium", "high"]] = None
    physical_tools_available: FreeText = Field(default=None, max_length=config.FREE_TEXT_MAX)
    policy_restrictions: FreeText = Field(default=None, max_length=config.FREE_TEXT_MAX)
    home_device_access: Optional[Literal["most", "some", "few", "none"]] = None
    budget_constraint: Optional[Literal["free_only", "paid_allowed"]] = None


class TPACKRequest(_Strict):
    content: ContentKnowledge
    learners: Learners
    pedagogy: PedagogicalKnowledge
    technology: TechnologicalKnowledge
    other_requests: FreeText = Field(default=None, max_length=config.FREE_TEXT_MAX)


# --- Error envelope ------------------------------------------------------------


def error_response(status, code, message, details=None):
    trace_id = current_trace_id()
    response = JSONResponse(
        status_code=status,
        content={
            "error": {
                "code": code,
                "message": message,
                "details": details or [],
                "trace_id": trace_id,
            }
        },
    )
    if trace_id:
        response.headers["X-Trace-Id"] = trace_id
    return response


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError):
    log_event(
        "api.error",
        "api",
        level=logging.WARNING if exc.http_status < 500 else logging.ERROR,
        code=exc.code,
        status=exc.http_status,
        fields=[d.get("field") for d in exc.details],
    )
    return error_response(exc.http_status, exc.code, exc.message, exc.details)


_FRIENDLY = {
    "missing": "This field is required.",
    "extra_forbidden": "Unknown field.",
    "json_invalid": "The request body is not valid JSON.",
    "literal_error": "Choose one of the listed options.",
    "int_parsing": "Enter a whole number.",
    "string_too_short": "This is too short.",
    "string_type": "Enter some text.",
    "bool_parsing": "Choose yes or no.",
}


@app.exception_handler(RequestValidationError)
async def request_validation_handler(request: Request, exc: RequestValidationError):
    details = []
    for err in exc.errors():
        loc = [str(p) for p in err.get("loc", ()) if p not in ("body",)]
        field = ".".join(loc) or "body"
        if err.get("type") == "json_invalid":
            field = "body"
        message = _FRIENDLY.get(err.get("type"), err.get("msg", "Invalid value."))
        details.append({"field": field, "message": message})
    log_event(
        "validation.rejected",
        "validator",
        level=logging.WARNING,
        code="invalid_input",
        fields=[d["field"] for d in details],
    )
    return error_response(
        422,
        "invalid_input",
        "Some lesson details are missing or invalid. See the highlighted fields.",
        details,
    )


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(request: Request, exc: StarletteHTTPException):
    if not request.url.path.startswith("/api/"):
        return await http_exception_handler(request, exc)
    code = "not_found" if exc.status_code == 404 else "http_error"
    return error_response(exc.status_code, code, str(exc.detail))


@app.middleware("http")
async def trace_middleware(request: Request, call_next):
    trace_id = start_trace(
        request.headers.get("x-trace-id") or request.headers.get("x-request-id")
    )
    is_api = request.url.path.startswith("/api/")
    started = time.perf_counter()
    if is_api:
        log_event("human.request", "human", method=request.method, path=request.url.path)

    try:
        response = await call_next(request)
    except Exception as exc:  # anything the route-level handlers did not catch
        log_event(
            "api.error",
            "api",
            level=logging.ERROR,
            exc_info=exc,
            code="internal_error",
            error_type=type(exc).__name__,
        )
        response = error_response(
            500,
            "internal_error",
            "Something went wrong on the server. Quote the trace id if you report it.",
        )

    response.headers["X-Trace-Id"] = trace_id
    if is_api:
        log_event(
            "human.response",
            "api",
            status=response.status_code,
            duration_ms=round((time.perf_counter() - started) * 1000),
        )
    return response


# --- Endpoints -----------------------------------------------------------------


class MissingDetail(BaseModel):
    field: Optional[str] = None  # wizard data-field that answers it, if any
    question: str


class PlanOut(LessonPlan):
    missing_details: List[MissingDetail] = Field(default_factory=list)


class AgentStep(BaseModel):
    seq: int
    agent: str
    ms: int
    status: str


class PlanNotes(BaseModel):
    approach_summary: Optional[str] = None
    pk_feasibility: List[Any] = Field(default_factory=list)
    tk_tool_status: List[Any] = Field(default_factory=list)
    removed_references: int = 0  # cited passages that were not retrieved


class GenerateResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    trace_id: Optional[str]
    lesson: PlanOut
    notes: PlanNotes
    warnings: List[str]
    agent_sequence: List[AgentStep]
    retrieved_ids: List[str] = Field(alias="_retrieved_ids")


class MaterialsRequest(_Strict):
    request: TPACKRequest
    plan: LessonPlan


class MaterialsResponse(BaseModel):
    trace_id: Optional[str]
    materials: LessonMaterials
    agent_sequence: List[AgentStep]


@app.post("/api/generate", response_model=GenerateResponse)
def generate(body: TPACKRequest):
    log_event("human.lesson_submitted", "human", inputs=body.model_dump(exclude_none=True))
    return run_pipeline(body)


@app.post("/api/materials", response_model=MaterialsResponse)
def materials(body: MaterialsRequest):
    """Stage 2: handouts and exit ticket for a plan returned by /api/generate."""
    log_event(
        "human.materials_requested",
        "human",
        topic=body.request.content.topic,
        plan_title=body.plan.overview.title,
        phases=[p.name for p in body.plan.phases],
    )
    return run_materials(body.request, body.plan.model_dump())


@app.get("/api/chunks/{chunk_id}")
def read_chunk(chunk_id: str):
    chunk = get_chunk(chunk_id)
    log_event("human.citation_view", "human", chunk_id=chunk_id, found=chunk is not None)
    if chunk is None:
        raise NotFoundError(
            f"No textbook passage with id {chunk_id}.",
            details=[{"field": "chunk_id", "message": "Unknown citation id."}],
        )
    return chunk


# MUST be last: this mount is a catch-all, so any route registered after it is unreachable.
app.mount(
    "/",
    StaticFiles(directory=os.path.join(os.path.dirname(os.path.abspath(__file__)), "static"), html=True),
    name="static",
)
