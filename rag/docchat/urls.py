"""Map a docs file to its site URL, mirroring Docusaurus routing.

First version: page URLs only. Heading anchors arrive in Step 3.
The rules follow @docusaurus/plugin-content-docs 3.10 (lib/slug.js,
lib/numberPrefix.js and isCategoryIndex in lib/docs.js).
"""

import posixpath
import re
from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Any

# "2021-11-foo" and "7.0-foo" look like dates or versions, so they keep their digits.
_IGNORED_PREFIX = re.compile(r"^\d+[-_.]\d+")
_NUMBER_PREFIX = re.compile(r"^\d+\s*[-_.]+\s*(?P<suffix>[^-_.\s].*)$")


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
