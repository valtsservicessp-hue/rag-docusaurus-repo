"""LLM providers. Each one only sends a prompt and returns text.

Everything else (retrieval, prompts, citations) lives outside this file and is
the same for every provider, so a new provider is one new class here.
"""

from dataclasses import dataclass
from typing import Protocol

import anthropic
import httpx

from docchat.config import Settings

TEMPERATURE = 0.1
ANTHROPIC_TIMEOUT = 120.0
ANTHROPIC_MAX_TOKENS = 16000
# Chat-style questions do well at low effort, which is also faster and cheaper.
ANTHROPIC_EFFORT = "low"
ANTHROPIC_FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LLMError(RuntimeError):
    """The provider could not answer. The message says what to do next."""


@dataclass(frozen=True)
class LLMResult:
    text: str
    input_tokens: int | None
    output_tokens: int | None
    provider: str
    model: str


class LLMClient(Protocol):
    provider: str
    model: str

    def complete(self, system: str, user: str) -> LLMResult: ...


class NoneClient:
    """No LLM: callers fall back to retrieval-only answers."""

    provider = "none"
    model = ""

    def complete(self, system: str, user: str) -> LLMResult:
        return LLMResult("", 0, 0, self.provider, self.model)


class OllamaClient:
    """A local model served by Ollama. Nothing leaves this machine."""

    provider = "ollama"

    def __init__(
        self,
        url: str,
        model: str,
        timeout: float = 120.0,
        num_gpu: int | None = None,
        http: httpx.Client | None = None,
    ) -> None:
        self.url = url.rstrip("/")
        self.model = model
        self.num_gpu = num_gpu
        self._http = http or httpx.Client(timeout=timeout)

    def complete(self, system: str, user: str) -> LLMResult:
        options: dict[str, float | int] = {"temperature": TEMPERATURE}
        if self.num_gpu is not None:
            options["num_gpu"] = self.num_gpu

        try:
            response = self._http.post(
                f"{self.url}/api/chat",
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "stream": False,
                    "options": options,
                },
            )
        except httpx.ConnectError as error:
            raise LLMError(
                f"Cannot reach Ollama at {self.url}. "
                "Start Ollama or set LLM_PROVIDER=none."
            ) from error
        except httpx.TimeoutException as error:
            raise LLMError(
                f"Ollama did not answer in time using {self.model}. A cold start "
                "on CPU is slow: raise OLLAMA_TIMEOUT or try again."
            ) from error

        if response.status_code == 404:
            raise LLMError(
                f"Ollama has no model {self.model}. Run: ollama pull {self.model}"
            )
        if response.status_code != 200:
            raise LLMError(f"Ollama error {response.status_code}: {response.text}")

        data = response.json()
        return LLMResult(
            text=data["message"]["content"],
            input_tokens=data.get("prompt_eval_count"),
            output_tokens=data.get("eval_count"),
            provider=self.provider,
            model=self.model,
        )


class AnthropicClient:
    """Claude through the Anthropic API.

    Sampling settings are not sent: Claude Sonnet 5.5 rejects a non-default
    temperature, so effort is the setting that controls this provider.
    """

    provider = "anthropic"

    def __init__(
        self,
        api_key: str | None,
        model: str,
        client: anthropic.Anthropic | None = None,
    ) -> None:
        if client is None:
            if not api_key:
                raise LLMError(
                    "ANTHROPIC_API_KEY is not set. "
                    "Add it to rag/.env or set LLM_PROVIDER=none."
                )
            client = anthropic.Anthropic(api_key=api_key, timeout=ANTHROPIC_TIMEOUT)
        self.model = model
        self._client = client

    def complete(self, system: str, user: str) -> LLMResult:
        try:
            response = self._client.beta.messages.create(
                model=self.model,
                max_tokens=ANTHROPIC_MAX_TOKENS,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={"effort": ANTHROPIC_EFFORT},
                # If a safety classifier declines, retry on a fallback model.
                betas=[ANTHROPIC_FALLBACK_BETA],
                fallbacks="default",
            )
        except anthropic.AuthenticationError as error:
            raise LLMError("Anthropic rejected ANTHROPIC_API_KEY.") from error
        except anthropic.NotFoundError as error:
            raise LLMError(f"Anthropic has no model {self.model}.") from error
        except anthropic.RateLimitError as error:
            raise LLMError("Anthropic rate limit reached. Try again soon.") from error
        except anthropic.APIStatusError as error:
            raise LLMError(f"Anthropic error {error.status_code}.") from error
        except anthropic.APIConnectionError as error:
            raise LLMError("Cannot reach the Anthropic API.") from error

        if response.stop_reason == "refusal":
            raise LLMError("Claude declined to answer this question.")

        text = "".join(block.text for block in response.content if block.type == "text")
        return LLMResult(
            text=text,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            provider=self.provider,
            model=self.model,
        )


def get_llm_client(settings: Settings, provider: str | None = None) -> LLMClient:
    """Pick the client from LLM_PROVIDER, or from an explicit override."""
    name = (provider or settings.llm_provider).strip().lower()
    if name == "none":
        return NoneClient()
    if name == "ollama":
        return OllamaClient(
            settings.ollama_url,
            settings.ollama_model,
            timeout=settings.ollama_timeout,
            num_gpu=settings.ollama_num_gpu,
        )
    if name == "anthropic":
        return AnthropicClient(settings.anthropic_api_key, settings.claude_model)
    raise LLMError(f"Unknown LLM_PROVIDER {name!r}. Use none, ollama or anthropic.")
