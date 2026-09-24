"""Local and cloud providers must be interchangeable for the pipeline.

Each contract runs the same semantic assertions against both implementations,
with the HTTP layer (or the local model) replaced at the lowest boundary.
"""

import json
from dataclasses import dataclass
from pathlib import Path

import av
import httpx
import httpx2
import pytest

from apps.analyses.providers.llm import (
    OllamaLLMProvider,
    OpenAICompatibleLLMProvider,
    StructuredAnalysisRequest,
)
from apps.analyses.providers.transcription import (
    AudioSource,
    LocalWhisperTranscriptionProvider,
    OpenAITranscriptionProvider,
)
from apps.common.errors import ErrorCode, ProcessingError
from apps.prompts.builder import build_standard_prompt

CONTENT = '{"summary": "ok"}'


# --- LLM ---------------------------------------------------------------------------


def ollama_body(content: str) -> dict:
    return {
        "message": {"role": "assistant", "content": content},
        "prompt_eval_count": 120,
        "eval_count": 30,
        "done_reason": "stop",
        "total_duration": 2_000_000_000,
    }


def openai_body(content: str) -> dict:
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 0,
        "model": "gpt-4o-mini",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150},
    }


TIMEOUT = object()


def _transport(lib, reply):
    """Adapts a library-neutral `reply(json_payload) -> (status, body) | TIMEOUT`
    to the provider's HTTP library (httpx for Ollama, the SDK's httpx2 for OpenAI)."""

    def handler(request):
        outcome = reply(json.loads(request.content))
        if outcome is TIMEOUT:
            raise lib.ReadTimeout("slow", request=request)
        status, body = outcome
        return lib.Response(status, json=body)

    return lib.MockTransport(handler)


def make_ollama(reply):
    return OllamaLLMProvider(
        "qwen2.5:3b-instruct", "http://ollama", 5, transport=_transport(httpx, reply)
    )


def make_openai(reply):
    client = httpx2.Client(transport=_transport(httpx2, reply))
    return OpenAICompatibleLLMProvider("gpt-4o-mini", "sk-test", None, 5, http_client=client)


LLM_PROVIDERS = [(make_ollama, ollama_body), (make_openai, openai_body)]
LLM_IDS = ["ollama", "openai"]


@pytest.fixture
def request_():
    prompt = build_standard_prompt("Customer: the price is too high.")
    return StructuredAnalysisRequest(
        messages=prompt.messages, json_schema=prompt.json_schema, schema_name=prompt.schema_name
    )


@pytest.mark.parametrize(("make", "body"), LLM_PROVIDERS, ids=LLM_IDS)
def test_llm_returns_raw_content_usage_and_identity(make, body, request_):
    sent = {}

    def reply(payload):
        sent.update(payload)
        return 200, body(CONTENT)

    provider = make(reply)
    response = provider.generate_structured(request_)

    assert response.content == CONTENT
    assert (response.input_tokens, response.output_tokens) == (120, 30)
    assert response.provider == provider.name
    assert response.model == provider.model
    # Role separation reaches the wire: the transcript is its own user message.
    assert [m["role"] for m in sent["messages"]] == ["system", "user"]
    assert "price is too high" not in sent["messages"][0]["content"]


@pytest.mark.parametrize(("make", "body"), LLM_PROVIDERS, ids=LLM_IDS)
@pytest.mark.parametrize(
    ("status", "retryable", "code"),
    [
        (429, True, ErrorCode.LLM_RATE_LIMITED),
        (503, True, ErrorCode.LLM_PROVIDER_ERROR),
        (404, False, ErrorCode.PROVIDER_CONFIGURATION_ERROR),
    ],
)
def test_llm_http_errors_map_to_the_same_error_model(make, body, request_, status, retryable, code):
    provider = make(lambda payload: (status, {"error": {"message": "x"}}))

    with pytest.raises(ProcessingError) as raised:
        provider.generate_structured(request_)

    assert (raised.value.retryable, raised.value.code) == (retryable, code)


