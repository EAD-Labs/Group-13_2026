"""Central configuration for the AgentTPACK RAG proof-of-concept."""

CHROMA_PATH = "./chroma_store"
COLLECTION_NAME = "ncert_g7_biology"
EMBEDDING_MODEL = "all-MiniLM-L6-v2"

DB_PATH = "./app.db"

GRADE = 7
SOURCE = "NCERT Science Class 7"

CHUNK_SIZE = 1000
CHUNK_OVERLAP = 150

# IMPORTANT: verify these chapter numbers against YOUR OWN copy of the PDF.
# NCERT renumbered (and dropped) chapters in the 2023 rationalisation, so the
# chapter number printed in an older book will not match a current print run.
# The number recorded here becomes part of every chunk id and of the
# `chapter_num` metadata filter, so a wrong number silently breaks retrieval.
CHAPTERS = {
    "chapter01.txt": {"num": 1, "name": "Nutrition in Plants"},
}
