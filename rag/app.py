"""HTTP API for the chat widget.

Run from rag/:  python -m uvicorn app:app --host 127.0.0.1 --port 8001 --reload
"""

import logging
import math
import threading
import time
from collections import deque
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, StringConstraints

from docchat.chunking import chunk_documents
from docchat.config import Settings, load_settings
from docchat.generate import answer_question
from docchat.llm import LLMClient, LLMError, NoneClient, get_llm_client
from docchat.loaders import load_documents
from docchat.retrieve import retrieve
from docchat.store import DocStore, Embedder, FastEmbedder, StoreError

MAX_QUESTION_CHARS = 1000
RATE_LIMIT = 20
RATE_WINDOW = 60.0
LOCAL_CLIENTS = {"127.0.0.1", "::1"}

# Uvicorn already prints this logger, so warnings show up in Terminal 1.
logger = logging.getLogger("uvicorn.error")


class AskRequest(BaseModel):
    question: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True, min_length=1, max_length=MAX_QUESTION_CHARS
        ),
    ]


class RateLimiter:
    """Allow `limit` requests per client in any `window` seconds.

    Memory only: the count resets on restart and is per process, which suits one
    local server. Several servers would need a shared store such as Redis.
    """

    def __init__(
        self,
        limit: int = RATE_LIMIT,
        window: float = RATE_WINDOW,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limit = limit
        self.window = window
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        # Sync endpoints run in a thread pool, so requests can arrive together.
        self._lock = threading.Lock()

    def check(self, client: str) -> int:
        """Record a request. Returns 0 if allowed, else the seconds to wait."""
        now = self._clock()
        cutoff = now - self.window
        with self._lock:
            # Forget idle clients, so memory doesn't grow with every IP ever seen.
            idle = [key for key, hits in self._hits.items() if hits[-1] <= cutoff]
            for key in idle:
                del self._hits[key]

            hits = self._hits.setdefault(client, deque())
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(hits) >= self.limit:
                return math.ceil(hits[0] + self.window - now)
            hits.append(now)
            return 0


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def build_llm(settings: Settings) -> LLMClient:
    """Pick the client. A broken provider must not stop the backend starting."""
    try:
        return get_llm_client(settings)
    except LLMError as error:
        logger.warning("LLM disabled, answering retrieval-only: %s", error)
        return NoneClient()


def create_app(
    settings: Settings | None = None,
    store: DocStore | None = None,
    embedder: Embedder | None = None,
    llm: LLMClient | None = None,
    limiter: RateLimiter | None = None,
) -> FastAPI:
    """Build the API. Tests pass fakes; the real server passes nothing."""
    config = settings or load_settings()
    rate_limiter = limiter or RateLimiter()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Built here, not at import, so importing this file touches nothing.
        app.state.store = store or DocStore(config.chroma_dir, config.embed_model)
        app.state.embedder = embedder or FastEmbedder(
            config.embed_model, config.embed_cache_dir
        )
        app.state.llm = llm or build_llm(config)
        app.state.ingest_lock = threading.Lock()
        # Pay the model load once now, not on the first visitor's question.
        app.state.embedder.embed(["warm up"])
        yield

    app = FastAPI(title="DocsChat", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(config.allowed_origins),
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
        expose_headers=["Retry-After"],
    )

    def enforce_rate_limit(request: Request) -> None:
        wait = rate_limiter.check(client_ip(request))
        if wait:
            raise HTTPException(
                status_code=429,
                detail="Too many questions. Please wait and try again.",
                headers={"Retry-After": str(wait)},
            )

    def require_local(request: Request) -> None:
        # The Origin check matters: any website open in a browser on this machine
        # can send a POST to 127.0.0.1, and that request does come from localhost.
        origin = request.headers.get("origin")
        foreign_page = origin is not None and origin not in config.allowed_origins
        if client_ip(request) not in LOCAL_CLIENTS or foreign_page:
            raise HTTPException(status_code=403, detail="Ingest is local only.")

    # Plain def, not async def: FastAPI runs these in a thread pool, so one slow
    # Ollama call doesn't freeze every other request.
    @app.post("/api/ask", dependencies=[Depends(enforce_rate_limit)])
    def ask(body: AskRequest, request: Request) -> dict[str, Any]:
        state = request.app.state
        if state.store.count() == 0:
            raise HTTPException(
                status_code=503, detail="The index is empty. Run: python ingest.py"
            )

        hits = retrieve(
            body.question,
            state.store,
            state.embedder,
            config.top_k,
            config.min_score,
        )
        try:
            answer = answer_question(body.question, hits, state.llm)
        except LLMError as error:
            # Visitors still get the closest pages. The reason stays in the server
            # log, because it can hold internal URLs and provider responses.
            logger.warning("LLM failed, answering retrieval-only: %s", error)
            answer = answer_question(body.question, hits, NoneClient())
        return asdict(answer)

    @app.post("/api/ingest", dependencies=[Depends(require_local)])
    def ingest(request: Request) -> dict[str, int]:
        state = request.app.state
        if not state.ingest_lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail="Ingest is already running.")
        try:
            documents, skipped = load_documents(config)
            chunks = chunk_documents(documents, config.chunk_size, config.chunk_overlap)
            result = state.store.sync(chunks, state.embedder)
        except StoreError as error:
            raise HTTPException(status_code=500, detail=str(error)) from error
        finally:
            state.ingest_lock.release()

        return {
            "pages": len(documents),
            "skipped": len(skipped),
            "chunks": len(chunks),
            "written": result.written,
            "unchanged": result.unchanged,
            "removed": result.removed,
            "indexed": state.store.count(),
        }

    @app.get("/api/stats")
    def stats(request: Request) -> dict[str, Any]:
        state = request.app.state
        stored = state.store.collection.get(include=["metadatas"])
        return {
            "chunks": len(stored["ids"]),
            "pages": len({metadata["source"] for metadata in stored["metadatas"]}),
            "embed_model": config.embed_model,
            "top_k": config.top_k,
            "min_score": config.min_score,
            "provider": state.llm.provider,
            "model": state.llm.model,
        }

    @app.get("/healthz")
    def healthz(request: Request) -> dict[str, Any]:
        state = request.app.state
        return {
            "status": "ok",
            "chunks": state.store.count(),
            "provider": config.llm_provider,
            # Set up, not proven reachable: this never calls the provider.
            "llm_configured": state.llm.provider != "none",
        }

    return app


app = create_app()
