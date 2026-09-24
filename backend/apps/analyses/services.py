import logging
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from kombu.exceptions import OperationalError as BrokerError

from apps.analyses import state
from apps.analyses.models import AnalysisJob, JobStatus
from apps.analyses.tasks import enqueue_for_status
from apps.common.errors import DomainError, ErrorCode
from apps.common.logging import log_context
from apps.uploads.models import AudioUpload, UploadStatus

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AnalysisRequestOutcome:
    job: AnalysisJob
    created: bool


def create_analysis(upload: AudioUpload, profile: str) -> AnalysisRequestOutcome:
    """Commit the job before publishing so a worker never receives an unknown id."""
    if upload.status != UploadStatus.UPLOADED:
        raise DomainError(
            ErrorCode.UPLOAD_INCOMPLETE,
            "The upload has not been completed yet.",
            status_code=409,
        )

    existing = _live_job(upload, profile)
    if existing is not None:
        return AnalysisRequestOutcome(existing, created=False)
    try:
        with transaction.atomic():
            job = AnalysisJob.objects.create(audio_upload=upload, analysis_profile=profile)
    except IntegrityError:
        existing = _live_job(upload, profile)
        if existing is None:
            raise
        return AnalysisRequestOutcome(existing, created=False)

    with log_context(job_id=str(job.id), upload_id=str(upload.id)):
        try:
            enqueue_for_status(str(job.id), JobStatus.CREATED)
        except BrokerError:
            logger.exception("could not publish job; left in CREATED for the recovery sweep")
            return AnalysisRequestOutcome(job, created=True)
        AnalysisJob.objects.filter(pk=job.pk, status=JobStatus.CREATED).update(
            status=JobStatus.QUEUED, updated_at=timezone.now()
        )
        job.refresh_from_db()
        logger.info("analysis queued", extra={"profile": profile})
    return AnalysisRequestOutcome(job, created=True)


def _live_job(upload: AudioUpload, profile: str) -> AnalysisJob | None:
    return (
        AnalysisJob.objects.filter(audio_upload=upload, analysis_profile=profile)
        .exclude(status=JobStatus.FAILED)
        .first()
    )


def delete_analysis(job: AnalysisJob) -> None:
    with transaction.atomic():
        AnalysisJob.objects.filter(pk=job.pk).delete()
    logger.info("analysis deleted", extra={"job_id": str(job.id)})


def requeue_stalled_jobs(older_than: timedelta | None = None) -> list[str]:
    """Republish durable work only after both its activity window and execution lease expire."""
    now = timezone.now()
    cutoff = now - (older_than or timedelta(seconds=settings.PIPELINE_STALL_SECONDS))
    lease_cutoff = now - timedelta(seconds=settings.PIPELINE_STAGE_LEASE_SECONDS)
    stalled = (
        AnalysisJob.objects.exclude(status__in=state.TERMINAL)
        .filter(updated_at__lt=cutoff)
        .filter(Q(stage_claimed_at__isnull=True) | Q(stage_claimed_at__lte=lease_cutoff))
    )

    requeued = []
    for job_id, status in stalled.values_list("id", "status"):
        with log_context(job_id=str(job_id)):
            enqueue_for_status(str(job_id), JobStatus(status))
            logger.warning("requeued stalled job", extra={"job_status": status})
        requeued.append(str(job_id))
    return requeued
