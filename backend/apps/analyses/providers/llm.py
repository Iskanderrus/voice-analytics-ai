"""LLM boundary. Providers transport a prepared prompt and return raw text plus
usage; parsing and validation belong to the pipeline, so both providers are
held to the same contract and neither is trusted to return valid JSON."""

from dataclasses import dataclass, field
from typing import Any, Protocol, cast

import httpx
import httpx2
import openai
from openai.types.chat import ChatCompletionMessageParam
from openai.types.shared_params import ResponseFormatJSONSchema

from apps.analyses.providers.openai_errors import openai_client, translate_openai_error
from apps.common.errors import ErrorCode, permanent, retryable
from apps.prompts.builder import ChatMessage


@dataclass(frozen=True)
class StructuredAnalysisRequest:
    messages: list[ChatMessage]
    json_schema: dict[str, Any]
    schema_name: str


@dataclass(frozen=True)
class StructuredAnalysisResponse:
    content: str
    provider: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    metadata: dict[str, Any] = field(default_factory=dict)


class LLMProvider(Protocol):
    name: str
    model: str

    def generate_structured(self, request: StructuredAnalysisRequest) -> StructuredAnalysisResponse:
        """Raises ProcessingError (retryable or permanent) on transport failure."""
        ...


def _messages(request: StructuredAnalysisRequest) -> list[dict[str, str]]:
    return [{"role": m.role, "content": m.content} for m in request.messages]


class OllamaLLMProvider:
    """Local Ollama server; `format` constrains decoding to the JSON schema."""

    name = "ollama"

    def __init__(
        self,
        model: str,
        base_url: str,
        timeout: float,
        temperature: float = 0.0,
        transport: httpx.BaseTransport | None = None,
    ):
        self.model = model
        self.temperature = temperature
        self._client = httpx.Client(base_url=base_url, timeout=timeout, transport=transport)

    def generate_structured(self, request: StructuredAnalysisRequest) -> StructuredAnalysisResponse:
        payload = {
            "model": self.model,
            "messages": _messages(request),
            "format": request.json_schema,
            "stream": False,
            "options": {"temperature": self.temperature},
        }
        try:
            response = self._client.post("/api/chat", json=payload)
        except httpx.TimeoutException as exc:
            raise retryable(ErrorCode.LLM_PROVIDER_ERROR, "Ollama request timed out.") from exc
        except httpx.TransportError as exc:
            raise retryable(ErrorCode.LLM_PROVIDER_ERROR, "Ollama is unreachable.") from exc

        if response.status_code == 404:
            raise permanent(
                ErrorCode.PROVIDER_CONFIGURATION_ERROR,
                f"Ollama model '{self.model}' is not available (was it pulled?).",
            )
        if response.status_code == 429:
            raise retryable(ErrorCode.LLM_RATE_LIMITED, "Ollama is overloaded.")
        if response.status_code >= 500:
            raise retryable(
                ErrorCode.LLM_PROVIDER_ERROR, f"Ollama error (HTTP {response.status_code})."
            )
        if response.status_code != 200:
            raise permanent(
                ErrorCode.LLM_PROVIDER_ERROR,
                f"Ollama rejected request (HTTP {response.status_code}).",
            )

        body = response.json()
        return StructuredAnalysisResponse(
            content=body.get("message", {}).get("content", ""),
            provider=self.name,
            model=self.model,
            input_tokens=body.get("prompt_eval_count"),
            output_tokens=body.get("eval_count"),
            metadata={
                "done_reason": body.get("done_reason"),
                "total_duration_ms": round(body.get("total_duration", 0) / 1e6),
            },
        )


class OpenAICompatibleLLMProvider:
    """OpenAI Chat Completions, or any server exposing the same API (OPENAI_BASE_URL)."""

    name = "openai"

    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: str | None,
        timeout: float,
        temperature: float = 0.0,
        http_client: httpx2.Client | None = None,
    ):
        self.model = model
        self.temperature = temperature
        self._client = openai_client(api_key, base_url, timeout, http_client)

    def generate_structured(self, request: StructuredAnalysisRequest) -> StructuredAnalysisResponse:
        messages = cast(list[ChatCompletionMessageParam], _messages(request))
        response_format: ResponseFormatJSONSchema = {
            "type": "json_schema",
            "json_schema": {"name": request.schema_name, "schema": request.json_schema},
        }
        try:
            completion = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=self.temperature,
                response_format=response_format,
            )
        except openai.OpenAIError as exc:
            raise translate_openai_error(
                exc,
                rate_limited=ErrorCode.LLM_RATE_LIMITED,
                timeout=ErrorCode.LLM_PROVIDER_ERROR,
                failure=ErrorCode.LLM_PROVIDER_ERROR,
                bad_request=ErrorCode.LLM_PROVIDER_ERROR,
            ) from exc

        choice = completion.choices[0]
        usage = completion.usage
        return StructuredAnalysisResponse(
            content=choice.message.content or "",
            provider=self.name,
            model=completion.model or self.model,
            input_tokens=usage.prompt_tokens if usage else None,
            output_tokens=usage.completion_tokens if usage else None,
            metadata={"finish_reason": choice.finish_reason},
        )
