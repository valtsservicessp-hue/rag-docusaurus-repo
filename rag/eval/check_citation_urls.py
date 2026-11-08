"""Check that every URL the index can cite exists in the Docusaurus build.

Run from rag/, after npm run build and python ingest.py:
    python eval/check_citation_urls.py --build ../build
"""

import argparse
import sys
from collections import defaultdict
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote

# Run as a script, Python puts eval/ on the path, not rag/, so add rag/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from docchat.config import load_settings
from docchat.store import DocStore, StoreError


class _IdCollector(HTMLParser):
    """Collect every id attribute. The build writes some without quotes, so a
    real HTML parser is safer than a regex."""

    def __init__(self) -> None:
        super().__init__()
        self.ids: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if name == "id" and value:
                self.ids.add(value)


def page_file(build: Path, path: str, base_url: str) -> Path | None:
    """The HTML file Docusaurus wrote for a URL path, or None if there is none."""
    base = "/" + base_url.strip("/")
    if base != "/" and (path == base or path.startswith(base + "/")):
        path = path[len(base) :]
    relative = unquote(path).strip("/")
    candidates = [build / relative / "index.html", build / f"{relative}.html"]
    if not relative:
        candidates = [build / "index.html"]
    return next((file for file in candidates if file.is_file()), None)


def check_urls(urls: set[str], build: Path, base_url: str = "/") -> list[str]:
    """Return a problem for each URL whose page or #anchor is missing."""
    problems = []
    ids_by_file: dict[Path, set[str]] = {}
    for url in sorted(urls):
        path, _, anchor = url.partition("#")
        file = page_file(build, path, base_url)
        if file is None:
            problems.append(f"{url}: page not in the build (404)")
            continue
        if not anchor:
            continue
        if file not in ids_by_file:
            collector = _IdCollector()
            collector.feed(file.read_text(encoding="utf-8"))
            ids_by_file[file] = collector.ids
        if unquote(anchor) not in ids_by_file[file]:
            problems.append(f"{url}: no heading with id '{anchor}' on the page")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description="Check citation URLs.")
    parser.add_argument("--build", type=Path, default=Path("../build"))
    args = parser.parse_args()

    if not (args.build / "index.html").is_file():
        print(f"Error: no site build at {args.build}. Run: npm run build")
        return 1

    settings = load_settings()
    try:
        store = DocStore(settings.chroma_dir, settings.embed_model)
    except StoreError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    metadatas = store.collection.get(include=["metadatas"])["metadatas"]
    if not metadatas:
        print("Error: the index is empty. Run: python ingest.py", file=sys.stderr)
        return 1
    sources = defaultdict(set)
    for metadata in metadatas:
        sources[metadata["url"]].add(metadata["source"])

    problems = check_urls(set(sources), args.build, settings.site_base_url)
    print(f"Checked {len(sources)} URLs from {len(metadatas)} chunks.")
    if problems:
        for problem in problems:
            url = problem.split(": ", 1)[0]
            print(f"FAIL {problem} (from {', '.join(sorted(sources[url]))})")
        return 1
    print("All citation URLs exist in the build, including their anchors.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
