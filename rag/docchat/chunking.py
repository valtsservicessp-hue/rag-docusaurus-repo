"""Split documents into chunks small enough to embed and cite.

Run from rag/:  python -m docchat.chunking --stats
"""

import argparse
import hashlib
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass

from docchat.config import load_settings
from docchat.loaders import Document, load_documents
from docchat.mdx_clean import scan_fences
from docchat.urls import section_url

PROSE_SEPARATORS = ("\n\n", "\n", ". ", " ", "")
CODE_SEPARATORS = ("\n", " ", "")

_CODE_LABEL = re.compile(r"^Code \(.*\):$")
_FENCE_MARK = re.compile(r"`{3,}|~{3,}")


@dataclass(frozen=True)
class Chunk:
    id: str
    source: str
    title: str
    section: str
    url: str  # Page URL plus the section's anchor.
    index: int  # Position within the document.
    text: str  # "title > section", a blank line, then the body.


def chunk_id(source: str, section: str, index: int, text: str) -> str:
    """Hash a chunk's identity, so the same content always gets the same id."""
    key = "\0".join((source, section, str(index), text))
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]


def split_text(text: str, limit: int, separators: tuple[str, ...]) -> list[str]:
    """Split on the largest separator that works, recursing only when needed."""
    if len(text) <= limit:
        return [text]

    separator, *rest = separators
    if not separator:
        return [text[start : start + limit] for start in range(0, len(text), limit)]

    parts = text.split(separator)
    parts = [part + separator for part in parts[:-1]] + parts[-1:]

    pieces: list[str] = []
    current = ""
    for part in parts:
        if len(current) + len(part) <= limit:
            current += part
            continue
        if current:
            pieces.append(current)
            current = ""
        if len(part) <= limit:
            current = part
        else:
            pieces.extend(split_text(part, limit, tuple(rest)))
    if current:
        pieces.append(current)
    return pieces


def split_blocks(text: str) -> list[tuple[str, bool]]:
    """Split cleaned text into (block, is_code), keeping each fence in one block."""
    blocks: list[tuple[str, bool]] = []
    prose: list[str] = []
    code: list[str] = []

    def flush_prose() -> None:
        block = "\n".join(prose).strip()
        if block:
            blocks.append((block, False))
        prose.clear()

    def flush_code() -> None:
        if code:
            blocks.append(("\n".join(code), True))
        code.clear()

    for line, kind in scan_fences(text):
        if kind == "text":
            flush_code()
            prose.append(line)
            continue
        if kind == "open":
            flush_code()
            # The "Code (bash):" label belongs with the block it describes.
            label = prose.pop() if prose and _CODE_LABEL.match(prose[-1]) else None
            flush_prose()
            if label:
                code.append(label)
        code.append(line)

    flush_code()
    flush_prose()
    return blocks


