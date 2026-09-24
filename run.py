"""Multi-agent TPACK pipeline.

Stage 1, POST /api/generate (run_pipeline):
    validate -> retrieve -> CK -> PK -> TK -> TPACK integrator -> LessonPlan

Stage 2, POST /api/materials (run_materials):
    validate -> retrieve -> materials agent (given the plan) -> LessonMaterials

CK, PK and TK are foundational agents that never see each other's output.
The TPACK integrator receives all three (logged as agent.handoff events) plus
the retrieved chunks. Handouts and the exit ticket are a separate call so a
failure there never loses the plan.
"""

import re

from pydantic import ValidationError

from agents import CK, MATERIALS, PK, TK, TPACK, run_agent
from errors import AgentOutputError
from lesson_schema import LessonMaterials, LessonPlan
from logging_setup import current_trace_id, log_event
from retrieval import format_chunks, retrieve
from validation import chapter_name, check_consistency, check_relevance, check_safety

import config

NS = config.NOT_SPECIFIED

# Human-readable values for the prompts. Keys are the enum values api.py accepts.
METHOD_LABELS = {
    "5E": "5E Model (Engage, Explore, Explain, Elaborate, Evaluate)",
    "inquiry": "Inquiry-based learning",
    "direct": "Explicit / direct instruction",
    "socratic": "Socratic dialogue / discussion-based",
}
BLOOMS_LABELS = {
    "understand": "Remember & Understand",
    "analyze": "Apply & Analyze",
    "create": "Evaluate & Create",
}
INFRASTRUCTURE_LABELS = {
    "projector": "Teacher smart board / projector",
    "1to1": "1:1 student devices",
    "lab": "Computer lab",
    "lowtech": "Low-tech (blackboard / printouts)",
}
PRIOR_KNOWLEDGE_LABELS = {
    "new": "New topic (no prior exposure)",
    "partial": "Partial exposure",
    "revision": "Revision of a familiar topic",
}
CLASSROOM_FORMAT_LABELS = {
    "whole_class": "Whole-class",
    "small_groups": "Small groups",
    "pairs": "Pairs",
    "mixed": "Mixed",
}
STUDENT_DEVICE_LABELS = {
    "none": "None",
    "shared": "Shared devices (a few per class)",
    "1to1": "One device per student",
}
BUDGET_LABELS = {"free_only": "Free tools only", "paid_allowed": "Paid tools allowed"}

# Missing inputs reported by the PK/TK agents, as a question for the teacher
# and the wizard field that answers it. The field is a data-field name in
# static/index.html, so the result screen can offer "Regenerate with this".
MISSING_DETAIL_QUESTIONS = {
    "class_size": ("learners.class_size", "How many students are in the class?"),
    "prior_knowledge": ("learners.prior_knowledge", "Have your students met this topic before?"),
    "classroom_format": ("learners.classroom_format", "Should students work as a whole class, in groups or in pairs?"),
    "assessment_style": ("learners.assessment_style", "What kind of check for understanding do you want?"),
    "language_of_instruction": ("learners.language_of_instruction", "Which language do you teach this class in?"),
    "accessibility_needs": ("learners.accessibility_needs", "Do any students need extra support (for example with seeing or hearing)?"),
    "continuity_notes": ("learners.continuity_notes", "What did the class cover in the previous lesson?"),
    "projector_available": ("technology.projector_available", "Do you have a projector?"),
    "smart_board_available": ("technology.smart_board_available", "Do you have a smart board?"),
    "board_type": ("technology.board_type", "Do you have a blackboard or a whiteboard?"),
    "student_devices": ("technology.student_devices", "Do students have devices (tablets, laptops) in class?"),
    "internet_access": ("technology.internet_access", "Is there internet access in your classroom?"),
    "mentioned_tools": ("technology.tools", "Which digital tools would you like to use?"),
    "tools": ("technology.tools", "Which digital tools would you like to use?"),
    "teacher_tech_comfort": ("technology.teacher_tech_comfort", "How comfortable are you using technology in class?"),
    "physical_tools_available": ("technology.physical_tools_available", "What materials do you have in the room (charts, models, lab equipment)?"),
    "policy_restrictions": ("technology.policy_restrictions", "Does your school restrict any technology in class?"),
    "home_device_access": ("technology.home_device_access", "How many students have a device at home?"),
    "budget_constraint": ("technology.budget_constraint", "Can you use paid tools, or only free ones?"),
}

# Chunk ids that leaked into prose, with optional surrounding brackets.
_CHUNK_ID_IN_TEXT = re.compile(r"\s*[\[(]?\bg\d+_ch\d{2}_s\d+\.\d+_\d{3}\b[\])]?")


def _yes_no(value):
    if value is None:
        return NS
    return "yes" if value else "no"


def _or_ns(value, labels=None):
    if value is None or value == "":
        return NS
    if labels is not None:
        return labels.get(value, value)
    return value


