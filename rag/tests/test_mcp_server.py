"""MCP server tests: a real MCP client talks to the server in-process.

No port, no network and no real model: the store uses a fake embedder.
"""

from dataclasses import replace

import pytest
from mcp import Client

from docchat.chunking import Chunk
from docchat.config import load_settings
from docchat.store import DocStore
from mcp_server import build_server

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


class FakeEmbedder:
    """Docs and questions match, unless the text says it is off-topic."""

    def embed(self, texts):
        return [[0.0, 1.0] if "off-topic" in text else [1.0, 0.0] for text in texts]


def chunk(n: int) -> Chunk:
    return Chunk(
        id=f"chunk-{n}",
        source=f"docs/page{n}.mdx",
        title=f"Page {n}",
        section="Setup",
        url=f"/docs/page{n}#setup",
        index=0,
        text=f"Page {n} > Setup\n\nInstall step {n}.",
    )


@pytest.fixture
def server(tmp_path):
    settings = replace(
        load_settings(), public_site_url="https://docs.example.com/", min_score=0.38
    )
    embedder = FakeEmbedder()
    store = DocStore(tmp_path / "chroma", "test-model")
    store.sync([chunk(n) for n in range(3)], embedder)
    return build_server(settings, store, embedder)


async def search(server, **arguments):
    async with Client(server) as client:
        return await client.call_tool("search_docs", arguments)


async def test_tool_is_listed_as_read_only(server):
    async with Client(server) as client:
        tools = (await client.list_tools()).tools
    assert [tool.name for tool in tools] == ["search_docs"]
    assert tools[0].annotations.read_only_hint is True
    assert set(tools[0].input_schema["properties"]) == {"query", "k"}


async def test_returns_absolute_urls_with_anchor(server):
    result = await search(server, query="How do I install?", k=2)
    assert not result.is_error
    hits = result.structured_content["results"]
    assert len(hits) == 2
    assert hits[0]["url"].startswith("https://docs.example.com/docs/page")
    assert hits[0]["url"].endswith("#setup")
    assert "Install step" in hits[0]["snippet"]
    assert "Page" not in hits[0]["snippet"]  # The title prefix is not repeated.


async def test_off_topic_returns_nothing_with_a_note(server):
    result = await search(server, query="off-topic pasta recipe")
    assert result.structured_content["results"] == []
    assert "Not covered" in result.structured_content["note"]


@pytest.mark.parametrize(
    "arguments",
    [
        {"query": ""},
        {"query": "x" * 1001},
        {"query": "q", "k": 0},
        {"query": "q", "k": 11},
    ],
)
async def test_bad_arguments_are_refused(server, arguments):
    result = await search(server, **arguments)
    assert result.is_error
