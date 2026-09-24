"""Provider selection from settings. Adding a provider means one class + one branch here."""

from functools import cache

from django.conf import settings

from apps.analyses.providers.llm import (
    LLMProvider,
    OllamaLLMProvider,
    OpenAICompatibleLLMProvider,
)
from apps.analyses.providers.transcription import (
    LocalWhisperTranscriptionProvider,
    OpenAITranscriptionProvider,
    TranscriptionProvider,
)
from apps.common.errors import ErrorCode, permanent


@cache
def get_transcription_provider() -> TranscriptionProvider:
    match settings.STT_PROVIDER:
        case "whisper_local":
            return LocalWhisperTranscriptionProvider(
                settings.STT_MODEL, settings.WHISPER_DEVICE, settings.WHISPER_COMPUTE_TYPE
            )
        case "openai":
            return OpenAITranscriptionProvider(
                settings.STT_MODEL,
                settings.OPENAI_API_KEY,
                settings.OPENAI_BASE_URL,
                settings.STT_TIMEOUT_SECONDS,
            )
    raise permanent(
        ErrorCode.PROVIDER_CONFIGURATION_ERROR, f"Unknown STT_PROVIDER '{settings.STT_PROVIDER}'."
    )


@cache
def get_llm_provider() -> LLMProvider:
    match settings.LLM_PROVIDER:
        case "ollama":
            return OllamaLLMProvider(
                settings.LLM_MODEL,
                settings.OLLAMA_BASE_URL,
                settings.LLM_TIMEOUT_SECONDS,
                settings.LLM_TEMPERATURE,
            )
        case "openai":
            return OpenAICompatibleLLMProvider(
                settings.LLM_MODEL,
                settings.OPENAI_API_KEY,
                settings.OPENAI_BASE_URL,
                settings.LLM_TIMEOUT_SECONDS,
                settings.LLM_TEMPERATURE,
            )
    raise permanent(
        ErrorCode.PROVIDER_CONFIGURATION_ERROR, f"Unknown LLM_PROVIDER '{settings.LLM_PROVIDER}'."
    )
