"""Find the chunks closest in meaning to a question."""

from dataclasses import dataclass

from docchat.store import DocStore, Embedder


@dataclass(frozen=True)
class Hit:
    id: str
    score: float  # Cosine similarity: 1 is the same direction, 0 is unrelated.
    title: str
    section: str
    url: str
    source: str
    text: str


def snippet(hit: Hit, length: int = 100) -> str:
    """Return the start of the body, without the title prefix, on one line."""
    body = hit.text.split("\n\n", 1)[-1]
    return " ".join(body.split())[:length]


def retrieve(
    query: str,
    store: DocStore,
    embedder: Embedder,
    top_k: int,
    min_score: float | None = None,
) -> list[Hit]:
    """Return up to top_k hits, best first, dropping any below min_score."""
    query = query.strip()
    if not query:
        return []

    result = store.collection.query(
        query_embeddings=embedder.embed([query]),
        n_results=top_k,
        include=["documents", "metadatas", "distances"],
    )

    hits = [
        Hit(
            id=chunk_id,
            score=1 - distance,
            title=metadata["title"],
            section=metadata["section"],
            url=metadata["url"],
            source=metadata["source"],
            text=text,
        )
        for chunk_id, distance, metadata, text in zip(
            result["ids"][0],
            result["distances"][0],
            result["metadatas"][0],
            result["documents"][0],
            strict=True,
        )
    ]
    if min_score is None:
        return hits
    return [hit for hit in hits if hit.score >= min_score]
