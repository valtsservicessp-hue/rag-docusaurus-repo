"""Embed chunks and keep them in Chroma."""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import chromadb
from chromadb.config import Settings as ChromaSettings
from chromadb.errors import NotFoundError
from fastembed import TextEmbedding

from docchat.chunking import Chunk

COLLECTION = "docs"
BATCH_SIZE = 256


class StoreError(RuntimeError):
    """The index cannot be used or changed as asked."""


class Embedder(Protocol):
    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class FastEmbedder:
    """Local embeddings. The model loads on first use, not at startup."""

    def __init__(self, model_name: str, cache_dir: Path) -> None:
        self.model_name = model_name
        self.cache_dir = cache_dir
        self._model: TextEmbedding | None = None

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if self._model is None:
            self._model = TextEmbedding(self.model_name, cache_dir=str(self.cache_dir))
        return [vector.tolist() for vector in self._model.embed(list(texts))]


@dataclass(frozen=True)
class SyncResult:
    written: int
    removed: int
    unchanged: int


def _metadata(chunk: Chunk) -> dict[str, str | int]:
    return {
        "source": chunk.source,
        "title": chunk.title,
        "section": chunk.section,
        "url": chunk.url,
        "index": chunk.index,
    }


class DocStore:
    """The chunk index. Vectors from different models can never be mixed."""

    def __init__(self, path: Path, embed_model: str, reset: bool = False) -> None:
        self._client = chromadb.PersistentClient(
            path=str(path),
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        if reset:
            try:
                self._client.delete_collection(COLLECTION)
            except NotFoundError:
                pass

        # embedding_function=None stops Chroma from loading its own default model.
        self.collection = self._client.get_or_create_collection(
            COLLECTION,
            configuration={"hnsw": {"space": "cosine"}},
            metadata={"embed_model": embed_model},
            embedding_function=None,
        )

        stored_model = self.collection.metadata["embed_model"]
        if stored_model != embed_model:
            raise StoreError(
                f"The index was built with {stored_model}, but EMBED_MODEL is "
                f"{embed_model}. Vectors from different models are not comparable. "
                "Run: python ingest.py --reset"
            )

    def count(self) -> int:
        return self.collection.count()

    def sync(self, chunks: Sequence[Chunk], embedder: Embedder) -> SyncResult:
        """Make the index match chunks, embedding only what changed."""
        if not chunks:
            raise StoreError(
                "No chunks to index, so nothing was changed. "
                "Check SITE_ROOT and CONTENT_DIRS."
            )

        stored = self.collection.get(include=["metadatas"])
        old = dict(zip(stored["ids"], stored["metadatas"], strict=True))
        new_ids = {chunk.id for chunk in chunks}

        # Ids hash the text, not the URL, so a new slug keeps the same id.
        # Comparing metadata as well stops the index citing the old URL.
        to_write = [chunk for chunk in chunks if old.get(chunk.id) != _metadata(chunk)]
        to_remove = [chunk_id for chunk_id in old if chunk_id not in new_ids]

        # Write before deleting: a failed run can leave extra chunks, never fewer.
        for start in range(0, len(to_write), BATCH_SIZE):
            batch = to_write[start : start + BATCH_SIZE]
            texts = [chunk.text for chunk in batch]
            self.collection.upsert(
                ids=[chunk.id for chunk in batch],
                embeddings=embedder.embed(texts),
                documents=texts,
                metadatas=[_metadata(chunk) for chunk in batch],
            )
        if to_remove:
            self.collection.delete(ids=to_remove)

        return SyncResult(
            written=len(to_write),
            removed=len(to_remove),
            unchanged=len(chunks) - len(to_write),
        )
