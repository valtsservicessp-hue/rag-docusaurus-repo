"""Chunking tests. Snippets marked 'real' are copied from docs/."""

from itertools import pairwise

import pytest

from docchat.chunking import (
    chunk_document,
    chunk_id,
    overlap_tail,
    split_blocks,
    split_body,
    split_code,
    split_text,
)
from docchat.loaders import Document, Section

SIZE = 300
OVERLAP = 60

# real: docs/intro.mdx
START_YOUR_SITE = """Run the development server:

Code (bash):
```bash
cd my-website
npm run start
```

The `cd` command changes the directory you're working with."""


def make_doc(*sections: Section, title: str = "Guide") -> Document:
    return Document(
        source="docs/guide.mdx",
        title=title,
        url="/docs/guide",
        sections=sections,
    )


def sentences(count: int) -> str:
    return " ".join(f"Sentence number {n} says something useful." for n in range(count))


def body_of(chunk) -> str:
    return chunk.text.split("\n\n", 1)[1]


def fence_lines(text: str) -> int:
    return sum(line.strip().startswith("```") for line in text.split("\n"))


def test_short_section_is_one_chunk_with_prefix():
    chunks = chunk_document(
        make_doc(Section("Setup", "setup", "Short text.")), SIZE, OVERLAP
    )
    assert len(chunks) == 1
    assert chunks[0].text == "Guide > Setup\n\nShort text."


def test_page_intro_prefix_is_only_the_title():
    chunks = chunk_document(make_doc(Section("", "", "Intro.")), SIZE, OVERLAP)
    assert chunks[0].text == "Guide\n\nIntro."


def test_chunk_url_includes_the_section_anchor():
    doc = make_doc(Section("", "", "Intro."), Section("Setup", "setup", "Body."))
    chunks = chunk_document(doc, SIZE, OVERLAP)
    assert [chunk.url for chunk in chunks] == ["/docs/guide", "/docs/guide#setup"]


def test_chunk_keeps_its_metadata():
    chunks = chunk_document(make_doc(Section("A > B", "b", "Body.")), SIZE, OVERLAP)
    chunk = chunks[0]
    assert (chunk.source, chunk.title, chunk.section, chunk.index) == (
        "docs/guide.mdx",
        "Guide",
        "A > B",
        0,
    )


def test_long_prose_stays_within_the_size_limit():
    doc = make_doc(Section("Setup", "setup", sentences(40)))
    chunks = chunk_document(doc, SIZE, OVERLAP)
    assert len(chunks) > 1
    assert all(len(chunk.text) <= SIZE for chunk in chunks)


def test_long_prose_loses_nothing():
    text = sentences(40)
    chunks = chunk_document(make_doc(Section("Setup", "setup", text)), SIZE, OVERLAP)
    joined = " ".join(chunk.text for chunk in chunks)
    for number in range(40):
        assert f"Sentence number {number} says" in joined


def test_consecutive_chunks_overlap():
    doc = make_doc(Section("Setup", "setup", sentences(40)))
    chunks = chunk_document(doc, SIZE, OVERLAP)
    for previous, current in pairwise(chunks):
        start = body_of(current).split("\n\n", 1)[0]
        assert 0 < len(start) <= OVERLAP
        assert body_of(previous).endswith(start)


def test_overlap_starts_on_a_word_boundary():
    assert overlap_tail("alpha bravo charlie delta echo", 12) == "delta echo"


def test_zero_overlap_repeats_nothing():
    doc = make_doc(Section("Setup", "setup", sentences(40)))
    chunks = chunk_document(doc, SIZE, 0)
    joined = "".join(body_of(chunk) for chunk in chunks)
    assert joined.count("Sentence number 10 says") == 1


def test_zero_overlap_tail_is_empty():
    assert overlap_tail("Run the development server:", 0) == ""


def test_zero_overlap_does_not_repeat_short_prose_before_code():
    # real shape: a short sentence, then code that fills the next chunk.
    code = "Code (bash):\n```bash\n" + "npm run build\n" * 12 + "```"
    text = f"Build your site for a specific locale:\n\n{code}"
    chunks = chunk_document(make_doc(Section("S", "s", text)), SIZE, 0)
    assert "".join(c.text for c in chunks).count("specific locale") == 1


def test_no_empty_chunk_when_a_code_header_exceeds_the_limit():
    text = f'Intro.\n\n```js title="{"x" * 200}"\nconst a = 1;\n```'
    chunks = split_body(text, 100, 20)
    assert chunks
    assert all(chunk.strip() for chunk in chunks)


def test_long_code_header_keeps_code_pieces_a_useful_size():
    lines = "\n".join(f"line_{n}" for n in range(40))
    code = f'```js title="{"x" * 200}"\n{lines}\n```'
    assert len(split_code(code, 100)) < 10


def test_sections_are_never_merged():
    doc = make_doc(Section("A", "a", "First."), Section("B", "b", "Second."))
    chunks = chunk_document(doc, SIZE, OVERLAP)
    assert [body_of(chunk) for chunk in chunks] == ["First.", "Second."]


