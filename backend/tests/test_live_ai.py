"""Real local models, no fakes: faster-whisper on the fixture audio, and a real
Ollama model through the full prompt -> provider -> Pydantic validation path.

    LIVE_AI=1 OLLAMA_BASE_URL=http://localhost:11434 LLM_MODEL=qwen2.5:3b-instruct \
        uv run pytest tests/test_live_ai.py

Skipped by default (CI has no models).
"""

import os
from pathlib import Path

import pytest
from django.conf import settings

from apps.analyses.pipeline import generate_validated
from apps.analyses.providers.llm import OllamaLLMProvider
from apps.analyses.providers.transcription import AudioSource, LocalWhisperTranscriptionProvider
from apps.analyses.schemas import StandardAnalysis
from apps.prompts.builder import build_standard_prompt

FIXTURE = Path(__file__).resolve().parents[2] / "scripts" / "fixtures" / "sales_call.m4a"

pytestmark = [
    pytest.mark.live_ai,
    pytest.mark.skipif(os.environ.get("LIVE_AI") != "1", reason="set LIVE_AI=1"),
]


def test_local_whisper_transcribes_the_fixture():
    provider = LocalWhisperTranscriptionProvider(settings.STT_MODEL)

    result = provider.transcribe(
        AudioSource(path=FIXTURE, content_type="audio/mp4", size_bytes=FIXTURE.stat().st_size)
    )

    assert result.language == "en"
    assert 40 < result.duration_seconds < 47
    text = result.text.lower()
    assert "discount" in text
    assert "contract" in text


@pytest.mark.django_db
def test_ollama_output_validates_against_the_standard_schema(monkeypatch):
    provider = OllamaLLMProvider(settings.LLM_MODEL, settings.OLLAMA_BASE_URL, 300)
    monkeypatch.setattr("apps.analyses.pipeline.get_llm_provider", lambda: provider)
    transcript = (
        "Hi Daniel, I wanted to follow up on the pricing proposal. The price is higher than what "
        "we pay Acme today. If you sign a two year contract we can offer a fifteen percent "
        "discount. I will send an updated quote by Friday. Let us schedule a call next Tuesday."
    )

    validated = generate_validated(build_standard_prompt(transcript))

    analysis = validated.output
    assert isinstance(analysis, StandardAnalysis)
    assert analysis.summary
    assert validated.usage["input_tokens"] > 0
    assert validated.provider == "ollama"
