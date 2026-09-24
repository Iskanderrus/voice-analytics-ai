import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.analyses import pipeline, tasks
from apps.analyses.models import JobStatus
from apps.common.errors import ErrorCode

S = JobStatus

pytestmark = pytest.mark.django_db(transaction=True)


def test_concurrent_duplicate_claims_only_one_execution_lease(make_job, settings):
    settings.PIPELINE_MAX_STAGE_ATTEMPTS = 4
    job = make_job(S.QUEUED)
    barrier = threading.Barrier(2)

    def claim(delivery: str):
        barrier.wait()
        return pipeline.claim_stage(
            str(job.pk),
            S.TRANSCRIBING,
            delivery,
            frozenset({S.CREATED, S.QUEUED}),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(claim, ["delivery-a", "delivery-b"]))

    job.refresh_from_db()
    assert sum(item.job is not None for item in claims) == 1
    assert job.status == S.TRANSCRIBING
    assert job.stage_attempts == 1
    assert job.stage_claim_id in {"delivery-a", "delivery-b"}


def test_live_foreign_lease_does_not_consume_attempt_or_call_provider(
    make_job, transcriber, settings
):
    settings.PIPELINE_STAGE_LEASE_SECONDS = 1800
    job = make_job(
        S.TRANSCRIBING,
        stage_attempts=1,
        stage_claim_id="other-delivery",
        stage_claimed_at=timezone.now(),
    )

    tasks.transcribe_audio.apply(args=(str(job.pk),), task_id="duplicate-delivery")

    job.refresh_from_db()
    assert transcriber.calls == 0
    assert job.stage_attempts == 1
    assert job.stage_claim_id == "other-delivery"


def test_live_lease_rejects_reuse_of_the_same_claim_token(make_job, settings):
    settings.PIPELINE_STAGE_LEASE_SECONDS = 1800
    job = make_job(
        S.TRANSCRIBING,
        stage_attempts=1,
        stage_claim_id="same-token",
        stage_claimed_at=timezone.now(),
    )

    claim = pipeline.claim_stage(str(job.pk), S.TRANSCRIBING, "same-token")

    job.refresh_from_db()
    assert claim.job is None
    assert job.stage_attempts == 1
    assert job.stage_claim_id == "same-token"


def test_each_task_execution_gets_a_fresh_claim_token():
    observed: list[str] = []

    class Request:
        id = "same-celery-task-id"
        retries = 0

    class FakeTask:
        request = Request()

    def stage(_job_id: str, claim_id: str):
        observed.append(claim_id)

    fake = FakeTask()
    tasks._run_stage(fake, "job", "test", stage, ErrorCode.INTERNAL_PROCESSING_ERROR)
    tasks._run_stage(fake, "job", "test", stage, ErrorCode.INTERNAL_PROCESSING_ERROR)

    assert len(observed) == 2
    assert observed[0] != observed[1]
    assert "same-celery-task-id" not in observed


def test_expired_lease_can_be_reclaimed(make_job, transcriber, settings):
    settings.PIPELINE_STAGE_LEASE_SECONDS = 60
    job = make_job(
        S.TRANSCRIBING,
        stage_attempts=1,
        stage_claim_id="lost-worker",
        stage_claimed_at=timezone.now() - timedelta(seconds=61),
    )

    tasks.transcribe_audio.apply(args=(str(job.pk),), task_id="redelivered-task")

    job.refresh_from_db()
    assert transcriber.calls == 1
    assert job.status == S.ANALYSING_STANDARD


def test_stale_execution_cannot_release_or_fail_new_owner(make_job):
    job = make_job(
        S.TRANSCRIBING,
        stage_attempts=2,
        stage_claim_id="new-owner",
        stage_claimed_at=timezone.now(),
    )

    assert pipeline.release_stage_claim(str(job.pk), "old-owner") is False
    assert (
        pipeline.fail_job(
            str(job.pk),
            "old-owner",
            ErrorCode.INTERNAL_PROCESSING_ERROR,
            "stale worker failed",
        )
        is False
    )

    job.refresh_from_db()
    assert job.status == S.TRANSCRIBING
    assert job.stage_attempts == 2
    assert job.stage_claim_id == "new-owner"
