"""Measure retrieval against labelled questions, and fail below a threshold.

Run from rag/:  python eval/eval_retrieval.py [--min-hit-rate 0.8] [--min-reject-rate 1]
"""

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

# Run as a script, Python puts eval/ on the path, not rag/, so add rag/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from docchat.config import load_settings
from docchat.retrieve import Hit, retrieve
from docchat.store import DocStore, FastEmbedder, StoreError

QUESTIONS = Path(__file__).resolve().parent / "questions.json"


@dataclass(frozen=True)
class Question:
    id: str
    question: str
    expected_url: str | None  # None means off-topic: it should find nothing.


@dataclass(frozen=True)
class Row:
    question: Question
    rank: int | None  # Position of the expected page among the hits, from 1.
    section_hit: bool
    hits: list[Hit]


@dataclass(frozen=True)
class Report:
    rows: list[Row]
    answerable: int
    hit_rate: float
    section_hit_rate: float
    mrr: float
    off_topic: int
    reject_rate: float


def page_of(url: str) -> str:
    return url.split("#", 1)[0]


def load_questions(path: Path) -> list[Question]:
    questions = [Question(**item) for item in json.loads(path.read_text("utf-8"))]
    ids = [question.id for question in questions]
    if len(ids) != len(set(ids)):
        raise ValueError(f"Duplicate question ids in {path.name}.")
    return questions


def stale_questions(questions: Sequence[Question], indexed_urls: set[str]) -> list[str]:
    """Expected URLs that no indexed chunk has: the doc was renamed or deleted.

    This check is exact, so a single renamed doc fails the run even when the
    hit rate would still pass the threshold.
    """
    return [
        f"{question.id}: {question.expected_url}"
        for question in questions
        if question.expected_url and question.expected_url not in indexed_urls
    ]


def evaluate(
    questions: Sequence[Question], search: Callable[[str], list[Hit]]
) -> Report:
    rows = []
    for question in questions:
        hits = search(question.question)
        rank = None
        section_hit = False
        if question.expected_url:
            expected_page = page_of(question.expected_url)
            pages = [page_of(hit.url) for hit in hits]
            if expected_page in pages:
                rank = pages.index(expected_page) + 1
            section_hit = any(hit.url == question.expected_url for hit in hits)
        rows.append(Row(question, rank, section_hit, hits))

    answerable = [row for row in rows if row.question.expected_url]
    off_topic = [row for row in rows if not row.question.expected_url]

    def share(count: int, total: int) -> float:
        return count / total if total else 1.0

    return Report(
        rows=rows,
        answerable=len(answerable),
        hit_rate=share(
            sum(row.rank is not None for row in answerable), len(answerable)
        ),
        section_hit_rate=share(
            sum(row.section_hit for row in answerable), len(answerable)
        ),
        mrr=share(sum(1 / row.rank for row in answerable if row.rank), len(answerable)),
        off_topic=len(off_topic),
        # Rejected means no hit passed MIN_SCORE, so the LLM is never called.
        reject_rate=share(sum(not row.hits for row in off_topic), len(off_topic)),
    )


def print_report(report: Report, top_k: int, min_score: float) -> None:
    print(f"{'ID':<20} {'RANK':>4} {'SECT':>4} {'TOP':>5}  TOP URL")
    for row in report.rows:
        top = row.hits[0] if row.hits else None
        if row.question.expected_url:
            rank = str(row.rank) if row.rank else "miss"
            section = "yes" if row.section_hit else "no"
        else:
            rank = "ok" if not row.hits else "FAIL"
            section = "-"
        score = f"{top.score:.3f}" if top else "-"
        url = top.url if top else "(nothing above MIN_SCORE)"
        print(f"{row.question.id:<20} {rank:>4} {section:>4} {score:>5}  {url}")

    print()
    print(f"Settings:           top_k={top_k}, min_score={min_score}")
    print(f"Page hit@{top_k}:         {report.hit_rate:.0%} of {report.answerable}")
    print(f"Section hit@{top_k}:      {report.section_hit_rate:.0%}")
    print(f"MRR:                {report.mrr:.3f}")
    print(f"Off-topic rejected: {report.reject_rate:.0%} of {report.off_topic}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate retrieval quality.")
    parser.add_argument("--questions", type=Path, default=QUESTIONS)
    parser.add_argument("--min-hit-rate", type=float, default=0.0)
    parser.add_argument("--min-reject-rate", type=float, default=0.0)
    args = parser.parse_args()

    sys.stdout.reconfigure(errors="replace")
    settings = load_settings()
    questions = load_questions(args.questions)

    try:
        store = DocStore(settings.chroma_dir, settings.embed_model)
        if store.count() == 0:
            raise StoreError("The index is empty. Run: python ingest.py")
    except StoreError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    indexed = store.collection.get(include=["metadatas"])["metadatas"]
    indexed_urls = {metadata["url"] for metadata in indexed}
    indexed_urls |= {page_of(url) for url in indexed_urls}
    stale = stale_questions(questions, indexed_urls)
    if stale:
        print("FAIL: these expected URLs are not in the index.", file=sys.stderr)
        print(
            "Was a doc renamed, moved or deleted? Update eval/questions.json.",
            file=sys.stderr,
        )
        for line in stale:
            print(f"  {line}", file=sys.stderr)
        return 1

    embedder = FastEmbedder(settings.embed_model, settings.embed_cache_dir)
    report = evaluate(
        questions,
        lambda text: retrieve(
            text, store, embedder, settings.top_k, settings.min_score
        ),
    )
    print_report(report, settings.top_k, settings.min_score)

    failed = False
    if report.hit_rate < args.min_hit_rate:
        print(f"FAIL: hit rate is below {args.min_hit_rate:.0%}.", file=sys.stderr)
        failed = True
    if report.reject_rate < args.min_reject_rate:
        print(
            f"FAIL: off-topic rejection is below {args.min_reject_rate:.0%}.",
            file=sys.stderr,
        )
        failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
