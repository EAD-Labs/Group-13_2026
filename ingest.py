"""Ingest NCERT chapter text files into a local ChromaDB collection.

Run this after replacing data/chapter01.txt with real chapter text:

    python ingest.py

The collection is deleted and rebuilt on every run, so ingestion is idempotent
and the chunk ids stay reproducible across runs.
"""

import os
import re

import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
from langchain_text_splitters import RecursiveCharacterTextSplitter

import config

HEADING_RE = re.compile(r"^##\s+(\d+\.\d+)\s+.*$", re.MULTILINE)

BATCH_SIZE = 100


def split_sections(text):
    """Split a chapter into [(section_number, body), ...] on '## X.Y Title' lines."""
    matches = list(HEADING_RE.finditer(text))
    if not matches:
        raise ValueError(
            "No section headings found. Every section must start on its own line "
            'in the form "## 1.2 Section Title" — two hashes, a space, the '
            "section number as X.Y, a space, then the title. Paragraphs inside a "
            "section must be separated by a blank line."
        )

    sections = []
    for i, match in enumerate(matches):
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        if body:
            sections.append((match.group(1), body))
    return sections


def main():
    client = chromadb.PersistentClient(path=config.CHROMA_PATH)
    embedding_fn = SentenceTransformerEmbeddingFunction(
        model_name=config.EMBEDDING_MODEL
    )

    # Rebuild from scratch so re-running never duplicates or orphans chunks.
    try:
        client.delete_collection(config.COLLECTION_NAME)
    except Exception:
        pass

    collection = client.create_collection(
        name=config.COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
        embedding_function=embedding_fn,
    )

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=config.CHUNK_SIZE,
        chunk_overlap=config.CHUNK_OVERLAP,
        separators=["\n\n", "\n", ". ", " ", ""],
    )

    ids, documents, metadatas = [], [], []

    for filename, info in config.CHAPTERS.items():
        path = os.path.join("data", filename)
        with open(path, encoding="utf-8") as f:
            text = f.read()

        sections = split_sections(text)
        print(f"{filename}: {len(sections)} sections")

        for section_number, body in sections:
            chunks = splitter.split_text(body)
            kept = 0
            for chunk in chunks:
                chunk = chunk.strip()
                if len(chunk) < 100:
                    continue
                chunk_id = (
                    f"g{config.GRADE}"
                    f"_ch{info['num']:02d}"
                    f"_s{section_number}"
                    f"_{kept:03d}"
                )
                ids.append(chunk_id)
                documents.append(chunk)
                metadatas.append(
                    {
                        "grade": config.GRADE,
                        "chapter_num": info["num"],
                        "chapter": info["name"],
                        "section": section_number,
                        "source": config.SOURCE,
                    }
                )
                kept += 1
            print(f"  section {section_number}: {kept} chunks")

    for start in range(0, len(ids), BATCH_SIZE):
        stop = start + BATCH_SIZE
        collection.add(
            ids=ids[start:stop],
            documents=documents[start:stop],
            metadatas=metadatas[start:stop],
        )

    print(f"collection.count() = {collection.count()}")


if __name__ == "__main__":
    main()
