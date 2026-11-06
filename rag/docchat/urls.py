"""Map a docs file to its site URL, mirroring Docusaurus routing.

Page rules follow @docusaurus/plugin-content-docs 3.10 (lib/slug.js,
lib/numberPrefix.js and isCategoryIndex in lib/docs.js). Anchor rules follow
@docusaurus/mdx-loader 3.10 (lib/remark/headings) and github-slugger 1.5.
"""

import html
import posixpath
import re
import unicodedata
from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Any

# "2021-11-foo" and "7.0-foo" look like dates or versions, so they keep their digits.
_IGNORED_PREFIX = re.compile(r"^\d+[-_.]\d+")
_NUMBER_PREFIX = re.compile(r"^\d+\s*[-_.]+\s*(?P<suffix>[^-_.\s].*)$")

# Explicit heading ids: "{/* #my-id */}", "<!-- #my-id -->" and "{#my-id}".
_COMMENT_ID = re.compile(
    r"^(?P<text>.*?)\s*(?:\{/\*(?P<mdx>.*?)\*/\}|<!--(?P<html>.*?)-->)\s*$"
)
_CLASSIC_ID = re.compile(r"^(?P<text>.*?)\s*\{#(?P<id>[^{}]+)\}\s*$")

_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_STRONG = re.compile(r"(\*\*|__)(.+?)\1")
_EMPHASIS = re.compile(r"(?<!\w)([*_])(.+?)\1(?!\w)")
_INLINE_TAG = re.compile(r"</?[A-Za-z][^>]*>")
_ESCAPE = re.compile(r"\\([!-/:-@\[-`{-~])")


def strip_number_prefix(name: str) -> str:
    """Turn '02-auth' into 'auth', leaving date-like names alone."""
    if _IGNORED_PREFIX.match(name):
        return name
    match = _NUMBER_PREFIX.match(name)
    return match.group("suffix") if match else name


def _is_category_index(file_name: str, dir_name: str) -> bool:
    """True for index, README, or a file named like its folder."""
    last_dir = dir_name.rsplit("/", 1)[-1].lower()
    return file_name.lower() in ("index", "readme", last_dir)


def doc_slug(rel_path: str, front_matter: Mapping[str, Any]) -> str:
    """Return a doc's slug. rel_path is relative to the docs folder."""
    path = PurePosixPath(rel_path)
    dir_name = path.parent.as_posix()
    slug = front_matter.get("slug")
    slug = str(slug) if slug is not None else None

    if slug and slug.startswith("/"):
        return slug

    if dir_name == ".":
        dir_slug = "/"
    else:
        parts = [strip_number_prefix(part) for part in dir_name.split("/")]
        dir_slug = "/" + "/".join(parts) + "/"

    if not slug and _is_category_index(path.stem, dir_name):
        return dir_slug

    doc_id = front_matter.get("id")
    base = slug or (str(doc_id) if doc_id is not None else None)
    base = base or strip_number_prefix(path.stem)

    resolved = posixpath.normpath(posixpath.join(dir_slug, base))
    return resolved + "/" if base.endswith("/") and resolved != "/" else resolved


def page_url(site_base_url: str, route_base: str, slug: str) -> str:
    """Join baseUrl, the docs route and a slug into a site path."""
    parts = [part.strip("/") for part in (site_base_url, route_base)]
    prefix = "/".join(part for part in parts if part)
    return f"/{prefix}{slug}" if prefix else slug


def split_heading_id(heading: str) -> tuple[str, str | None]:
    """Split raw heading text into (text, explicit id or None)."""
    match = _COMMENT_ID.match(heading)
    if match:
        comment = (match["mdx"] or match["html"] or "").split()
        word = comment[0] if comment else ""
        explicit = word[1:] if word.startswith("#") and len(word) > 1 else None
        return match["text"], explicit
    match = _CLASSIC_ID.match(heading)
    if match:
        return match["text"], match["id"].strip()
    return heading, None


def heading_text(heading: str) -> str:
    """Reduce inline Markdown to the text Docusaurus slugs and displays."""
    text = _LINK.sub(r"\1", heading)
    text = _INLINE_TAG.sub("", text)
    text = text.replace("`", "")
    text = _STRONG.sub(r"\2", text)
    text = _EMPHASIS.sub(r"\2", text)
    text = _ESCAPE.sub(r"\1", text)
    return html.unescape(text).strip()


def _keeps_in_slug(char: str) -> bool:
    """Keep letters, marks and digits, but not numerals like the 2 in m²."""
    category = unicodedata.category(char)
    return char in " -_" or category[0] in "LM" or category in ("Nd", "Nl")


def github_slug(text: str) -> str:
    """Lowercase, drop punctuation and symbols, turn spaces into hyphens."""
    return "".join(filter(_keeps_in_slug, text.lower())).replace(" ", "-")


class Slugger:
    """Make heading slugs unique within one page, as github-slugger does."""

    def __init__(self) -> None:
        self._occurrences: dict[str, int] = {}

    def slug(self, text: str) -> str:
        original = slug = github_slug(text)
        while slug in self._occurrences:
            self._occurrences[original] += 1
            slug = f"{original}-{self._occurrences[original]}"
        self._occurrences[slug] = 0
        return slug


def section_url(page: str, anchor: str) -> str:
    """Add an anchor to a page URL. An empty anchor means the top of the page."""
    return f"{page}#{anchor}" if anchor else page
