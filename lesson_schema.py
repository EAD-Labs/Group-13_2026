"""Structured output of the lesson-writing agents.

The TPACK integrator returns a LessonPlan; the materials agent (second stage)
returns LessonMaterials. agents.run_agent validates each against these models
and retries once with the validation errors if the model gets it wrong. The
frontend renders the result screen and the print view from the same shapes.

The two screening agents (screening.py) return InputScreen and OutputScreen.
"""

from typing import Annotated, List, Literal, Union

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


class DesignDecision(_Out):
    """A trade-off the TPACK integrator settled, with what forced it."""

    decision: Line
    chosen: Line
    rejected: Text = ""
    because: List[Short] = Field(default_factory=list, max_length=10)
    agents: List[Short] = Field(default_factory=list, max_length=7)


class LessonPlan(_Out):
    overview: Overview
    phases: List[Phase] = Field(min_length=1, max_length=10)
    not_covered: List[Line] = Field(default_factory=list, max_length=20)
    design_decisions: List[DesignDecision] = Field(default_factory=list, max_length=12)


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


# --- Content screening (screening.py) ----------------------------------------


class InputIssue(_Out):
    field: Short  # dotted request field, e.g. content.topic
    category: Short
    reason: Line


class InputScreen(_Out):
    verdict: Literal["allow", "flag"]
    issues: List[InputIssue] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def _issues_decide(self):
        # Contradictory answers fail closed: any issue means the request is flagged.
        if self.issues:
            self.verdict = "flag"
        elif self.verdict == "flag":
            raise ValueError("a flagged request must list at least one issue")
        return self


class OutputIssue(_Out):
    path: List[Union[int, str]] = Field(min_length=1, max_length=8)
    problem: Line


class OutputScreen(_Out):
    verdict: Literal["clean", "flagged"]
    issues: List[OutputIssue] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def _issues_decide(self):
        if self.issues:
            self.verdict = "flagged"
        elif self.verdict == "flagged":
            raise ValueError("flagged content must list at least one issue")
        return self
