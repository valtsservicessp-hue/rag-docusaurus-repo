"""Retrieval tests. Vectors are chosen by hand, so scores can be checked exactly."""

import math

import pytest

from docchat.chunking import Chunk
from docchat.retrieve import retrieve
from docchat.store import DocStore


class MappedEmbedder:
    """Return a fixed vector for each known text."""

    def __init__(self, vectors):
        self.vectors = vectors
        self.calls = 0

    def embed(self, texts):
        self.calls += 1
        return [self.vectors[text] for text in texts]


def at_angle(degrees: float) -> list[float]:
    """A unit vector whose cosine with [1, 0] is cos(degrees)."""
    radians = math.radians(degrees)
    return [math.cos(radians), math.sin(radians)]


def make_chunk(name: str) -> Chunk:
    return Chunk(
        id=name,
        source=f"docs/{name}.mdx",
        title=name.title(),
        section="Setup",
        url=f"/docs/{name}#setup",
        index=0,
        text=name,
    )


@pytest.fixture
def embedder():
    return MappedEmbedder(
        {
            "query": [1.0, 0.0],
            "near": at_angle(10),
            "middle": at_angle(60),
            "far": at_angle(90),
        }
    )


@pytest.fixture
def store(tmp_path, embedder):
    store = DocStore(tmp_path / "chroma", "test-model")
    store.sync([make_chunk(n) for n in ("far", "near", "middle")], embedder)
    return store


def test_hits_are_ordered_best_first(store, embedder):
    hits = retrieve("query", store, embedder, top_k=3)
    assert [hit.id for hit in hits] == ["near", "middle", "far"]


def test_score_is_cosine_similarity(store, embedder):
    hits = retrieve("query", store, embedder, top_k=3)
    assert [hit.score for hit in hits] == pytest.approx(
        [math.cos(math.radians(10)), 0.5, 0.0], abs=1e-4
    )


def test_top_k_limits_the_hits(store, embedder):
    assert len(retrieve("query", store, embedder, top_k=2)) == 2


def test_top_k_above_the_index_size_returns_what_exists(store, embedder):
    assert len(retrieve("query", store, embedder, top_k=50)) == 3


def test_hits_below_min_score_are_dropped(store, embedder):
    hits = retrieve("query", store, embedder, top_k=3, min_score=0.4)
    assert [hit.id for hit in hits] == ["near", "middle"]


def test_hit_exactly_at_min_score_is_kept(store, embedder):
    score = retrieve("query", store, embedder, top_k=3)[1].score
    hits = retrieve("query", store, embedder, top_k=3, min_score=score)
    assert [hit.id for hit in hits] == ["near", "middle"]


def test_nothing_passes_a_high_threshold(store, embedder):
    assert retrieve("query", store, embedder, top_k=3, min_score=0.999) == []


def test_no_threshold_keeps_everything(store, embedder):
    assert len(retrieve("query", store, embedder, top_k=3, min_score=None)) == 3


def test_hit_carries_everything_a_citation_needs(store, embedder):
    hit = retrieve("query", store, embedder, top_k=1)[0]
    assert (hit.title, hit.section, hit.url, hit.source, hit.text) == (
        "Near",
        "Setup",
        "/docs/near#setup",
        "docs/near.mdx",
        "near",
    )


@pytest.mark.parametrize("query", ["", "   ", "\n"])
def test_blank_query_returns_nothing_without_embedding(store, embedder, query):
    embedder.calls = 0
    assert retrieve(query, store, embedder, top_k=3) == []
    assert embedder.calls == 0


def test_query_is_stripped_before_embedding(store, embedder):
    assert retrieve("  query  ", store, embedder, top_k=1)[0].id == "near"


def test_empty_index_returns_nothing(tmp_path, embedder):
    store = DocStore(tmp_path / "empty", "test-model")
    assert retrieve("query", store, embedder, top_k=3) == []
