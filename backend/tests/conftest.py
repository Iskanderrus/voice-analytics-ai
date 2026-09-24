"""Shared fakes keep provider and broker behaviour deterministic in unit tests."""

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import boto3
import pytest
from celery import Task
from django.conf import settings
from django.utils import timezone
from moto import mock_aws
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.analyses import pipeline
from apps.analyses.models import AnalysisJob, JobStatus
from apps.analyses.providers.llm import StructuredAnalysisRequest, StructuredAnalysisResponse
from apps.analyses.providers.transcription import AudioSource, TranscriptionResult
from apps.uploads.models import AudioUpload, UploadStatus
from apps.uploads.storage import get_storage

AUDIO_BYTES = b"RIFF....WAVEfmt fake-audio-bytes"


@pytest.fixture(autouse=True)
def s3() -> Iterator[Any]:
    with mock_aws():
        get_storage.cache_clear()
        client = boto3.client("s3", region_name=settings.S3_REGION)
        client.create_bucket(Bucket=settings.S3_BUCKET)
        yield client
        get_storage.cache_clear()


@dataclass
class CeleryQueue:
    sent: list[tuple[Task, tuple[Any, ...]]] = field(default_factory=list)

    def names(self) -> list[str]:
        return [task.name.rsplit(".", 1)[-1] for task, _ in self.sent]

    def drain(self, limit: int = 20) -> None:
        for _ in range(limit):
            if not self.sent:
                return
            task, args = self.sent.pop(0)
            task.apply(args=args)
        raise AssertionError("queue did not drain; task loop?")


@pytest.fixture(autouse=True)
def celery_queue(monkeypatch: pytest.MonkeyPatch) -> CeleryQueue:
    queue = CeleryQueue()

    def apply_async(self: Task, args: tuple[Any, ...] = (), kwargs: Any = None, **_: Any) -> None:
        queue.sent.append((self, tuple(args or ())))

    monkeypatch.setattr(Task, "apply_async", apply_async)
    return queue


@pytest.fixture
def user(django_user_model: Any) -> Any:
    return django_user_model.objects.create_user(username="alice", password="x")


@pytest.fixture
def other_user(django_user_model: Any) -> Any:
    return django_user_model.objects.create_user(username="mallory", password="x")


def _client_for(user: Any) -> APIClient:
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Token {Token.objects.create(user=user).key}")
    return client


@pytest.fixture
def api(user: Any) -> APIClient:
    return _client_for(user)


@pytest.fixture
def other_api(other_user: Any) -> APIClient:
    return _client_for(other_user)


@pytest.fixture
def uploaded(user: Any, s3: Any) -> AudioUpload:
    upload = AudioUpload.objects.create(
        owner=user,
        object_key=f"audio/{user.pk}/test/source.wav",
        staging_object_key=f"staging/{user.pk}/test/source.wav",
        original_filename="call.wav",
        content_type="audio/wav",
        declared_size=len(AUDIO_BYTES),
        stored_size=len(AUDIO_BYTES),
        status=UploadStatus.UPLOADED,
        upload_url_expires_at=timezone.now() + timedelta(minutes=15),
    )
    s3.put_object(
        Bucket=settings.S3_BUCKET,
        Key=upload.object_key,
        Body=AUDIO_BYTES,
        ContentType="audio/wav",
    )
    return upload


@pytest.fixture
def make_job(uploaded: AudioUpload) -> Callable[..., AnalysisJob]:
    def make(status: JobStatus = JobStatus.QUEUED, **fields: Any) -> AnalysisJob:
        return AnalysisJob.objects.create(audio_upload=uploaded, status=status, **fields)

    return make


Outcome = str | Exception


@dataclass
class FakeTranscriber:
    outcomes: list[Outcome] = field(default_factory=lambda: ["Hello, let us talk about pricing."])
    name: str = "fake_stt"
    model: str = "fake-stt-1"
    calls: int = 0
    on_call: Callable[[], None] | None = None

    def transcribe(self, audio: AudioSource) -> TranscriptionResult:
        self.calls += 1
        assert audio.path.read_bytes() == AUDIO_BYTES
        if self.on_call:
            self.on_call()
        outcome = self.outcomes[min(self.calls, len(self.outcomes)) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        return TranscriptionResult(
            text=outcome,
            language="en",
            duration_seconds=12.5,
            provider=self.name,
            model=self.model,
            metadata={"segments": 1},
        )


@dataclass
class FakeLLM:
    outcomes: list[Outcome] = field(default_factory=list)
    name: str = "fake_llm"
    model: str = "fake-llm-1"
    requests: list[StructuredAnalysisRequest] = field(default_factory=list)

    def generate_structured(self, request: StructuredAnalysisRequest) -> StructuredAnalysisResponse:
        self.requests.append(request)
        outcome = self.outcomes[min(len(self.requests), len(self.outcomes)) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        return StructuredAnalysisResponse(
            content=outcome,
            provider=self.name,
            model=self.model,
            input_tokens=100,
            output_tokens=40,
        )


@pytest.fixture
def transcriber(monkeypatch: pytest.MonkeyPatch) -> FakeTranscriber:
    fake = FakeTranscriber()
    monkeypatch.setattr(pipeline, "get_transcription_provider", lambda: fake)
    return fake


@pytest.fixture
def llm(monkeypatch: pytest.MonkeyPatch) -> FakeLLM:
    fake = FakeLLM()
    monkeypatch.setattr(pipeline, "get_llm_provider", lambda: fake)
    return fake
