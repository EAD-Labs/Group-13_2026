# Image for hosting on Render (Docker runtime); also runs anywhere with
# `docker run -p 8000:8000 -e GEMINI_API_KEY=... <image>`.
FROM python:3.14-slim

RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PYTHONUNBUFFERED=1
WORKDIR /home/user/app

COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

COPY --chown=user . .

# chroma_store/ is gitignored, so build the index (and cache the embedding
# model) inside the image.
RUN python ingest.py

# Render sets PORT; default to 8000 elsewhere.
CMD uvicorn api:app --host 0.0.0.0 --port ${PORT:-8000}
