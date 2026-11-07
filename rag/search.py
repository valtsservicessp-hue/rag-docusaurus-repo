"""Search the index without calling an LLM.

Run from rag/:  python search.py "how do I start the site?" [--k 5] [--all]
"""

import argparse
import sys

from docchat.config import load_settings
from docchat.retrieve import retrieve, snippet
from docchat.store import DocStore, FastEmbedder, StoreError


def main() -> int:
    parser = argparse.ArgumentParser(description="Search the docs index.")
    parser.add_argument("query", help="the question to search for")
    parser.add_argument("--k", type=int, help="number of results (default: TOP_K)")
    parser.add_argument(
        "--all", action="store_true", help="also show hits below MIN_SCORE"
    )
    args = parser.parse_args()

    # Keep a non-ASCII result from crashing on a legacy Windows console.
    sys.stdout.reconfigure(errors="replace")
    settings = load_settings()
    try:
        store = DocStore(settings.chroma_dir, settings.embed_model)
    except StoreError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    if store.count() == 0:
        print("Error: the index is empty. Run: python ingest.py", file=sys.stderr)
        return 1

    embedder = FastEmbedder(settings.embed_model, settings.embed_cache_dir)
    hits = retrieve(args.query, store, embedder, args.k or settings.top_k)

    shown = 0
    for hit in hits:
        below = hit.score < settings.min_score
        if below and not args.all:
            continue
        shown += 1
        marker = "  (below MIN_SCORE)" if below else ""
        where = f"{hit.title} > {hit.section}" if hit.section else hit.title
        print(f"{shown}. {hit.score:.3f}  {hit.url}{marker}")
        print(f"   {where}")
        print(f"   {snippet(hit)}")

    if shown == 0:
        print(f"No hits at or above MIN_SCORE ({settings.min_score}).")
        print("Add --all to see them anyway.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
