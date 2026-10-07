"""Semantic retrieval over the ingested NCERT chunks."""

import logging
import re

import config
from errors import InputValidationError, RetrievalUnavailableError
from logging_setup import log_event

CHUNK_ID_RE = re.compile(r"^g\d+_ch\d{2}_s\d+\.\d+_\d{3}$")

_collection = None


def _get_collection():
    """Open the collection on first use, so importing this module never fails
    just because `python ingest.py` has not been run yet."""
    global _collection
    if _collection is not None:
        return _collection
    try:
        import chromadb
        from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

        client = chromadb.PersistentClient(path=config.CHROMA_PATH)
        _collection = client.get_collection(
            name=config.COLLECTION_NAME,
            embedding_function=SentenceTransformerEmbeddingFunction(
                model_name=config.EMBEDDING_MODEL
            ),
        )
    except Exception as e:
        log_event(
            "retrieval.error",
            "retriever",
            level=logging.ERROR,
            error_type=type(e).__name__,
            error=str(e)[:500],
        )
        raise RetrievalUnavailableError(
            "The textbook index is not available on the server. Run `python ingest.py` and restart."
        ) from e
    return _collection


def retrieve(query, chapter_num=None, k=config.RETRIEVAL_K):
    """Semantic search. Returns [{id, text, metadata, distance}, ...]."""
    kwargs = {"query_texts": [query], "n_results": k}
    if chapter_num is not None:
        kwargs["where"] = {"chapter_num": chapter_num}

    try:
        result = _get_collection().query(**kwargs)
    except RetrievalUnavailableError:
        raise
    except Exception as e:
        log_event("retrieval.error", "retriever", level=logging.ERROR, error=str(e)[:500])
        raise RetrievalUnavailableError("Searching the textbook failed. Please try again.") from e

    chunks = []
    for i, chunk_id in enumerate(result["ids"][0]):
        chunks.append(
            {
                "id": chunk_id,
                "text": result["documents"][0][i],
                "metadata": result["metadatas"][0][i],
                "distance": result["distances"][0][i],
            }
        )

    log_event(
        "retrieval.done",
        "retriever",
        query=query,
        chapter_num=chapter_num,
        ids=[c["id"] for c in chunks],
        distances=[round(c["distance"], 4) for c in chunks],
        detail={
            "passages": [
                {"id": c["id"], "distance": round(c["distance"], 4), "text": c["text"]}
                for c in chunks
            ]
        },
    )
    return chunks


def get_chunk(chunk_id):
    """Keyed lookup of a single chunk by id. No similarity search. None if absent."""
    if not CHUNK_ID_RE.match(chunk_id):
        raise InputValidationError(
            "That citation id is not in the expected format.",
            details=[{"field": "chunk_id", "message": "Expected an id like g7_ch01_s1.2_000."}],
        )
    result = _get_collection().get(ids=[chunk_id])
    if not result["ids"]:
        return None
    return {
        "id": result["ids"][0],
        "text": result["documents"][0],
        "metadata": result["metadatas"][0],
    }


def format_chunks(chunks):
    """Render chunks for the prompt, with the id visible so the model can cite it."""
    parts = []
    for chunk in chunks:
        meta = chunk["metadata"]
        header = (
            f"[{chunk['id']}] "
            f"(Chapter {meta['chapter_num']}, Section {meta['section']})"
        )
        parts.append(f"{header}\n{chunk['text']}")
    return "\n\n".join(parts)
