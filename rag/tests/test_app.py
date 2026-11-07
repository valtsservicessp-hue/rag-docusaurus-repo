"""API tests. Real Chroma in a temp folder; fake embedder and LLM, so no network."""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app import MAX_QUESTION_CHARS, RateLimiter, create_app
from docchat.chunking import Chunk
from docchat.config import load_settings
from docchat.generate import MODE_GENERATED, MODE_NOT_FOUND, MODE_RETRIEVAL_ONLY
from docchat.llm import LLMError, LLMResult
from docchat.store import DocStore

LOCAL = ("127.0.0.1", 50000)
REMOTE = ("203.0.113.7", 50000)
ALLOWED = "http://localhost:3000"


class FakeEmbedder:
    """Docs and questions match, unless the text says it is off-topic."""

    def __init__(self):
        self.calls = 0

    def embed(self, texts):
        self.calls += 1
        return [[0.0, 1.0] if "off-topic" in text else [1.0, 0.0] for text in texts]


class FakeLLM:
    provider = "fake"
    model = "fake-model"

    def __init__(self):
        self.calls = 0

    def complete(self, system, user):
        self.calls += 1
        return LLMResult("Run npm start [1].", 100, 10, self.provider, self.model)


class BrokenLLM(FakeLLM):
    def complete(self, system, user):
        raise LLMError("Cannot reach Ollama at http://secret-host:11434.")


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def make_chunk(n: int, source: str = "docs/intro.mdx") -> Chunk:
    return Chunk(
        id=f"chunk-{n}",
        source=source,
        title="Intro",
        section="Start",
        url="/docs/intro#start",
        index=n,
        text=f"Intro > Start\n\nBody {n}.",
    )


@pytest.fixture
def settings(tmp_path):
    """Pin every value a test relies on, so a developer's rag/.env can't change it."""
    return replace(
        load_settings(),
        site_root=tmp_path / "site",
        content_dirs=("docs",),
        chunk_size=800,
        chunk_overlap=150,
        top_k=5,
        min_score=0.38,
        llm_provider="ollama",
        allowed_origins=(ALLOWED,),
        anthropic_api_key=None,
    )


@pytest.fixture
def embedder():
    return FakeEmbedder()


@pytest.fixture
def store(tmp_path, embedder):
    store = DocStore(tmp_path / "chroma", "test-model")
    store.sync([make_chunk(0), make_chunk(1, "docs/other.mdx")], embedder)
    return store


@pytest.fixture
def llm():
    return FakeLLM()


@pytest.fixture
def make_client(settings, store, embedder, llm):
    """Start the app, lifespan included, with fakes that a test can override."""
    clients = []

    def start(client=LOCAL, **overrides):
        options = {
            "settings": settings,
            "store": store,
            "embedder": embedder,
            "llm": llm,
            **overrides,
        }
        test_client = TestClient(create_app(**options), client=client)
        test_client.__enter__()
        clients.append(test_client)
        return test_client

    yield start
    for test_client in clients:
        test_client.__exit__(None, None, None)


def ask(client, question="How do I start?", **kwargs):
    return client.post("/api/ask", json={"question": question}, **kwargs)


# --- Startup ---


def test_embedding_model_loads_at_startup(make_client, embedder):
    before = embedder.calls
    make_client()
    assert embedder.calls == before + 1


def test_missing_api_key_does_not_stop_startup(make_client, settings):
    settings = replace(settings, llm_provider="anthropic", anthropic_api_key=None)
    body = make_client(settings=settings, llm=None).get("/healthz").json()
    assert body["status"] == "ok"
    assert body["provider"] == "anthropic"
    assert body["llm_configured"] is False


def test_unknown_provider_does_not_stop_startup(make_client, settings):
    client = make_client(settings=replace(settings, llm_provider="typo"), llm=None)
    assert client.get("/healthz").json()["llm_configured"] is False


# --- /healthz and /api/stats ---


