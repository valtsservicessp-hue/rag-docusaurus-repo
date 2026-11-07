"""Ask the docs a question from the terminal.

Run from rag/:  python ask.py "how do I start the site?" [--provider ollama]
"""

import argparse
import sys
from time import perf_counter

from docchat.config import load_settings
from docchat.generate import answer_question
from docchat.llm import LLMError, get_llm_client
from docchat.retrieve import retrieve
from docchat.store import DocStore, FastEmbedder, StoreError


def main() -> int:
    parser = argparse.ArgumentParser(description="Ask the docs a question.")
    parser.add_argument("question", help="the question to ask")
    parser.add_argument(
        "--provider",
        choices=["none", "ollama", "anthropic"],
        help="override LLM_PROVIDER for this run",
    )
    parser.add_argument("--k", type=int, help="number of hits (default: TOP_K)")
    args = parser.parse_args()

    # Keep a non-ASCII answer from crashing on a legacy Windows console.
    sys.stdout.reconfigure(errors="replace")
    settings = load_settings()

    try:
        llm = get_llm_client(settings, args.provider)
        store = DocStore(settings.chroma_dir, settings.embed_model)
        if store.count() == 0:
            raise StoreError("The index is empty. Run: python ingest.py")

        embedder = FastEmbedder(settings.embed_model, settings.embed_cache_dir)
        started = perf_counter()
        hits = retrieve(
            args.question,
            store,
            embedder,
            args.k or settings.top_k,
            settings.min_score,
        )
        retrieved = perf_counter()
        answer = answer_question(args.question, hits, llm)
        finished = perf_counter()
    except (LLMError, StoreError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    print(answer.answer)
    if answer.citations:
        print("\nSources:")
        for citation in answer.citations:
            link = settings.public_site_url.rstrip("/") + citation.url
            print(f"  [{citation.n}] {citation.title}")
            print(f"      {link}")

    print(
        f"\nProvider: {answer.provider}  Model: {answer.model or '-'}  "
        f"Mode: {answer.mode}"
    )
    print(
        f"Latency: retrieval {retrieved - started:.1f}s, "
        f"generation {finished - retrieved:.1f}s  "
        f"Tokens: {answer.usage.input_tokens} in, {answer.usage.output_tokens} out"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
