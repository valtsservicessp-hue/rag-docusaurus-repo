# DocsChat backend with the docs, the embedding model and the index baked in,
# so the image version is also the docs version.
#
# Build from the repo root (the context must include docs/ as well as rag/):
#   docker build -f rag/Dockerfile -t docchat .
# Run:
#   docker run -p 8001:8001 docchat
#   docker run -p 8001:8001 -e LLM_PROVIDER=anthropic -e ANTHROPIC_API_KEY=... docchat

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    EMBED_MODEL=sentence-transformers/paraphrase-multilingual-mpnet-base-v2 \
    EMBED_CACHE_DIR=data/models \
    CHROMA_DIR=data/chroma \
    SITE_ROOT=.. \
    CONTENT_DIRS=docs \
    LLM_PROVIDER=none

# Never run as root: if the app is ever exploited, the attacker isn't root.
RUN useradd --create-home --uid 10001 app \
    && mkdir -p /app/rag \
    && chown app:app /app /app/rag
WORKDIR /app/rag
USER app
ENV PATH=/home/app/.local/bin:$PATH

# Layers are ordered from least to most often changed, so a code change
# reuses the cached dependencies and the 1 GB model instead of downloading again.
COPY --chown=app:app rag/requirements.txt .
RUN pip install --user -r requirements.txt

RUN python -c "import os; from fastembed import TextEmbedding; \
TextEmbedding(os.environ['EMBED_MODEL'], cache_dir=os.environ['EMBED_CACHE_DIR'])"

COPY --chown=app:app docs/ /app/docs/
COPY --chown=app:app rag/ .

# Bake the index. Chroma writes to data/ at runtime too, so app must own it.
RUN python ingest.py --reset

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/healthz', timeout=4)"

# 0.0.0.0, not 127.0.0.1: inside a container, 127.0.0.1 is unreachable from outside.
CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8001"]
