"""Central configuration for the AgentTPACK RAG proof-of-concept."""

CHROMA_PATH = "./chroma_store"
COLLECTION_NAME = "ncert_g7_biology"
EMBEDDING_MODEL = "all-MiniLM-L6-v2"

GRADE = 7
SOURCE = "NCERT Science Class 7"

CHUNK_SIZE = 1000
CHUNK_OVERLAP = 150

# IMPORTANT: verify these chapter numbers against YOUR OWN copy of the PDF.
# NCERT renumbered (and dropped) chapters in the 2023 rationalisation, so the
# chapter number printed in an older book will not match a current print run.
# The number recorded here becomes part of every chunk id and of the
# `chapter_num` metadata filter, so a wrong number silently breaks retrieval.
# Numbers and names below match the 2018-19 print (data/raw/). The names must
# match the chapter title pages exactly: prepare_textbook.py splits on them.
CHAPTERS = {
    "chapter01.txt": {"num": 1, "name": "Nutrition in Plants"},
    "chapter02.txt": {"num": 2, "name": "Nutrition in Animals"},
    "chapter03.txt": {"num": 3, "name": "Fibre to Fabric"},
    "chapter04.txt": {"num": 4, "name": "Heat"},
    "chapter05.txt": {"num": 5, "name": "Acids, Bases and Salts"},
    "chapter06.txt": {"num": 6, "name": "Physical and Chemical Changes"},
    "chapter07.txt": {"num": 7, "name": "Weather, Climate and Adaptations of Animals to Climate"},
    "chapter08.txt": {"num": 8, "name": "Winds, Storms and Cyclones"},
    "chapter09.txt": {"num": 9, "name": "Soil"},
    "chapter10.txt": {"num": 10, "name": "Respiration in Organisms"},
    "chapter11.txt": {"num": 11, "name": "Transportation in Animals and Plants"},
    "chapter12.txt": {"num": 12, "name": "Reproduction in Plants"},
    "chapter13.txt": {"num": 13, "name": "Motion and Time"},
    "chapter14.txt": {"num": 14, "name": "Electric Current and its Effects"},
    "chapter15.txt": {"num": 15, "name": "Light"},
    "chapter16.txt": {"num": 16, "name": "Water: A Precious Resource"},
    "chapter17.txt": {"num": 17, "name": "Forests: Our Lifeline"},
    "chapter18.txt": {"num": 18, "name": "Wastewater Story"},
}

# --- Logging -----------------------------------------------------------------

LOG_DIR = "./logs"
LOG_FILE = "interactions.jsonl"
LOG_MAX_BYTES = 5_000_000
LOG_BACKUP_COUNT = 5
# When True, full prompts and raw model responses are written to the log.
# Off by default: they are large and may contain teacher-entered text.
LOG_FULL_PAYLOADS = False
# One readable file per session (trace) under LOG_DIR/LOG_SESSION_DIR, showing
# which agent ran when, for how long, and what each one received, returned
# and handed on. LOG_SESSION_PAYLOADS includes those contents (they can echo
# teacher-entered text); the oldest files are deleted beyond the maximum.
LOG_SESSION_FILES = True
LOG_SESSION_DIR = "sessions"
LOG_SESSION_PAYLOADS = True
LOG_SESSION_MAX_FILES = 500

# --- LLM -----------------------------------------------------------------------

LLM_TIMEOUT_S = 60
# Waits (seconds) before each retry of the SAME model after an overload or
# rate-limit error. After these run out, the next model in
# GEMINI_FALLBACK_MODELS is tried with the same schedule.
LLM_RETRY_BACKOFF_S = (2, 6, 12)

# --- Retrieval relevance -----------------------------------------------------------

# Cosine distance (0 = identical). If even the closest chunk is further away
# than this, the topic is treated as not covered by the chapter. Calibrate
# against the real chapter text: log a few on- and off-topic queries and pick
# a value between the two clusters.
# Measured on chapter 1 (2026-09-24): on-topic 0.27 (photosynthesis) to 0.74
# (stomata, saprotrophs); off-topic 0.82 (black holes) to 0.89 (algebra).
# Short single-word topics score higher, so err towards letting them through;
# the CK agent still reports coverage gaps. "acids and bases" (0.67) passes,
# so this is a coarse filter, not a guarantee.
# Re-checked on the full 18-chapter book (2026-10-01), filtered by chapter:
# on-topic 0.25 (polar bears, ch7) to 0.73 (stomata, ch1); off-topic 0.81
# (photosynthesis in ch14) and 0.89 (black holes in ch1), but "algebra" in
# ch13 (0.78, graphs) still passes.
RELEVANCE_MAX_DISTANCE = 0.80
RETRIEVAL_K = 4

# --- Input limits ------------------------------------------------------------

TOPIC_MIN_LEN = 2
TOPIC_MAX_LEN = 120
DURATION_MIN = 10
DURATION_MAX = 180
CLASS_SIZE_MIN = 1
CLASS_SIZE_MAX = 200
FREE_TEXT_MAX = 300

NOT_SPECIFIED = "Not specified"

# --- Safety rules ------------------------------------------------------------

# Regexes matched case-insensitively against every free-text field after
# normalisation. Keys are the category reported in logs (never to the user).
# Keep these narrow: the source is a biology textbook, so words like "shoot",
# "sexual reproduction" or "germs are killed" are legitimate content.
UNSAFE_PATTERNS = {
    "violence_weapons": r"\b(murder(ing)?|bomb(s|ing)?|explosives?|terroris[mt]s?|guns?|firearms?|weapons?|behead(ing)?|massacres?|how to kill)\b",
    "sexual": r"\b(porn(ography)?|nudes?|naked|erotic|fetish)\b",
    "self_harm": r"\b(suicide|self[- ]?harm|cutting (myself|yourself)|kill (myself|yourself))\b",
    "hate": r"\b(racial slur|white power|ethnic cleansing|genocide is)\b",
    "drugs": r"\b(cocaine|heroin|meth(amphetamine)?|lsd|ecstasy|how to (make|cook|grow) (drugs|weed))\b",
}

INJECTION_PATTERNS = [
    r"ignore\s+(all\s+|any\s+|the\s+)?(previous|prior|above|earlier)\s+(instructions|prompts?|rules)",
    r"disregard\s+(all\s+|the\s+)?(previous|prior|above)",
    r"\bsystem\s+prompt\b",
    r"\byou\s+are\s+now\b",
    r"\bact\s+as\s+(an?\s+)?(different|new)\b",
    r"</?\s*(system|assistant|user)\s*>",
    r"^\s*(system|assistant)\s*:",
    r"```",
]
