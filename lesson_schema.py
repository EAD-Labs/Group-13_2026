"""Structured output of the lesson-writing agents.

The TPACK integrator returns a LessonPlan; the materials agent (second stage)
returns LessonMaterials. agents.run_agent validates each against these models
and retries once with the validation errors if the model gets it wrong. The
frontend renders the result screen and the print view from the same shapes.
"""

from typing import Annotated, List, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

# Bounds keep a plan echoed back by the browser (POST /api/materials) small.
Text = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]
Line = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=600)]
Short = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]

BLOOM_LEVELS = ("Remember", "Understand", "Apply", "Analyze", "Evaluate", "Create")
Bloom = Literal["Remember", "Understand", "Apply", "Analyze", "Evaluate", "Create"]

_BLOOM_ALIASES = {"analyse": "Analyze", "understanding": "Understand", "remembering": "Remember"}


class _Out(BaseModel):
    # Models sometimes add fields; ignore them rather than fail the lesson.
    model_config = ConfigDict(extra="ignore")


# --- Stage 1: the plan -------------------------------------------------------


class PhaseQuestion(_Out):
    q: Line
    expected_answer: Line


class Phase(_Out):
    name: Short
    minutes: int = Field(ge=1, le=180)
    teacher_steps: List[Line] = Field(min_length=1, max_length=15)
    student_actions: List[Line] = Field(default_factory=list, max_length=15)
    board_notes: Text = ""
    questions: List[PhaseQuestion] = Field(default_factory=list, max_length=10)
    misconceptions: List[Line] = Field(default_factory=list, max_length=10)
    if_tech_missing: Text = ""
    sources: List[Short] = Field(default_factory=list, max_length=10)


class Overview(_Out):
    title: Short
    duration_min: int = Field(ge=1, le=240)
    objectives: List[Line] = Field(min_length=1, max_length=8)
    materials: List[Line] = Field(default_factory=list, max_length=30)
    prep_before_class: List[Line] = Field(default_factory=list, max_length=20)


class LessonPlan(_Out):
    overview: Overview
    phases: List[Phase] = Field(min_length=1, max_length=10)
    not_covered: List[Line] = Field(default_factory=list, max_length=20)


# --- Stage 2: handouts and exit ticket ---------------------------------------


class Handout(_Out):
    title: Short
    for_phase: Short
    instructions: Text
    items: List[Line] = Field(min_length=1, max_length=15)


class ExitQuestion(_Out):
    q: Line
    type: Literal["mcq"] = "mcq"
    options: List[Line] = Field(min_length=2, max_length=6)
    answer: Line
    why: Line
    bloom: Bloom

    @field_validator("bloom", mode="before")
    @classmethod
    def _normalise_bloom(cls, value):
        if isinstance(value, str):
            key = value.strip().lower()
            if key in _BLOOM_ALIASES:
                return _BLOOM_ALIASES[key]
            return key.capitalize()
        return value

    @model_validator(mode="after")
    def _answer_is_an_option(self):
        if len({o.lower() for o in self.options}) != len(self.options):
            raise ValueError("options must all be different")
        if self.answer not in self.options:
            raise ValueError(
                f"answer {self.answer!r} must be exactly one of the options {self.options!r}"
            )
        return self


class LessonMaterials(_Out):
    handouts: List[Handout] = Field(min_length=1, max_length=6)
    exit_ticket: List[ExitQuestion] = Field(min_length=3, max_length=8)
