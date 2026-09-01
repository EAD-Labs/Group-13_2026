"""Semantic retrieval over the ingested NCERT chunks."""

import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

import config

_client = chromadb.PersistentClient(path=config.CHROMA_PATH)
_embedding_fn = SentenceTransformerEmbeddingFunction(model_name=config.EMBEDDING_MODEL)
_collection = _client.get_collection(
    name=config.COLLECTION_NAME,
    embedding_function=_embedding_fn,
)


def retrieve(query, chapter_num=None, k=4):
    """Semantic search. Returns [{id, text, metadata, distance}, ...]."""
    kwargs = {"query_texts": [query], "n_results": k}
    if chapter_num is not None:
        kwargs["where"] = {"chapter_num": chapter_num}

    result = _collection.query(**kwargs)

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
    return chunks


def get_chunk(chunk_id):
    """Keyed lookup of a single chunk by id. No similarity search. None if absent."""
    result = _collection.get(ids=[chunk_id])
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
