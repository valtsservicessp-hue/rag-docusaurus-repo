"""Read the site's Markdown files into Document objects.

Run from rag/:  python -m docchat.loaders --inventory
"""

import argparse
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import frontmatter
import yaml

from docchat.config import Settings, load_settings
from docchat.mdx_clean import clean_mdx, scan_fences
from docchat.urls import (
    Slugger,
    doc_slug,
    heading_text,
    page_url,
    split_heading_id,
    strip_number_prefix,
)

logger = logging.getLogger(__name__)

EXTENSIONS = {".md", ".mdx"}
EXCLUDED_DIRS = {"node_modules", "build", "__tests__"}

_HEADING = re.compile(r"^ {0,3}(?P<hashes>#{1,6})[ \t]+(?P<text>.+?)[ \t]*$")
_CLOSING_HASHES = re.compile(r"[ \t]+#+$")


@dataclass(frozen=True)
class Section:
    path: str  # Heading path below the page title, such as "Setup > Windows".
    anchor: str  # Heading id on the page. Empty means the top of the page.
    text: str  # Cleaned text.


@dataclass(frozen=True)
class Document:
    source: str  # Path from the site root, with forward slashes.
    title: str
    url: str
    sections: tuple[Section, ...]
    unlisted: bool = False

    @property
    def char_count(self) -> int:
        return sum(len(section.text) for section in self.sections)


@dataclass(frozen=True)
class Skipped:
    source: str
    reason: str


def split_sections(body: str) -> tuple[str | None, list[Section]]:
    """Split MDX by headings. Returns (first H1, sections with text).

    Splitting runs before cleaning, because cleaning drops the {/* #id */}
    comments that set anchors. Headings inside code blocks are ignored.
    """
    h1 = None
    stack: list[tuple[int, str]] = []
    current_path = ""
    current_anchor = ""
    current_lines: list[str] = []
    sections: list[Section] = []
    slugger = Slugger()

    def flush() -> None:
        text = clean_mdx("\n".join(current_lines))
        if text:
            sections.append(Section(current_path, current_anchor, text))
        current_lines.clear()

    for line, kind in scan_fences(body):
        heading = _HEADING.match(line) if kind == "text" else None
        if not heading:
            current_lines.append(line)
            continue

        flush()
        level = len(heading["hashes"])
        raw, explicit_id = split_heading_id(heading["text"])
        text = heading_text(_CLOSING_HASHES.sub("", raw))
        # Every heading counts towards duplicates, even an H1 with no anchor.
        anchor = explicit_id or slugger.slug(text)

        if level == 1:
            h1 = h1 or text
            stack.clear()
            current_anchor = ""
        else:
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, text))
            current_anchor = anchor
        current_path = " > ".join(name for _, name in stack)

    flush()
    return h1, sections


def _excluded_reason(rel_parts: tuple[str, ...]) -> str | None:
    """Mirror Docusaurus's default excludes: _partials and _folders."""
    *folders, file_name = rel_parts
    for folder in folders:
        if folder in EXCLUDED_DIRS:
            return f"excluded folder {folder}/"
        if folder.startswith("_"):
            return f"underscore folder {folder}/"
    if file_name.startswith("_"):
        return "underscore partial"
    return None


def load_file(path: Path, settings: Settings) -> Document | Skipped:
    """Load one Markdown file, or say why it was skipped."""
    source = path.relative_to(settings.site_root).as_posix()
    content_dir, _, rel_path = source.partition("/")

    if content_dir != "docs":
        return Skipped(source, f"no URL rule yet for {content_dir}/")

    try:
        post = frontmatter.loads(path.read_text(encoding="utf-8-sig"))
    except (yaml.YAMLError, UnicodeDecodeError) as error:
        return Skipped(source, f"unreadable: {error}")

    meta = post.metadata
    if meta.get("draft") is True:
        return Skipped(source, "draft: true")

    h1, sections = split_sections(post.content)
    fallback = meta.get("id") or strip_number_prefix(path.stem)
    return Document(
        source=source,
        title=str(meta.get("title") or h1 or fallback),
        url=page_url(
            settings.site_base_url,
            settings.docs_route_base,
            doc_slug(rel_path, meta),
        ),
        sections=tuple(sections),
        unlisted=meta.get("unlisted") is True,
    )


def load_documents(settings: Settings) -> tuple[list[Document], list[Skipped]]:
    """Walk CONTENT_DIRS. Returns published documents and skipped files."""
    documents: list[Document] = []
    skipped: list[Skipped] = []

    for content_dir in settings.content_dirs:
        root = settings.site_root / content_dir
        if not root.is_dir():
            skipped.append(Skipped(content_dir, "content folder not found"))
            continue
        for path in sorted(root.rglob("*")):
            if path.suffix.lower() not in EXTENSIONS or not path.is_file():
                continue
            reason = _excluded_reason(path.relative_to(root).parts)
            if reason:
                source = path.relative_to(settings.site_root).as_posix()
                skipped.append(Skipped(source, reason))
                continue
            result = load_file(path, settings)
            target = documents if isinstance(result, Document) else skipped
            target.append(result)

    for item in skipped:
        logger.info("Skipped %s: %s", item.source, item.reason)
    return documents, skipped


def print_inventory(documents: list[Document], skipped: list[Skipped]) -> None:
    source_width = max((len(doc.source) for doc in documents), default=6)
    title_width = max((len(doc.title) for doc in documents), default=5)
    url_width = max((len(doc.url) for doc in documents), default=3)

    print(
        f"{'SOURCE':<{source_width}}  {'TITLE':<{title_width}}  "
        f"{'URL':<{url_width}}  SECTIONS  CHARS  FLAGS"
    )
    for doc in documents:
        flags = "unlisted" if doc.unlisted else ""
        print(
            f"{doc.source:<{source_width}}  {doc.title:<{title_width}}  "
            f"{doc.url:<{url_width}}  {len(doc.sections):>8}  "
            f"{doc.char_count:>5}  {flags}".rstrip()
        )

    print(f"\nSkipped: {len(skipped)}")
    for item in skipped:
        print(f"  {item.source}: {item.reason}")

    sections = sum(len(doc.sections) for doc in documents)
    chars = sum(doc.char_count for doc in documents)
    print(f"\nTotal: {len(documents)} pages, {sections} sections, {chars} chars")


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect the site's content files.")
    parser.add_argument(
        "--inventory", action="store_true", help="list every content file"
    )
    args = parser.parse_args()
    if not args.inventory:
        parser.error("nothing to do: pass --inventory")

    # Keep a non-ASCII title from crashing on a legacy Windows console.
    sys.stdout.reconfigure(errors="replace")
    print_inventory(*load_documents(load_settings()))


if __name__ == "__main__":
    main()
