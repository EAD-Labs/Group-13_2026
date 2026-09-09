"""SQLite schema for teacher profiles and the context log.

Run this once to create ./app.db and its tables:

    python db.py

Safe to re-run: every statement uses IF NOT EXISTS, so it never touches
existing data. Unlike ingest.py, this is never dropped and rebuilt — a
teacher's profile is not regenerable the way NCERT chunks are.
"""

import sqlite3

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS teachers (
    id TEXT PRIMARY KEY,
    name TEXT,
    subject TEXT,
    years_experience INTEGER,
    tech_comfort TEXT,
    medium_of_instruction TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    teacher_id TEXT NOT NULL REFERENCES teachers(id),
    name TEXT NOT NULL,
    grade INTEGER,
    num_students INTEGER,
    ability_spread TEXT,
    language_gap TEXT,
    access_notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (teacher_id, name)
);

CREATE TABLE IF NOT EXISTS section_infra (
    section_id INTEGER PRIMARY KEY REFERENCES sections(id),
    tech_available TEXT,
    tech_reliability TEXT,
    device_model TEXT,
    power_reliability TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS section_pedagogy (
    section_id INTEGER PRIMARY KEY REFERENCES sections(id),
    preferred_styles TEXT,
    style_comfort TEXT,
    assessment_style TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS syllabus_progress (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    section_id INTEGER NOT NULL REFERENCES sections(id),
    chapter TEXT NOT NULL,
    topic TEXT NOT NULL,
    covered_on TEXT NOT NULL DEFAULT (datetime('now')),
    source TEXT NOT NULL DEFAULT 'ck_agent'
);

CREATE TABLE IF NOT EXISTS context_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    teacher_id TEXT NOT NULL REFERENCES teachers(id),
    section_id INTEGER REFERENCES sections(id),
    event_type TEXT NOT NULL CHECK (event_type IN ('teacher_input', 'agent_call')),
    agent_name TEXT,
    input_payload TEXT,
    prompt_text TEXT,
    raw_response TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_sections_teacher ON sections(teacher_id);
CREATE INDEX IF NOT EXISTS idx_syllabus_section ON syllabus_progress(section_id);
CREATE INDEX IF NOT EXISTS idx_log_teacher ON context_log(teacher_id);
CREATE INDEX IF NOT EXISTS idx_log_section ON context_log(section_id);
"""


def get_connection():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_connection()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    init_db()
    print(f"initialized {config.DB_PATH}")