def test_healthz_reports_chunks_and_provider(make_client):
    assert make_client().get("/healthz").json() == {
        "status": "ok",
        "chunks": 2,
        "provider": "ollama",
        "llm_configured": True,
    }


def test_healthz_never_calls_the_llm(make_client, llm):
    make_client().get("/healthz")
    assert llm.calls == 0


def test_none_provider_is_healthy_but_not_configured(make_client, settings):
    client = make_client(settings=replace(settings, llm_provider="none"), llm=None)
    body = client.get("/healthz").json()
    assert (body["provider"], body["llm_configured"]) == ("none", False)


def test_stats_counts_chunks_and_pages(make_client):
    body = make_client().get("/api/stats").json()
    assert (body["chunks"], body["pages"]) == (2, 2)
    assert (body["provider"], body["model"]) == ("fake", "fake-model")


def test_stats_never_includes_the_api_key(make_client, settings):
    settings = replace(settings, anthropic_api_key="sk-secret")
    assert "sk-secret" not in make_client(settings=settings).get("/api/stats").text


# --- /api/ask ---


def test_ask_returns_the_full_answer(make_client):
    body = ask(make_client()).json()
    assert body["answer"] == "Run npm start [1]."
    assert body["mode"] == MODE_GENERATED
    assert body["citations"] == [
        {"n": 1, "title": "Intro > Start", "url": "/docs/intro#start"}
    ]
    assert len(body["hits"]) == 2
    assert body["usage"] == {"input_tokens": 100, "output_tokens": 10}
    assert (body["provider"], body["model"]) == ("fake", "fake-model")


def test_off_topic_question_skips_the_llm(make_client, llm):
    body = ask(make_client(), "an off-topic question").json()
    assert body["mode"] == MODE_NOT_FOUND
    assert llm.calls == 0


def test_none_provider_gives_retrieval_only(make_client, settings):
    client = make_client(settings=replace(settings, llm_provider="none"), llm=None)
    body = ask(client).json()
    assert body["mode"] == MODE_RETRIEVAL_ONLY
    assert body["provider"] == "none"


def test_missing_api_key_gives_retrieval_only(make_client, settings):
    settings = replace(settings, llm_provider="anthropic", anthropic_api_key=None)
    assert ask(make_client(settings=settings, llm=None)).json()["mode"] == (
        MODE_RETRIEVAL_ONLY
    )


def test_llm_failure_gives_the_closest_pages(make_client):
    response = ask(make_client(llm=BrokenLLM()))
    assert response.status_code == 200
    assert response.json()["mode"] == MODE_RETRIEVAL_ONLY
    assert response.json()["provider"] == "none"


def test_llm_failure_details_stay_out_of_the_response(make_client):
    assert "secret-host" not in ask(make_client(llm=BrokenLLM())).text


def test_empty_index_is_a_clear_error(make_client, tmp_path):
    client = make_client(store=DocStore(tmp_path / "empty", "test-model"))
    response = ask(client)
    assert response.status_code == 503
    assert "python ingest.py" in response.json()["detail"]


def test_question_at_the_limit_is_accepted(make_client):
    assert ask(make_client(), "x" * MAX_QUESTION_CHARS).status_code == 200


@pytest.mark.parametrize("question", ["", "   ", "x" * (MAX_QUESTION_CHARS + 1)])
def test_bad_question_is_rejected_without_calling_the_llm(make_client, llm, question):
    assert ask(make_client(), question).status_code == 422
    assert llm.calls == 0


def test_missing_question_is_rejected(make_client):
    assert make_client().post("/api/ask", json={}).status_code == 422


# --- CORS ---


def test_allowed_origin_gets_cors_headers(make_client):
    response = ask(make_client(), headers={"Origin": ALLOWED})
    assert response.headers["access-control-allow-origin"] == ALLOWED


