"""Read/write access to a teacher's classroom profile and the context log.

The profile tables (teachers, sections, section_infra, section_pedagogy) are
the current, reusable state of a classroom — read back on every extraction
and every lesson request. context_log is a separate, append-only record of
every teacher input and every agent call, written here but never read back
into a prompt anywhere in this codebase.
"""

import json

from db import get_connection


def get_profile(teacher_id, section_name):
    """Return the saved profile for one teacher/section as a nested dict, or
    None if nothing has been saved yet."""
    conn = get_connection()
    try:
        teacher = conn.execute(
            "SELECT * FROM teachers WHERE id = ?", (teacher_id,)
        ).fetchone()
        if teacher is None:
            return None

        section = conn.execute(
            "SELECT * FROM sections WHERE teacher_id = ? AND name = ?",
            (teacher_id, section_name),
        ).fetchone()
        if section is None:
            return None

        infra = conn.execute(
            "SELECT * FROM section_infra WHERE section_id = ?", (section["id"],)
        ).fetchone()
        pedagogy = conn.execute(
            "SELECT * FROM section_pedagogy WHERE section_id = ?", (section["id"],)
        ).fetchone()

        return {
            "teacher": {
                "id": teacher["id"],
                "name": teacher["name"],
                "subject": teacher["subject"],
                "years_experience": teacher["years_experience"],
                "tech_comfort": teacher["tech_comfort"],
                "medium_of_instruction": teacher["medium_of_instruction"],
            },
            "section": {
                "id": section["id"],
                "name": section["name"],
                "grade": section["grade"],
                "num_students": section["num_students"],
                "ability_spread": section["ability_spread"],
                "language_gap": section["language_gap"],
                "access_notes": section["access_notes"],
            },
            "infra": {
                "tech_available": json.loads(infra["tech_available"])
                if infra and infra["tech_available"]
                else [],
                "tech_reliability": infra["tech_reliability"] if infra else None,
                "device_model": infra["device_model"] if infra else None,
                "power_reliability": infra["power_reliability"] if infra else None,
            },
            "pedagogy": {
                "preferred_styles": json.loads(pedagogy["preferred_styles"])
                if pedagogy and pedagogy["preferred_styles"]
                else [],
                "style_comfort": pedagogy["style_comfort"] if pedagogy else None,
                "assessment_style": pedagogy["assessment_style"] if pedagogy else None,
            },
        }
    finally:
        conn.close()


def save_profile(teacher_id, profile):
    """Upsert a verified profile. `profile` has the same shape returned by
    get_profile (teacher/section/infra/pedagogy dicts). Returns the section id."""
    teacher = profile.get("teacher") or {}
    section = profile["section"]
    infra = profile.get("infra") or {}
    pedagogy = profile.get("pedagogy") or {}

    if not section.get("name"):
        raise ValueError("profile.section.name is required to save a profile")

    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO teachers (id, name, subject, years_experience, tech_comfort, medium_of_instruction, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                subject = excluded.subject,
                years_experience = excluded.years_experience,
                tech_comfort = excluded.tech_comfort,
                medium_of_instruction = excluded.medium_of_instruction,
                updated_at = datetime('now')
            """,
            (
                teacher_id,
                teacher.get("name"),
                teacher.get("subject"),
                teacher.get("years_experience"),
                teacher.get("tech_comfort"),
                teacher.get("medium_of_instruction"),
            ),
        )

        conn.execute(
            """
            INSERT INTO sections (teacher_id, name, grade, num_students, ability_spread, language_gap, access_notes, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(teacher_id, name) DO UPDATE SET
                grade = excluded.grade,
                num_students = excluded.num_students,
                ability_spread = excluded.ability_spread,
                language_gap = excluded.language_gap,
                access_notes = excluded.access_notes,
                updated_at = datetime('now')
            """,
            (
                teacher_id,
                section["name"],
                section.get("grade"),
                section.get("num_students"),
                section.get("ability_spread"),
                section.get("language_gap"),
                section.get("access_notes"),
            ),
        )
        section_id = conn.execute(
            "SELECT id FROM sections WHERE teacher_id = ? AND name = ?",
            (teacher_id, section["name"]),
        ).fetchone()["id"]

        conn.execute(
            """
            INSERT INTO section_infra (section_id, tech_available, tech_reliability, device_model, power_reliability, updated_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(section_id) DO UPDATE SET
                tech_available = excluded.tech_available,
                tech_reliability = excluded.tech_reliability,
                device_model = excluded.device_model,
                power_reliability = excluded.power_reliability,
                updated_at = datetime('now')
            """,
            (
                section_id,
                json.dumps(infra.get("tech_available") or []),
                infra.get("tech_reliability"),
                infra.get("device_model"),
                infra.get("power_reliability"),
            ),
        )

        conn.execute(
            """
            INSERT INTO section_pedagogy (section_id, preferred_styles, style_comfort, assessment_style, updated_at)
            VALUES (?, ?, ?, ?, datetime('now'))
            ON CONFLICT(section_id) DO UPDATE SET
                preferred_styles = excluded.preferred_styles,
                style_comfort = excluded.style_comfort,
                assessment_style = excluded.assessment_style,
                updated_at = datetime('now')
            """,
            (
                section_id,
                json.dumps(pedagogy.get("preferred_styles") or []),
                pedagogy.get("style_comfort"),
                pedagogy.get("assessment_style"),
            ),
        )

        conn.commit()
        return section_id
    finally:
        conn.close()


def log_teacher_input(teacher_id, section_id, payload):
    """Append a teacher-input event to context_log. Write-only: nothing in
    this module ever reads context_log back into a prompt."""
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO context_log (teacher_id, section_id, event_type, input_payload)
            VALUES (?, ?, 'teacher_input', ?)
            """,
            (teacher_id, section_id, json.dumps(payload)),
        )
        conn.commit()
    finally:
        conn.close()


def log_agent_call(teacher_id, section_id, agent_name, prompt_text, raw_response):
    """Append an agent-call event to context_log. Called from llm.call_agent
    for every LLM invocation that has a teacher_id — never by prompt-building
    code, and never read back by any agent."""
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO context_log (teacher_id, section_id, event_type, agent_name, prompt_text, raw_response)
            VALUES (?, ?, 'agent_call', ?, ?, ?)
            """,
            (teacher_id, section_id, agent_name, prompt_text, raw_response),
        )
        conn.commit()
    finally:
        conn.close()


def record_syllabus_progress(section_id, chapter, topic, source="ck_agent"):
    """Append a row noting that `topic` (in `chapter`) was generated for this
    section. Read back by nothing yet — a structured record for a future
    'what have I already covered' feature, not the raw log."""
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO syllabus_progress (section_id, chapter, topic, source)
            VALUES (?, ?, ?, ?)
            """,
            (section_id, chapter, topic, source),
        )
        conn.commit()
    finally:
        conn.close()
