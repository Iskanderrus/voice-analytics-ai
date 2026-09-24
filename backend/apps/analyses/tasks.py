"""Celery owns delivery and backoff; durable processing state stays in PostgreSQL."""

import logging
import random
import time
import uuid
from collections.abc import Callable

from celery import Task, shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings
from kombu.exceptions import OperationalError as BrokerError

from apps.analyses import pipeline
from apps.analyses.models import JobStatus
from apps.common.errors import ErrorCode, ProcessingError
from apps.common.logging import log_context

logger = logging.getLogger(__name__)

StageFn = Callable[[str, str], JobStatus | None]


def backoff_seconds(attempt: int) -> int:
    base = settings.PIPELINE_RETRY_BASE_SECONDS
    delay = min(base * 2 ** max(attempt - 1, 0), settings.PIPELINE_RETRY_MAX_SECONDS)
    return delay + random.randint(0, base)  # noqa: S311 - jitter, not crypto


def _run_stage(task: Task, job_id: str, stage: str, fn: StageFn, timeout_code: ErrorCode) -> None:
    claim_id = str(task.request.id or uuid.uuid4())
    with log_context(job_id=job_id, stage=stage, celery_retry=task.request.retries):
        started = time.monotonic()
        try:
            status = fn(job_id, claim_id)
        except ProcessingError as exc:
            attempts = pipeline.current_attempts(job_id)
            if exc.retryable and attempts < settings.PIPELINE_MAX_STAGE_ATTEMPTS:
                pipeline.release_stage_claim(job_id, claim_id)
                countdown = backoff_seconds(attempts)
                logger.warning(
                    "stage failed; retrying",
                    extra={"error_code": exc.code, "attempt": attempts, "countdown": countdown},
                )
                raise task.retry(exc=exc, countdown=countdown) from exc
            pipeline.fail_job(job_id, exc.code, exc.message)
            return
        except SoftTimeLimitExceeded:
            pipeline.fail_job(job_id, timeout_code, f"The {stage} stage exceeded its time limit.")
            return
        except Exception:
            logger.exception("unexpected stage error")
            pipeline.fail_job(
                job_id, ErrorCode.INTERNAL_PROCESSING_ERROR, "Unexpected processing error."
            )
            raise
        logger.info(
            "stage finished",
            extra={
                "job_status": status,
                "duration_ms": round((time.monotonic() - started) * 1000),
            },
        )
        enqueue_for_status(job_id, status)


def enqueue_for_status(job_id: str, status: JobStatus | None) -> None:
    """Enqueues the task that owns status. Stage claims absorb duplicate deliveries."""
    if status is None:
        return
    next_task = {
        JobStatus.CREATED: transcribe_audio,
        JobStatus.QUEUED: transcribe_audio,
        JobStatus.TRANSCRIBING: transcribe_audio,
        JobStatus.ANALYSING_STANDARD: run_standard_analysis,
        JobStatus.ANALYSING_CUSTOM: run_custom_analysis,
    }.get(status)
    if next_task is not None:
        next_task.delay(job_id)


_MAX_RETRIES = settings.PIPELINE_MAX_STAGE_ATTEMPTS
_LLM_SOFT_LIMIT = settings.LLM_TIMEOUT_SECONDS * settings.LLM_MAX_OUTPUT_ATTEMPTS + 30


@shared_task(
    bind=True,
    max_retries=_MAX_RETRIES,
    soft_time_limit=settings.STT_TIMEOUT_SECONDS,
    time_limit=settings.STT_TIMEOUT_SECONDS + 60,
)
def transcribe_audio(self: Task, job_id: str) -> None:
    _run_stage(self, job_id, "transcription", pipeline.transcribe, ErrorCode.TRANSCRIPTION_TIMEOUT)


@shared_task(
    bind=True,
    max_retries=_MAX_RETRIES,
    soft_time_limit=_LLM_SOFT_LIMIT,
    time_limit=_LLM_SOFT_LIMIT + 60,
)
def run_standard_analysis(self: Task, job_id: str) -> None:
    _run_stage(
        self,
        job_id,
        "standard_analysis",
        pipeline.run_standard_analysis,
        ErrorCode.LLM_PROVIDER_ERROR,
    )


@shared_task(
    bind=True,
    max_retries=_MAX_RETRIES,
    soft_time_limit=_LLM_SOFT_LIMIT,
    time_limit=_LLM_SOFT_LIMIT + 60,
)
def run_custom_analysis(self: Task, job_id: str) -> None:
    _run_stage(
        self, job_id, "custom_analysis", pipeline.run_custom_analysis, ErrorCode.LLM_PROVIDER_ERROR
    )


@shared_task(
    autoretry_for=(BrokerError,),
    retry_backoff=30,
    retry_backoff_max=300,
    max_retries=5,
)
def requeue_stalled_analysis_jobs() -> None:
    from apps.analyses.services import requeue_stalled_jobs

    requeue_stalled_jobs()
