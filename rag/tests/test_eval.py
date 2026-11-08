"""Eval and link-check tests. Fake search results and a tiny fake build."""

import json

import pytest

from docchat.retrieve import Hit
from eval.check_citation_urls import check_urls, page_file
from eval.eval_retrieval import (
    QUESTIONS,
    Question,
    evaluate,
    load_questions,
    stale_questions,
)


def hit(url: str) -> Hit:
    return Hit(id=url, score=0.5, title="T", section="", url=url, source="s", text="t")


def fake_search(results: dict[str, list[str]]):
    return lambda text: [hit(url) for url in results.get(text, [])]


# --- Metrics ---


def test_rank_and_mrr_use_the_page_not_the_section():
    questions = [
        Question("a", "qa", "/docs/a#one"),
        Question("b", "qb", "/docs/b"),
    ]
    search = fake_search({"qa": ["/docs/x", "/docs/a#two"], "qb": ["/docs/b#z"]})
    report = evaluate(questions, search)
    assert [row.rank for row in report.rows] == [2, 1]
    assert report.hit_rate == 1.0
    assert report.mrr == pytest.approx((1 / 2 + 1) / 2)


def test_section_hit_needs_the_exact_anchor():
    questions = [Question("a", "qa", "/docs/a#one")]
    report = evaluate(questions, fake_search({"qa": ["/docs/a#two"]}))
    assert report.hit_rate == 1.0
    assert report.section_hit_rate == 0.0


def test_a_miss_counts_zero():
    questions = [Question("a", "qa", "/docs/a"), Question("b", "qb", "/docs/b")]
    report = evaluate(questions, fake_search({"qa": ["/docs/a"], "qb": ["/docs/x"]}))
    assert report.hit_rate == 0.5
    assert report.mrr == 0.5


def test_off_topic_is_rejected_only_when_nothing_passes_min_score():
    questions = [Question("x", "qx", None), Question("y", "qy", None)]
    report = evaluate(questions, fake_search({"qy": ["/docs/a"]}))
    assert report.off_topic == 2
    assert report.reject_rate == 0.5
    assert report.answerable == 0


def test_off_topic_questions_do_not_change_the_hit_rate():
    questions = [Question("a", "qa", "/docs/a"), Question("x", "qx", None)]
    report = evaluate(questions, fake_search({"qa": ["/docs/a"], "qx": ["/docs/a"]}))
    assert report.hit_rate == 1.0


# --- Questions file ---


def test_renamed_doc_is_reported_as_stale():
    questions = [Question("a", "qa", "/docs/old#setup"), Question("x", "qx", None)]
    assert stale_questions(questions, {"/docs/new#setup"}) == ["a: /docs/old#setup"]


def test_duplicate_ids_are_refused(tmp_path):
    path = tmp_path / "q.json"
    item = {"id": "a", "question": "q", "expected_url": None}
    path.write_text(json.dumps([item, item]), encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate"):
        load_questions(path)


def test_real_questions_file_follows_the_plan():
    questions = load_questions(QUESTIONS)
    assert 10 <= len(questions) <= 15
    assert sum(question.expected_url is None for question in questions) == 3
    for question in questions:
        if question.expected_url:
            assert question.expected_url.startswith("/docs/")


# --- Citation link check ---


@pytest.fixture
def build(tmp_path):
    def page(relative: str, html: str) -> None:
        file = tmp_path / relative
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(html, encoding="utf-8")

    page("index.html", "<h1>Home</h1>")
    # Unquoted ids, as the real minified build writes them.
    page("docs/intro/index.html", "<h2 class=anchor id=start-your-site>Start</h2>")
    page("docs/flat.html", '<h2 id="setup">Setup</h2>')
    return tmp_path


def test_existing_page_and_anchor_pass(build):
    assert check_urls({"/docs/intro", "/docs/intro#start-your-site"}, build) == []


def test_missing_page_is_a_404(build):
    assert check_urls({"/docs/renamed"}, build) == [
        "/docs/renamed: page not in the build (404)"
    ]


def test_missing_anchor_fails(build):
    problems = check_urls({"/docs/intro#old-heading"}, build)
    assert problems == [
        "/docs/intro#old-heading: no heading with id 'old-heading' on the page"
    ]


def test_flat_html_file_and_home_page_are_found(build):
    assert check_urls({"/docs/flat#setup", "/"}, build) == []


def test_base_url_is_removed_before_looking_up_the_file(build):
    assert page_file(build, "/site/docs/intro", "/site/") == (
        build / "docs/intro/index.html"
    )
    assert check_urls({"/site/docs/intro#start-your-site"}, build, "/site/") == []