def split_code(block: str, limit: int) -> list[str]:
    """Split an oversized code block, giving every piece its own fences."""
    lines = block.split("\n")
    has_label = bool(_CODE_LABEL.match(lines[0]))
    head = lines[: 2 if has_label else 1]
    body = lines[len(head) :]

    closing = _FENCE_MARK.search(head[-1])[0]
    if body and body[-1].strip() == closing:
        body = body[:-1]

    header = "\n".join(head)
    # A very long fence line must not leave room for only a few characters.
    room = max(limit - len(header) - len(closing) - 2, limit // 2)
    pieces = split_text("\n".join(body), room, CODE_SEPARATORS)
    return [f"{header}\n{piece.strip(chr(10))}\n{closing}" for piece in pieces]


def overlap_tail(text: str, overlap: int) -> str:
    """Return the end of a chunk to repeat in the next one.

    Code is never repeated, because a partial block would break its fences.
    """
    if overlap == 0:
        return ""

    marks = list(_FENCE_MARK.finditer(text))
    prose = text[marks[-1].end() :] if marks else text
    prose = prose.strip()
    if len(prose) <= overlap:
        return prose

    tail = prose[-overlap:]
    # Start on a word boundary rather than in the middle of a word.
    boundary = re.search(r"\s", tail)
    return tail[boundary.end() :].strip() if boundary else tail


def split_body(text: str, limit: int, overlap: int) -> list[str]:
    """Pack a section's text into pieces no longer than limit."""
    if len(text) <= limit:
        return [text]

    pieces: list[str] = []
    for block, is_code in split_blocks(text):
        if not is_code:
            # Leave room for the overlap that will be added in front of this piece.
            pieces.extend(
                piece.strip()
                for piece in split_text(block, limit - overlap, PROSE_SEPARATORS)
                if piece.strip()
            )
        elif len(block) <= limit:
            pieces.append(block)
        else:
            pieces.extend(split_code(block, limit))

    chunks: list[str] = []
    current = ""
    for piece in pieces:
        joined = f"{current}\n\n{piece}" if current else piece
        if len(joined) <= limit:
            current = joined
            continue
        if current:
            chunks.append(current)
        tail = overlap_tail(current, overlap)
        with_tail = f"{tail}\n\n{piece}" if tail else piece
        current = with_tail if len(with_tail) <= limit else piece
    if current:
        chunks.append(current)
    return chunks


def chunk_document(doc: Document, chunk_size: int, overlap: int) -> list[Chunk]:
    """Chunk one document. Chunks never cross a section, so citations stay exact."""
    if not 0 <= overlap < chunk_size // 2:
        raise ValueError("CHUNK_OVERLAP must be under half of CHUNK_SIZE")

    chunks: list[Chunk] = []
    for section in doc.sections:
        prefix = f"{doc.title} > {section.path}" if section.path else doc.title
        limit = max(chunk_size - len(prefix) - 2, chunk_size // 2)
        for body in split_body(section.text, limit, overlap):
            text = f"{prefix}\n\n{body}"
            # The index runs across the document, so two identical sections
            # on one page still get different ids.
            index = len(chunks)
            chunks.append(
                Chunk(
                    id=chunk_id(doc.source, section.path, index, text),
                    source=doc.source,
                    title=doc.title,
                    section=section.path,
                    url=section_url(doc.url, section.anchor),
                    index=index,
                    text=text,
                )
            )
    return chunks


def chunk_documents(
    documents: Iterable[Document], chunk_size: int, overlap: int
) -> list[Chunk]:
    return [
        chunk for doc in documents for chunk in chunk_document(doc, chunk_size, overlap)
    ]


def print_stats(chunks: list[Chunk], chunk_size: int, overlap: int) -> None:
    pages: dict[str, list[int]] = {}
    for chunk in chunks:
        pages.setdefault(chunk.source, []).append(len(chunk.text))

    width = max((len(source) for source in pages), default=6)
    print(f"CHUNK_SIZE={chunk_size}  CHUNK_OVERLAP={overlap}\n")
    print(f"{'SOURCE':<{width}}  CHUNKS  MIN  AVG  MAX")
    for source, sizes in pages.items():
        average = sum(sizes) // len(sizes)
        print(
            f"{source:<{width}}  {len(sizes):>6}  {min(sizes):>3}  "
            f"{average:>3}  {max(sizes):>3}"
        )

    sizes = [len(chunk.text) for chunk in chunks]
    if sizes:
        print(
            f"\nTotal: {len(sizes)} chunks across {len(pages)} pages, "
            f"{min(sizes)}-{max(sizes)} chars, average {sum(sizes) // len(sizes)}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect how the docs are chunked.")
    parser.add_argument("--stats", action="store_true", help="print stats per page")
    args = parser.parse_args()
    if not args.stats:
        parser.error("nothing to do: pass --stats")

    # Keep a non-ASCII path from crashing on a legacy Windows console.
    sys.stdout.reconfigure(errors="replace")
    settings = load_settings()
    documents, _ = load_documents(settings)
    chunks = chunk_documents(documents, settings.chunk_size, settings.chunk_overlap)
    print_stats(chunks, settings.chunk_size, settings.chunk_overlap)


if __name__ == "__main__":
    main()
