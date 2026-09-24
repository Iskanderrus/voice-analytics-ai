import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from django.utils import timezone

from apps.analyses import pipeline, tasks
from apps.analyses.models import JobStatus

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


def test_same_delivery_can_reclaim_after_worker_redelivery(make_job, transcriber):
    job = make_job(
        S.TRANSCRIBING,
        stage_attempts=1,
        stage_claim_id="same-delivery",
        stage_claimed_at=timezone.now(),
    )

    tasks.transcribe_audio.apply(args=(str(job.pk),), task_id="same-delivery")

    job.refresh_from_db()
    assert transcriber.calls == 1
    assert job.status == S.ANALYSING_STANDARD
