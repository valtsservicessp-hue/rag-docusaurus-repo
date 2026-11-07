"""Provider tests. HTTP is mocked, so these never touch the network."""

import json
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest

from docchat.config import load_settings
from docchat.llm import (
    AnthropicClient,
    LLMError,
    NoneClient,
    OllamaClient,
    get_llm_client,
)

OLLAMA_REPLY = {
    "message": {"role": "assistant", "content": "Hello [1]."},
    "prompt_eval_count": 50,
    "eval_count": 7,
}


def ollama_with(handler, **kwargs) -> OllamaClient:
    http = httpx.Client(transport=httpx.MockTransport(handler))
    return OllamaClient("http://ollama.test/", "test-model", http=http, **kwargs)


def reply_with(status: int, body: dict):
    return lambda request: httpx.Response(status, json=body)


def raises_error(error: Exception):
    def handler(request):
        raise error

    return handler


class FakeAnthropic:
    """Stand in for anthropic.Anthropic and record the request."""

    def __init__(self, stop_reason: str = "end_turn"):
        self.kwargs: dict = {}
        self.stop_reason = stop_reason
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            stop_reason=self.stop_reason,
            usage=SimpleNamespace(input_tokens=40, output_tokens=9),
            content=[
                SimpleNamespace(type="thinking", thinking=""),
                SimpleNamespace(type="text", text="Hello "),
                SimpleNamespace(type="text", text="[1]."),
            ],
        )


# --- NoneClient ---


def test_none_client_returns_no_text():
    result = NoneClient().complete("system", "user")
    assert result.text == ""
    assert result.provider == "none"


# --- OllamaClient ---


def test_ollama_returns_text_and_token_counts():
    result = ollama_with(reply_with(200, OLLAMA_REPLY)).complete("s", "u")
    assert result.text == "Hello [1]."
    assert (result.input_tokens, result.output_tokens) == (50, 7)
    assert (result.provider, result.model) == ("ollama", "test-model")


def test_ollama_request_shape():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=OLLAMA_REPLY)

    ollama_with(handler).complete("the system", "the user")
    assert seen["url"] == "http://ollama.test/api/chat"
    assert seen["body"]["model"] == "test-model"
    assert seen["body"]["stream"] is False
    assert seen["body"]["options"] == {"temperature": 0.1}
    assert seen["body"]["messages"] == [
        {"role": "system", "content": "the system"},
        {"role": "user", "content": "the user"},
    ]


def test_ollama_can_force_cpu_only():
    seen = {}

    def handler(request):
        seen["options"] = json.loads(request.content)["options"]
        return httpx.Response(200, json=OLLAMA_REPLY)

    ollama_with(handler, num_gpu=0).complete("s", "u")
    assert seen["options"]["num_gpu"] == 0


def test_ollama_token_counts_are_optional():
    body = {"message": {"content": "Hi"}}
    result = ollama_with(reply_with(200, body)).complete("s", "u")
    assert (result.input_tokens, result.output_tokens) == (None, None)


def test_ollama_not_running_says_what_to_do():
    client = ollama_with(raises_error(httpx.ConnectError("refused")))
    with pytest.raises(LLMError, match="Start Ollama or set LLM_PROVIDER=none"):
        client.complete("s", "u")


def test_ollama_timeout_is_a_clear_error():
    client = ollama_with(raises_error(httpx.ReadTimeout("slow")))
    with pytest.raises(LLMError, match="OLLAMA_TIMEOUT"):
        client.complete("s", "u")


def test_ollama_missing_model_says_how_to_pull_it():
    client = ollama_with(reply_with(404, {"error": "model not found"}))
    with pytest.raises(LLMError, match="ollama pull test-model"):
        client.complete("s", "u")


def test_ollama_server_error_shows_ollamas_message():
    client = ollama_with(reply_with(500, {"error": "llama-server terminated"}))
    with pytest.raises(LLMError, match="llama-server terminated"):
        client.complete("s", "u")


# --- AnthropicClient ---


def test_anthropic_requires_a_key():
    with pytest.raises(LLMError, match="ANTHROPIC_API_KEY is not set"):
        AnthropicClient(None, "claude-sonnet-5-5")


def test_anthropic_joins_text_blocks_and_skips_others():
    client = AnthropicClient(None, "claude-sonnet-5-5", client=FakeAnthropic())
    result = client.complete("s", "u")
    assert result.text == "Hello [1]."
    assert (result.input_tokens, result.output_tokens) == (40, 9)
    assert (result.provider, result.model) == ("anthropic", "claude-sonnet-5-5")


def test_anthropic_request_shape():
    fake = FakeAnthropic()
    AnthropicClient(None, "claude-sonnet-5-5", client=fake).complete("sys", "usr")
    assert fake.kwargs["model"] == "claude-sonnet-5-5"
    assert fake.kwargs["system"] == "sys"
    assert fake.kwargs["messages"] == [{"role": "user", "content": "usr"}]
    assert fake.kwargs["fallbacks"] == "default"


def test_anthropic_never_sends_sampling_settings():
    fake = FakeAnthropic()
    AnthropicClient(None, "claude-sonnet-5-5", client=fake).complete("s", "u")
    assert "temperature" not in fake.kwargs


def test_anthropic_refusal_is_an_error():
    fake = FakeAnthropic(stop_reason="refusal")
    with pytest.raises(LLMError, match="declined"):
        AnthropicClient(None, "claude-sonnet-5-5", client=fake).complete("s", "u")


# --- Factory ---


@pytest.fixture
def settings():
    return replace(load_settings(), anthropic_api_key=None)


@pytest.mark.parametrize(
    ("provider", "expected"),
    [("none", NoneClient), ("ollama", OllamaClient), ("OLLAMA", OllamaClient)],
)
def test_factory_picks_the_client(settings, provider, expected):
    assert isinstance(get_llm_client(settings, provider), expected)


def test_factory_uses_llm_provider_by_default(settings):
    client = get_llm_client(replace(settings, llm_provider="none"))
    assert isinstance(client, NoneClient)


def test_factory_override_beats_llm_provider(settings):
    client = get_llm_client(replace(settings, llm_provider="ollama"), "none")
    assert isinstance(client, NoneClient)


def test_factory_passes_the_ollama_settings(settings):
    configured = replace(
        settings, ollama_url="http://x:1", ollama_model="m", ollama_num_gpu=0
    )
    client = get_llm_client(configured, "ollama")
    assert (client.url, client.model, client.num_gpu) == ("http://x:1", "m", 0)


def test_factory_needs_a_key_for_anthropic(settings):
    with pytest.raises(LLMError, match="ANTHROPIC_API_KEY"):
        get_llm_client(settings, "anthropic")


def test_factory_rejects_an_unknown_provider(settings):
    with pytest.raises(LLMError, match="none, ollama or anthropic"):
        get_llm_client(settings, "gpt")