def safety_fields(req):
    """Every free-text field, keyed by the dotted name the frontend uses."""
    return {
        "content.topic": req.content.topic,
        "learners.language_of_instruction": req.learners.language_of_instruction,
        "learners.accessibility_needs": req.learners.accessibility_needs,
        "learners.continuity_notes": req.learners.continuity_notes,
        "technology.tools": req.technology.tools,
        "technology.physical_tools_available": req.technology.physical_tools_available,
        "technology.policy_restrictions": req.technology.policy_restrictions,
        "other_requests": req.other_requests,
    }


def _classroom_summary(req):
    """One readable line of the hard classroom facts, for the integrator."""
    tech, learners = req.technology, req.learners
    parts = [
        f"{learners.class_size} students",
        f"setup: {INFRASTRUCTURE_LABELS[tech.infrastructure]}",
        f"student devices: {STUDENT_DEVICE_LABELS[tech.student_devices]}",
        f"internet: {tech.internet_access}",
    ]
    if tech.projector_available is not None:
        parts.append(f"projector: {_yes_no(tech.projector_available)}")
    if tech.smart_board_available is not None:
        parts.append(f"smart board: {_yes_no(tech.smart_board_available)}")
    if tech.board_type:
        parts.append(f"board: {tech.board_type}")
    if tech.physical_tools_available:
        parts.append(f"materials in the room: {tech.physical_tools_available}")
    return "; ".join(parts)


def _retrieve_for(req):
    chunks = retrieve(req.content.topic, chapter_num=req.content.chapter_num)
    check_relevance(chunks, req.content.chapter_num)
    return chunks


def run_pipeline(req):
    """Stage 1: run the agents up to the lesson plan for a validated TPACKRequest."""
    check_safety(safety_fields(req))
    warnings = check_consistency(req)

    content, learners, pedagogy, tech = req.content, req.learners, req.pedagogy, req.technology
    chapter = chapter_name(content.chapter_num)
    log_event("validation.passed", "validator", warnings=len(warnings))

    chunks = _retrieve_for(req)
    retrieved_ids = [c["id"] for c in chunks]
    formatted_chunks = format_chunks(chunks)

    steps = []

    ck, step = run_agent(
        CK,
        {
            "grade": content.grade,
            "chapter": chapter,
            "topic": content.topic,
            "duration": content.duration,
            "chunks": formatted_chunks,
        },
    )
    steps.append(step)

    pk, step = run_agent(
        PK,
        {
            "grade": content.grade,
            "topic": content.topic,
            "duration": content.duration,
            "class_size": learners.class_size,
            "prior_knowledge": PRIOR_KNOWLEDGE_LABELS[learners.prior_knowledge],
            "preferred_approach": METHOD_LABELS[pedagogy.method],
            "classroom_format": _or_ns(learners.classroom_format, CLASSROOM_FORMAT_LABELS),
            "assessment_style": _or_ns(learners.assessment_style),
            "language_of_instruction": _or_ns(learners.language_of_instruction),
            "accessibility_needs": _or_ns(learners.accessibility_needs),
            "continuity_notes": _or_ns(learners.continuity_notes),
            "other_requests": _or_ns(req.other_requests),
        },
    )
    steps.append(step)

    tk, step = run_agent(
        TK,
        {
            "grade": content.grade,
            "class_size": learners.class_size,
            "smart_board_available": _yes_no(tech.smart_board_available),
            "projector_available": _yes_no(tech.projector_available),
            "board_type": _or_ns(tech.board_type),
            "student_devices": STUDENT_DEVICE_LABELS[tech.student_devices],
            "internet_access": tech.internet_access,
            "mentioned_tools": _or_ns(tech.tools),
            "teacher_tech_comfort": _or_ns(tech.teacher_tech_comfort),
            "physical_tools_available": _or_ns(tech.physical_tools_available),
            "policy_restrictions": _or_ns(tech.policy_restrictions),
            "home_device_access": _or_ns(tech.home_device_access),
            "accessibility_needs": _or_ns(learners.accessibility_needs),
            "budget_constraint": _or_ns(tech.budget_constraint, BUDGET_LABELS),
        },
    )
    steps.append(step)

    plan, step = run_agent(
        TPACK,
        {
            "grade": content.grade,
            "chapter": chapter,
            "topic": content.topic,
            "duration": content.duration,
            "method": METHOD_LABELS[pedagogy.method],
            "blooms_focus": BLOOMS_LABELS[pedagogy.blooms_focus],
            "classroom": _classroom_summary(req),
            "tools": _or_ns(tech.tools),
            "chunks": formatted_chunks,
        },
        upstream={"ck": ck, "pk": pk, "tk": tk},
    )
    steps.append(step)

    removed_references = _strip_invalid_citations(plan, retrieved_ids)
    _scrub_ids_from_text(plan)

    total = sum(p["minutes"] for p in plan["phases"])
    if total != content.duration:
        warnings.append(
            f"The phases add up to {total} minutes, not the {content.duration} you asked for. Adjust the timings as needed."
        )

    for gap in ck.get("coverage_gaps") or []:
        if isinstance(gap, str) and gap.strip() and gap not in plan["not_covered"]:
            plan["not_covered"].append(gap.strip()[:600])
    plan["not_covered"] = plan["not_covered"][:20]

    # Post-processing must not produce a plan the response model rejects
    # (that would surface as a 500); report it as a bad agent answer instead.
    try:
        plan = LessonPlan.model_validate(plan).model_dump()
    except ValidationError as e:
        log_event("agent.error", "agent:tpack", reason="postprocess_invalid", error=str(e)[:1000])
        raise AgentOutputError("The lesson plan came back incomplete. Please try again.") from e

    missing = list(pk.get("missing_inputs") or []) + list(tk.get("missing_inputs") or [])
    plan["missing_details"] = missing_details(missing, req)

    return {
        "trace_id": current_trace_id(),
        "lesson": plan,
        "notes": {
            "approach_summary": pk.get("approach_summary"),
            "pk_feasibility": pk.get("feasibility_notes") or [],
            "tk_tool_status": tk.get("requested_tools_status") or [],
            "removed_references": removed_references,
        },
        "warnings": warnings,
        "agent_sequence": [{"seq": i + 1, **s} for i, s in enumerate(steps)],
        "_retrieved_ids": retrieved_ids,
    }