def test_other_origin_gets_no_cors_headers(make_client):
    response = ask(make_client(), headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in response.headers


@pytest.mark.parametrize(
    ("origin", "status"), [(ALLOWED, 200), ("http://evil.example", 400)]
)
def test_preflight_only_passes_for_allowed_origins(make_client, origin, status):
    response = make_client().options(
        "/api/ask",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert response.status_code == status


# --- Rate limit ---


def test_rate_limit_blocks_the_extra_request(make_client):
    client = make_client(limiter=RateLimiter(limit=2))
    assert [ask(client).status_code for _ in range(3)] == [200, 200, 429]


def test_blocked_request_says_when_to_retry_and_skips_the_llm(make_client, llm):
    client = make_client(limiter=RateLimiter(limit=1, window=60))
    ask(client)
    response = ask(client)
    assert 1 <= int(response.headers["retry-after"]) <= 60
    assert llm.calls == 1


def test_clients_have_separate_limits(make_client):
    limiter = RateLimiter(limit=1)
    assert ask(make_client(limiter=limiter)).status_code == 200
    assert ask(make_client(REMOTE, limiter=limiter)).status_code == 200


def test_healthz_is_not_rate_limited(make_client):
    client = make_client(limiter=RateLimiter(limit=1))
    assert [client.get("/healthz").status_code for _ in range(3)] == [200, 200, 200]


def test_limiter_allows_requests_after_the_window():
    clock = FakeClock()
    limiter = RateLimiter(limit=1, window=60, clock=clock)
    assert limiter.check("a") == 0
    clock.now += 59
    assert limiter.check("a") == 1
    clock.now += 1
    assert limiter.check("a") == 0


def test_limiter_window_slides_instead_of_resetting():
    clock = FakeClock()
    limiter = RateLimiter(limit=2, window=60, clock=clock)
    limiter.check("a")
    clock.now += 30
    limiter.check("a")
    clock.now += 31
    assert limiter.check("a") == 0
    assert limiter.check("a") == 29


def test_limiter_forgets_idle_clients():
    clock = FakeClock()
    limiter = RateLimiter(limit=1, window=60, clock=clock)
    limiter.check("old")
    clock.now += 61
    limiter.check("new")
    assert list(limiter._hits) == ["new"]


# --- /api/ingest ---


@pytest.fixture
def site(settings):
    docs = settings.site_root / "docs"
    docs.mkdir(parents=True)
    (docs / "guide.md").write_text(
        "---\ntitle: Guide\n---\n\n# Guide\n\n## Install\n\nRun the installer.\n",
        encoding="utf-8",
    )
    return docs


def test_ingest_indexes_the_site_and_removes_old_chunks(make_client, site):
    body = make_client().post("/api/ingest").json()
    assert body["pages"] == 1
    assert body["indexed"] == body["chunks"] > 0
    assert body["removed"] == 2


def test_second_ingest_changes_nothing(make_client, site):
    client = make_client()
    client.post("/api/ingest")
    body = client.post("/api/ingest").json()
    assert (body["written"], body["removed"]) == (0, 0)


def test_ingest_is_refused_from_another_machine(make_client, site, store):
    response = make_client(REMOTE).post("/api/ingest")
    assert response.status_code == 403
    assert store.count() == 2


def test_ingest_is_refused_from_a_foreign_page_on_this_machine(make_client, site):
    headers = {"Origin": "http://evil.example"}
    assert make_client().post("/api/ingest", headers=headers).status_code == 403


def test_second_ingest_at_once_is_refused(make_client, site):
    client = make_client()
    client.app.state.ingest_lock.acquire()
    assert client.post("/api/ingest").status_code == 409


def test_ingest_with_no_docs_keeps_the_index(make_client, store):
    response = make_client().post("/api/ingest")
    assert response.status_code == 500
    assert "SITE_ROOT" in response.json()["detail"]
    assert store.count() == 2


def test_failed_ingest_can_be_retried(make_client, settings):
    client = make_client()
    client.post("/api/ingest")
    docs = settings.site_root / "docs"
    docs.mkdir(parents=True)
    (docs / "guide.md").write_text("# Guide\n\nText.\n", encoding="utf-8")
    assert client.post("/api/ingest").status_code == 200
