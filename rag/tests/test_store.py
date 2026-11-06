"""Index tests. A fake embedder keeps the real model out of CI."""

import hashlib
from dataclasses import replace

import pytest

import docchat.store
from docchat.chunking import Chunk
from docchat.store import DocStore, StoreError

MODEL = "test-model"


class FakeEmbedder:
    """Return a repeatable vector per text, and record every call."""

    def __init__(self):
        self.embedded: list[str] = []

    def embed(self, texts):
        self.embedded.extend(texts)
        vectors = []
        for text in texts:
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            vectors.append([byte + 1.0 for byte in digest[:4]])
        return vectors


class BrokenEmbedder:
    def embed(self, texts):
        raise OSError("model download failed")


def make_chunk(n: int, source: str = "docs/a.mdx", text: str | None = None) -> Chunk:
    return Chunk(
        id=f"{source}-{n}-{text}",
        source=source,
        title="Page",
        section="" if n == 0 else f"Section {n}",
        url=f"/docs/a#section-{n}",
        index=n,
        text=text or f"Chunk {n}",
    )


@pytest.fixture
def store(tmp_path):
    return DocStore(tmp_path / "chroma", MODEL)


def stored_ids(store: DocStore) -> set[str]:
    return set(store.collection.get()["ids"])


def test_first_sync_writes_every_chunk(store):
    embedder = FakeEmbedder()
    result = store.sync([make_chunk(0), make_chunk(1)], embedder)
    assert (result.written, result.removed, result.unchanged) == (2, 0, 0)
    assert store.count() == 2
    assert embedder.embedded == ["Chunk 0", "Chunk 1"]


def test_second_sync_changes_nothing_and_embeds_nothing(store):
    chunks = [make_chunk(0), make_chunk(1)]
    store.sync(chunks, FakeEmbedder())
    embedder = FakeEmbedder()
    result = store.sync(chunks, embedder)
    assert (result.written, result.removed, result.unchanged) == (0, 0, 2)
    assert store.count() == 2
    assert embedder.embedded == []


def test_changed_chunk_replaces_only_itself(store):
    store.sync([make_chunk(0), make_chunk(1)], FakeEmbedder())
    edited = make_chunk(1, text="New text")
    embedder = FakeEmbedder()
    result = store.sync([make_chunk(0), edited], embedder)
    assert (result.written, result.removed, result.unchanged) == (1, 1, 1)
    assert embedder.embedded == ["New text"]
    assert stored_ids(store) == {make_chunk(0).id, edited.id}


def test_chunks_of_a_deleted_page_are_removed(store):
    kept = make_chunk(0, source="docs/keep.mdx")
    gone = make_chunk(0, source="docs/gone.mdx")
    store.sync([kept, gone], FakeEmbedder())
    result = store.sync([kept], FakeEmbedder())
    assert result.removed == 1
    assert stored_ids(store) == {kept.id}


def test_new_url_with_the_same_text_updates_the_citation(store):
    chunk = make_chunk(0)
    store.sync([chunk], FakeEmbedder())
    moved = replace(chunk, url="/docs/moved")
    result = store.sync([moved], FakeEmbedder())
    assert result.written == 1
    assert store.count() == 1
    assert store.collection.get()["metadatas"][0]["url"] == "/docs/moved"


def test_metadata_and_text_are_stored(store):
    chunk = make_chunk(1)
    store.sync([chunk], FakeEmbedder())
    stored = store.collection.get(include=["metadatas", "documents"])
    assert stored["documents"] == ["Chunk 1"]
    assert stored["metadatas"] == [
        {
            "source": "docs/a.mdx",
            "title": "Page",
            "section": "Section 1",
            "url": "/docs/a#section-1",
            "index": 1,
        }
    ]


def test_index_survives_reopening(tmp_path):
    DocStore(tmp_path / "chroma", MODEL).sync([make_chunk(0)], FakeEmbedder())
    assert DocStore(tmp_path / "chroma", MODEL).count() == 1


def test_collection_uses_cosine_distance(store):
    assert store.collection.configuration["hnsw"]["space"] == "cosine"


def test_cosine_ignores_vector_length(store):
    store.collection.upsert(ids=["a"], embeddings=[[1.0, 2.0, 3.0]], documents=["x"])
    result = store.collection.query(query_embeddings=[[10.0, 20.0, 30.0]], n_results=1)
    assert result["distances"][0][0] == pytest.approx(0.0, abs=1e-5)


def test_refuses_to_mix_embedding_models(tmp_path):
    DocStore(tmp_path / "chroma", MODEL).sync([make_chunk(0)], FakeEmbedder())
    with pytest.raises(StoreError, match="--reset"):
        DocStore(tmp_path / "chroma", "other-model")


def test_reset_allows_a_new_model(tmp_path):
    DocStore(tmp_path / "chroma", MODEL).sync([make_chunk(0)], FakeEmbedder())
    store = DocStore(tmp_path / "chroma", "other-model", reset=True)
    assert store.count() == 0
    assert store.collection.metadata["embed_model"] == "other-model"


def test_reset_works_on_an_empty_folder(tmp_path):
    assert DocStore(tmp_path / "chroma", MODEL, reset=True).count() == 0


def test_empty_input_never_wipes_the_index(store):
    store.sync([make_chunk(0)], FakeEmbedder())
    with pytest.raises(StoreError, match="SITE_ROOT"):
        store.sync([], FakeEmbedder())
    assert store.count() == 1


def test_failed_embedding_keeps_the_old_chunks(store):
    original = make_chunk(0)
    store.sync([original], FakeEmbedder())
    with pytest.raises(OSError, match="model download failed"):
        store.sync([make_chunk(0, text="New text")], BrokenEmbedder())
    assert stored_ids(store) == {original.id}


def test_large_syncs_are_batched(store, monkeypatch):
    monkeypatch.setattr(docchat.store, "BATCH_SIZE", 2)
    chunks = [make_chunk(n) for n in range(5)]
    result = store.sync(chunks, FakeEmbedder())
    assert result.written == 5
    assert store.count() == 5