@pytest.mark.parametrize(("make", "body"), LLM_PROVIDERS, ids=LLM_IDS)
def test_llm_timeouts_are_retryable(make, body, request_):
    with pytest.raises(ProcessingError) as raised:
        make(lambda payload: TIMEOUT).generate_structured(request_)

    assert raised.value.retryable
    assert raised.value.code == ErrorCode.LLM_PROVIDER_ERROR


# --- STT ---------------------------------------------------------------------------


@dataclass
class _Segment:
    text: str


@dataclass
class _Info:
    language: str = "en"
    language_probability: float = 0.98
    duration: float = 4.2
    duration_after_vad: float = 3.9


class _FakeWhisperModel:
    def __init__(self, error: Exception | None = None):
        self.error = error

    def transcribe(self, path, **kwargs):
        if self.error:
            raise self.error
        return iter([_Segment(" Hello there. "), _Segment("Pricing question.")]), _Info()


def make_whisper(outcome, monkeypatch):
    provider = LocalWhisperTranscriptionProvider("base")
    model = _FakeWhisperModel(error=outcome if isinstance(outcome, Exception) else None)
    monkeypatch.setattr(provider, "_load", lambda: model)
    return provider


def make_openai_stt(outcome, monkeypatch):
    def handler(request: httpx2.Request) -> httpx2.Response:
        if isinstance(outcome, Exception):
            return httpx2.Response(400, json={"error": {"message": "Invalid file format."}})
        return httpx2.Response(
            200, json={"text": "Hello there. Pricing question.", "language": "en", "duration": 4.2}
        )

    client = httpx2.Client(transport=httpx2.MockTransport(handler))
    return OpenAITranscriptionProvider("whisper-1", "sk-test", None, 5, http_client=client)


STT_PROVIDERS = [make_whisper, make_openai_stt]
STT_IDS = ["whisper_local", "openai"]


@pytest.fixture
def audio(tmp_path: Path) -> AudioSource:
    path = tmp_path / "source.wav"
    path.write_bytes(b"RIFF-fake")
    return AudioSource(path=path, content_type="audio/wav", size_bytes=9)


@pytest.mark.parametrize("make", STT_PROVIDERS, ids=STT_IDS)
def test_stt_returns_text_language_duration_and_identity(make, audio, monkeypatch):
    provider = make("ok", monkeypatch)

    result = provider.transcribe(audio)

    assert result.text == "Hello there. Pricing question."
    assert result.language == "en"
    assert result.duration_seconds == 4.2
    assert (result.provider, result.model) == (provider.name, provider.model)


@pytest.mark.parametrize("make", STT_PROVIDERS, ids=STT_IDS)
def test_stt_undecodable_audio_is_a_permanent_unsupported_audio_error(make, audio, monkeypatch):
    provider = make(av.error.InvalidDataError(1, "Invalid data"), monkeypatch)

    with pytest.raises(ProcessingError) as raised:
        provider.transcribe(audio)

    assert (raised.value.retryable, raised.value.code) == (False, ErrorCode.UNSUPPORTED_AUDIO)


def test_cloud_stt_rejects_files_over_its_limit_without_calling_out(monkeypatch, tmp_path):
    provider = make_openai_stt(AssertionError("must not be called"), monkeypatch)
    big = AudioSource(path=tmp_path / "x.wav", content_type="audio/wav", size_bytes=26 * 2**20)

    with pytest.raises(ProcessingError) as raised:
        provider.transcribe(big)

    assert raised.value.code == ErrorCode.UNSUPPORTED_AUDIO


def test_missing_api_key_is_a_configuration_error():
    with pytest.raises(ProcessingError) as raised:
        OpenAICompatibleLLMProvider("gpt-4o-mini", "", None, 5)

    assert (raised.value.retryable, raised.value.code) == (
        False,
        ErrorCode.PROVIDER_CONFIGURATION_ERROR,
    )
