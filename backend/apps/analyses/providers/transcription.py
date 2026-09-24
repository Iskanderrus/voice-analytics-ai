"""Speech-to-text boundary. The pipeline only sees `TranscriptionProvider`."""

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import httpx2
import openai

from apps.analyses.providers.openai_errors import openai_client, translate_openai_error
from apps.common.errors import ErrorCode, permanent

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AudioSource:
    path: Path
    content_type: str
    size_bytes: int


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    language: str | None
    duration_seconds: float | None
    provider: str
    model: str
    metadata: dict[str, Any] = field(default_factory=dict)


class TranscriptionProvider(Protocol):
    name: str
    model: str

    def transcribe(self, audio: AudioSource) -> TranscriptionResult:
        """Raises ProcessingError (retryable or permanent) on failure."""
        ...


class LocalWhisperTranscriptionProvider:
    """faster-whisper (CTranslate2) running in the worker process. No network.

    Model weights are resolved from the local Hugging Face cache; with
    HF_HUB_OFFLINE=1 a missing model is a configuration error, not a download.
    """

    name = "whisper_local"
    _models: dict[tuple[str, str, str], Any] = {}
    _lock = threading.Lock()

    def __init__(self, model: str, device: str = "cpu", compute_type: str = "int8"):
        self.model = model
        self.device = device
        self.compute_type = compute_type

    def _load(self) -> Any:
        from faster_whisper import WhisperModel

        key = (self.model, self.device, self.compute_type)
        with self._lock:
            if key not in self._models:
                try:
                    self._models[key] = WhisperModel(
                        self.model, device=self.device, compute_type=self.compute_type
                    )
                except (OSError, ValueError, RuntimeError) as exc:
                    # Retrying cannot repair missing weights, an invalid model
                    # name, or an unavailable device.
                    raise permanent(
                        ErrorCode.PROVIDER_CONFIGURATION_ERROR,
                        f"Whisper model '{self.model}' could not be loaded.",
                    ) from exc
            return self._models[key]

    def transcribe(self, audio: AudioSource) -> TranscriptionResult:
        import av

        model = self._load()
        try:
            segments, info = model.transcribe(str(audio.path), vad_filter=True, beam_size=5)
            # Iterate inside the provider boundary so decode-time failures are
            # translated consistently.
            texts = [segment.text.strip() for segment in segments]
        except av.error.FFmpegError as exc:
            raise permanent(ErrorCode.UNSUPPORTED_AUDIO, "Audio could not be decoded.") from exc
        return TranscriptionResult(
            text=" ".join(t for t in texts if t),
            language=info.language,
            duration_seconds=round(info.duration, 2),
            provider=self.name,
            model=self.model,
            metadata={
                "language_probability": round(info.language_probability, 3),
                "speech_duration_seconds": round(info.duration_after_vad, 2),
                "segments": len(texts),
                "device": self.device,
                "compute_type": self.compute_type,
            },
        )


# Fail locally before paying request latency for a payload the provider will reject.
OPENAI_STT_MAX_BYTES = 25 * 1024 * 1024


class OpenAITranscriptionProvider:
    """OpenAI (or compatible) `/audio/transcriptions`."""

    name = "openai"

    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: str | None,
        timeout: float,
        http_client: httpx2.Client | None = None,
    ):
        self.model = model
        self._client = openai_client(api_key, base_url, timeout, http_client)

    def transcribe(self, audio: AudioSource) -> TranscriptionResult:
        if audio.size_bytes > OPENAI_STT_MAX_BYTES:
            raise permanent(
                ErrorCode.UNSUPPORTED_AUDIO,
                "Audio exceeds the cloud transcription limit of 25 MB (chunking not enabled).",
            )
        try:
            with audio.path.open("rb") as file:
                # Request verbose metadata only where the provider supports it;
                # other models keep the same adapter contract.
                if self.model == "whisper-1":
                    response: Any = self._client.audio.transcriptions.create(
                        model=self.model, file=file, response_format="verbose_json"
                    )
                else:
                    response = self._client.audio.transcriptions.create(
                        model=self.model, file=file, response_format="json"
                    )
        except openai.OpenAIError as exc:
            raise translate_openai_error(
                exc,
                rate_limited=ErrorCode.TRANSCRIPTION_FAILED,
                timeout=ErrorCode.TRANSCRIPTION_TIMEOUT,
                failure=ErrorCode.TRANSCRIPTION_FAILED,
                bad_request=ErrorCode.UNSUPPORTED_AUDIO,
            ) from exc
        usage = getattr(response, "usage", None)
        return TranscriptionResult(
            text=response.text.strip(),
            language=getattr(response, "language", None),
            duration_seconds=getattr(response, "duration", None),
            provider=self.name,
            model=self.model,
            metadata={"usage": usage.model_dump() if usage is not None else None},
        )
