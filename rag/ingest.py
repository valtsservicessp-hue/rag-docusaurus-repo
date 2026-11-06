"""Build or update the search index from the site's docs.

Run from rag/:  python ingest.py [--reset]
"""

import argparse
import sys
from pathlib import Path
from time import perf_counter

from docchat.chunking import chunk_documents
from docchat.config import load_settings
from docchat.loaders import load_documents
from docchat.store import DocStore, FastEmbedder, StoreError


def folder_size_mb(path: Path) -> float:
    """Measure real files only, because cache folders can hold symlinks."""
    files = [p for p in path.rglob("*") if p.is_file() and not p.is_symlink()]
    return sum(file.stat().st_size for file in files) / 1024 / 1024


def main() -> int:
    parser = argparse.ArgumentParser(description="Index the site's docs.")
    parser.add_argument(
        "--reset", action="store_true", help="delete the index and rebuild it"
    )
    args = parser.parse_args()

    settings = load_settings()
    documents, skipped = load_documents(settings)
    chunks = chunk_documents(documents, settings.chunk_size, settings.chunk_overlap)
    embedder = FastEmbedder(settings.embed_model, settings.embed_cache_dir)

    started = perf_counter()
    try:
        store = DocStore(settings.chroma_dir, settings.embed_model, reset=args.reset)
        result = store.sync(chunks, embedder)
    except StoreError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    elapsed = perf_counter() - started

    print(f"Pages:     {len(documents)} ({len(skipped)} skipped)")
    print(f"Chunks:    {len(chunks)}")
    print(f"Written:   {result.written}")
    print(f"Unchanged: {result.unchanged}")
    print(f"Removed:   {result.removed}")
    print(f"Indexed:   {store.count()}")
    print(f"Time:      {elapsed:.1f}s")
    print(f"Model:     {settings.embed_model}")
    print(f"Model dir: {settings.embed_cache_dir}")
    if settings.embed_cache_dir.is_dir():
        print(f"Model MB:  {folder_size_mb(settings.embed_cache_dir):.0f}")
    print(f"Index dir: {settings.chroma_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