def test_real_section_that_fits_is_unchanged():
    doc = make_doc(Section("Start your site", "start-your-site", START_YOUR_SITE))
    chunks = chunk_document(doc, 800, 150)
    assert len(chunks) == 1
    assert body_of(chunks[0]) == START_YOUR_SITE


def test_split_blocks_keeps_label_with_its_code():
    blocks = split_blocks(START_YOUR_SITE)
    assert [is_code for _, is_code in blocks] == [False, True, False]
    assert blocks[1][0] == "Code (bash):\n```bash\ncd my-website\nnpm run start\n```"


def test_blank_lines_inside_code_do_not_split_the_block():
    # real: docs/tutorial-extras/translate-your-site.mdx
    code = (
        "Code (bash):\n```bash\nmkdir -p i18n/fr/current/\n\n"
        "cp docs/intro.md i18n/fr/current/intro.md\n```"
    )
    blocks = split_blocks(f"Copy the file:\n\n{code}\n\nThen translate it.")
    assert blocks == [
        ("Copy the file:", False),
        (code, True),
        ("Then translate it.", False),
    ]


def test_code_block_that_fits_is_never_cut():
    code = "Code (js):\n```js\n" + "const value = 1;\n" * 10 + "```"
    text = f"{sentences(6)}\n\n{code}\n\n{sentences(6)}"
    chunks = chunk_document(make_doc(Section("S", "s", text)), SIZE, OVERLAP)
    assert sum(code in chunk.text for chunk in chunks) == 1
    assert all(fence_lines(chunk.text) % 2 == 0 for chunk in chunks)


def test_oversized_code_is_refenced_in_every_piece():
    lines = "\n".join(f"line_{n} = {n}" for n in range(60))
    code = f"Code (python):\n```python\n{lines}\n```"
    pieces = split_code(code, 150)
    assert len(pieces) > 1
    for piece in pieces:
        assert len(piece) <= 150
        assert piece.startswith("Code (python):\n```python\n")
        assert piece.endswith("\n```")
    for n in range(60):
        assert sum(f"line_{n} = {n}\n" in piece for piece in pieces) == 1


def test_oversized_nested_fence_closes_with_its_own_marks():
    code = "````md\n" + "```js\nvalue\n```\n" * 30 + "````"
    pieces = split_code(code, 120)
    assert all(piece.startswith("````md\n") for piece in pieces)
    assert all(piece.endswith("\n````") for piece in pieces)


def test_code_is_not_repeated_as_overlap():
    code = "Code (bash):\n```bash\nnpm run start\n```"
    assert overlap_tail(f"Intro.\n\n{code}", 60) == ""


def test_overlap_uses_only_the_prose_after_code():
    code = "```bash\nnpm run start\n```"
    assert overlap_tail(f"{code}\n\nThen open it.", 60) == "Then open it."


def test_word_longer_than_the_limit_is_hard_split():
    pieces = split_text("x" * 250, 100, (" ", ""))
    assert [len(piece) for piece in pieces] == [100, 100, 50]


def test_split_text_prefers_paragraphs():
    text = f"{'a' * 40}\n\n{'b' * 40}"
    pieces = split_text(text, 50, ("\n\n", " ", ""))
    assert [piece.strip() for piece in pieces] == ["a" * 40, "b" * 40]


def test_ids_are_stable():
    doc = make_doc(Section("Setup", "setup", sentences(40)))
    first = [chunk.id for chunk in chunk_document(doc, SIZE, OVERLAP)]
    second = [chunk.id for chunk in chunk_document(doc, SIZE, OVERLAP)]
    assert first == second


def test_id_changes_with_each_input():
    base = chunk_id("a.md", "S", 0, "text")
    assert base != chunk_id("b.md", "S", 0, "text")
    assert base != chunk_id("a.md", "T", 0, "text")
    assert base != chunk_id("a.md", "S", 1, "text")
    assert base != chunk_id("a.md", "S", 0, "other")


def test_id_fields_cannot_run_together():
    assert chunk_id("a", "bc", 0, "t") != chunk_id("ab", "c", 0, "t")


def test_identical_sections_get_different_ids():
    doc = make_doc(Section("Setup", "setup", "Same."), Section("Setup", "x", "Same."))
    chunks = chunk_document(doc, SIZE, OVERLAP)
    assert len({chunk.id for chunk in chunks}) == 2


def test_index_runs_across_the_document():
    doc = make_doc(Section("A", "a", sentences(20)), Section("B", "b", "Second."))
    chunks = chunk_document(doc, SIZE, OVERLAP)
    assert [chunk.index for chunk in chunks] == list(range(len(chunks)))


@pytest.mark.parametrize("overlap", [150, 200, -1])
def test_invalid_overlap_is_rejected(overlap):
    with pytest.raises(ValueError, match="CHUNK_OVERLAP"):
        chunk_document(make_doc(Section("S", "s", "Body.")), 300, overlap)