def run_materials(req, plan):
    """Stage 2: handouts and the exit ticket for a plan from stage 1.

    `plan` is a validated LessonPlan dict echoed back by the browser, so the
    request is re-checked and the passages re-retrieved (local, no LLM call).
    """
    check_safety(safety_fields(req))
    chunks = _retrieve_for(req)

    materials, step = run_agent(
        MATERIALS,
        {
            "grade": req.content.grade,
            "topic": req.content.topic,
            "blooms_focus": BLOOMS_LABELS[req.pedagogy.blooms_focus],
            "chunks": format_chunks(chunks),
        },
        upstream={"tpack": plan},
    )
    _scrub_ids_from_text(materials)
    try:
        materials = LessonMaterials.model_validate(materials).model_dump()
    except ValidationError as e:
        log_event("agent.error", "agent:materials", reason="postprocess_invalid", error=str(e)[:1000])
        raise AgentOutputError("The handouts and quiz came back incomplete. Please try again.") from e
    return {
        "trace_id": current_trace_id(),
        "materials": materials,
        "agent_sequence": [{"seq": 1, **step}],
    }


def missing_details(names, req):
    """Turn agent-reported missing input names into teacher questions.

    Fields the teacher actually filled in are skipped (the agent was wrong),
    and each wizard field is asked about at most once.
    """
    details, seen = [], set()
    for raw in names:
        if not isinstance(raw, str) or not raw.strip():
            continue
        key = _normalise_field_name(raw)
        match = MISSING_DETAIL_QUESTIONS.get(key) or next(
            (v for k, v in MISSING_DETAIL_QUESTIONS.items() if k in key), None
        )
        if match is None:
            question = "Could you tell us more about " + raw.strip().replace("_", " ")[:120] + "?"
            if question not in seen:
                seen.add(question)
                details.append({"field": None, "question": question})
            continue
        field, question = match
        if field in seen or _request_value(req, field) not in (None, ""):
            continue
        seen.add(field)
        details.append({"field": field, "question": question})
    return details


def _normalise_field_name(raw):
    name = raw.strip().strip("`'\"").split(":")[0].split(".")[-1]
    return re.sub(r"[\s\-]+", "_", name.strip().lower())


def _request_value(req, dotted):
    section, _, field = dotted.partition(".")
    return getattr(getattr(req, section, None), field, None)


def _strip_invalid_citations(plan, retrieved_ids):
    """Drop cited ids the model was never given. Returns how many were dropped."""
    allowed = set(retrieved_ids)
    invalid = []
    for phase in plan.get("phases") or []:
        cited = phase.get("sources") or []
        invalid += [c for c in cited if c not in allowed]
        phase["sources"] = list(dict.fromkeys(c for c in cited if c in allowed))
    if invalid:
        log_event("citation.invalid", "agent:tpack", invalid_ids=invalid, allowed=retrieved_ids)
    return len(invalid)


def _scrub_ids_from_text(node, key=None):
    """Remove chunk ids that leaked into teacher- or student-facing text.
    `sources` lists are left alone."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "sources":
                continue
            if isinstance(v, str):
                node[k] = _CHUNK_ID_IN_TEXT.sub("", v).strip()
            else:
                _scrub_ids_from_text(v, k)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            if isinstance(v, str):
                node[i] = _CHUNK_ID_IN_TEXT.sub("", v).strip()
            else:
                _scrub_ids_from_text(v, key)
        # A list item that was only an id is now empty; drop it.
        node[:] = [v for v in node if v != ""]
