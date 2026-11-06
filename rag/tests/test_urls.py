"""URL and anchor tests. Expected values were checked against Docusaurus 3.10."""

import pytest

from docchat.loaders import split_sections
from docchat.urls import (
    Slugger,
    doc_slug,
    github_slug,
    heading_text,
    page_url,
    section_url,
    split_heading_id,
    strip_number_prefix,
)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("02-auth", "auth"),
        ("02_auth", "auth"),
        ("003 - doc", "doc"),
        ("intro", "intro"),
        ("2021-11-foo", "2021-11-foo"),
        ("7.0-foo", "7.0-foo"),
    ],
)
def test_strip_number_prefix(name, expected):
    assert strip_number_prefix(name) == expected


@pytest.mark.parametrize(
    ("rel_path", "expected"),
    [
        # real pages
        ("intro.mdx", "/intro"),
        ("tutorial-basics/create-a-page.mdx", "/tutorial-basics/create-a-page"),
        # number prefixes
        ("guides/02-auth.md", "/guides/auth"),
        ("01-guides/02_auth.md", "/guides/auth"),
        ("a/b/c/02-d.md", "/a/b/c/d"),
        ("2021-11-foo/bar.md", "/2021-11-foo/bar"),
        # index pages become the folder URL, with a trailing slash
        ("guides/index.md", "/guides/"),
        ("guides/README.md", "/guides/"),
        ("guides/guides.md", "/guides/"),
        ("Guides/guides.md", "/Guides/"),
        ("01-a/02-b/index.md", "/a/b/"),
        ("index.md", "/"),
        ("readme.mdx", "/"),
        # the index check sees raw names, before prefixes are removed
        ("01-guides/guides.md", "/guides/guides"),
        ("01-guides/01-guides.md", "/guides/"),
        ("guides/01-index.md", "/guides/index"),
    ],
)
def test_doc_slug_from_path(rel_path, expected):
    assert doc_slug(rel_path, {}) == expected


@pytest.mark.parametrize(
    ("front_matter", "expected"),
    [
        ({"id": "login"}, "/guides/login"),
        ({"slug": "/x"}, "/x"),
        ({"slug": "/"}, "/"),
        ({"slug": "x"}, "/guides/x"),
        ({"slug": "./z"}, "/guides/z"),
        ({"slug": "../y"}, "/y"),
        ({"slug": "x/"}, "/guides/x/"),
        ({"id": "login", "slug": "/x"}, "/x"),
    ],
)
def test_doc_slug_from_front_matter(front_matter, expected):
    assert doc_slug("guides/auth.md", front_matter) == expected


def test_index_page_ignores_id_but_not_slug():
    assert doc_slug("guides/index.md", {"id": "home"}) == "/guides/"
    assert doc_slug("guides/index.md", {"slug": "home"}) == "/guides/home"


@pytest.mark.parametrize(
    ("base", "route", "slug", "expected"),
    [
        ("/", "/docs", "/intro", "/docs/intro"),
        ("/my-site/", "/docs", "/intro", "/my-site/docs/intro"),
        ("/", "/", "/intro", "/intro"),
        ("/", "/docs", "/", "/docs/"),
    ],
)
def test_page_url(base, route, slug, expected):
    assert page_url(base, route, slug) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Getting Started", "getting-started"),
        ("What you'll need", "what-youll-need"),
        ("MDX and React Components", "mdx-and-react-components"),
        ("Congratulations!", "congratulations"),
        ("snake_case and kebab-case", "snake_case-and-kebab-case"),
        ("Guía de instalación", "guía-de-instalación"),
        ("A  B", "a--b"),
        ("Area in m²", "area-in-m"),
    ],
)
def test_github_slug(text, expected):
    assert github_slug(text) == expected


def test_slugger_numbers_duplicates():
    slugger = Slugger()
    assert [slugger.slug("Setup") for _ in range(3)] == ["setup", "setup-1", "setup-2"]


def test_slugger_skips_a_suffix_already_taken():
    slugger = Slugger()
    assert slugger.slug("a-1") == "a-1"
    assert slugger.slug("a") == "a"
    assert slugger.slug("a") == "a-2"


@pytest.mark.parametrize(
    ("heading", "expected"),
    [
        ("Plain", ("Plain", None)),
        ("Headings {/* #my-heading-id */}", ("Headings", "my-heading-id")),
        ("Heading Ids {/* #my-custom-id */}", ("Heading Ids", "my-custom-id")),
        ("Title {#custom-id}", ("Title", "custom-id")),
        ("Title <!-- #custom-id -->", ("Title", "custom-id")),
        ("Title {/* a note */}", ("Title", None)),
    ],
)
def test_split_heading_id(heading, expected):
    assert split_heading_id(heading) == expected


@pytest.mark.parametrize(
    ("heading", "expected"),
    [
        ("Use `npm` **now**", "Use npm now"),
        ("[Docs](./docs.md) home", "Docs home"),
        ("*Fast* and __safe__", "Fast and safe"),
        ("snake_case_name", "snake_case_name"),
        ("Tom &amp; Jerry", "Tom & Jerry"),
        (r"Literal \* star", "Literal * star"),
    ],
)
def test_heading_text(heading, expected):
    assert heading_text(heading) == expected


def test_section_url():
    assert section_url("/docs/intro", "start") == "/docs/intro#start"
    assert section_url("/docs/intro", "") == "/docs/intro"


def test_real_h1_counts_towards_duplicate_anchors():
    # real: docs/tutorial-basics/deploy-your-site.mdx. The site's H2 is "-1".
    body = "# Deploy your site\n\nIntro.\n\n## Deploy your site\n\nBody."
    h1, sections = split_sections(body)
    assert h1 == "Deploy your site"
    assert [(s.path, s.anchor) for s in sections] == [
        ("", ""),
        ("Deploy your site", "deploy-your-site-1"),
    ]


def test_real_custom_ids_and_nested_path():
    # real: docs/tutorial-basics/markdown-features.mdx
    body = (
        "## Headings {/* #my-heading-id */}\n\nText.\n\n"
        "### Heading Ids {/* #my-custom-id */}\n\nMore.\n\n## Links\n\nEnd."
    )
    _, sections = split_sections(body)
    assert [(s.path, s.anchor) for s in sections] == [
        ("Headings", "my-heading-id"),
        ("Headings > Heading Ids", "my-custom-id"),
        ("Links", "links"),
    ]


def test_explicit_ids_do_not_count_towards_duplicates():
    body = "## Setup {#setup}\n\nA.\n\n## Setup\n\nB."
    _, sections = split_sections(body)
    assert [s.anchor for s in sections] == ["setup", "setup"]


def test_empty_section_still_counts_towards_duplicates():
    body = "## Setup\n\n## Setup\n\nBody."
    _, sections = split_sections(body)
    assert [s.anchor for s in sections] == ["setup-1"]


def test_headings_in_code_are_ignored():
    # real: docs/tutorial-basics/create-a-page.mdx
    body = "## Page\n\n```mdx\n# My Markdown page\n\nThis is a Markdown page\n```"
    h1, sections = split_sections(body)
    assert h1 is None
    assert len(sections) == 1


def test_closing_hashes_are_removed_but_csharp_is_kept():
    _, sections = split_sections("## Setup ##\n\nA.\n\n## Learn C#\n\nB.")
    assert [s.path for s in sections] == ["Setup", "Learn C#"]


def test_ci_demo():
    assert 1 == 2
