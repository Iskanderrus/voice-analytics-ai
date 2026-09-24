import logging
from datetime import timedelta

from celery import Task, shared_task
from django.conf import settings
from django.utils import timezone
from kombu.exceptions import OperationalError as BrokerError

from apps.common.logging import log_context
from apps.uploads.models import AudioUpload, UploadStatus
from apps.uploads.storage import StorageUnavailable, get_storage

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    autoretry_for=(StorageUnavailable,),
    retry_backoff=10,
    retry_backoff_max=600,
    max_retries=8,
)
def delete_upload_object(self: Task, upload_id: str) -> None:
    with log_context(upload_id=upload_id, stage="object_cleanup", attempt=self.request.retries):
        upload = AudioUpload.objects.filter(pk=upload_id, status=UploadStatus.DELETED).first()
        if upload is None:
            return

        storage = get_storage()
        if upload.object_deleted_at is None:
            storage.delete(upload.object_key)
            AudioUpload.objects.filter(pk=upload.pk, object_deleted_at__isnull=True).update(
                object_deleted_at=timezone.now()
            )

        # A still-valid POST can recreate staging after deletion. Remove it now for privacy,
        # but only mark cleanup final once that POST has expired.
        storage.delete(upload.staging_object_key)
        if upload.upload_url_expires_at <= timezone.now():
            AudioUpload.objects.filter(pk=upload.pk, staging_deleted_at__isnull=True).update(
                staging_deleted_at=timezone.now()
            )
        logger.info("storage objects deleted")


@shared_task(
    bind=True,
    autoretry_for=(StorageUnavailable,),
    retry_backoff=10,
    retry_backoff_max=600,
    max_retries=8,
)
def delete_staging_object(self: Task, upload_id: str) -> None:
    with log_context(upload_id=upload_id, stage="staging_cleanup", attempt=self.request.retries):
        upload = AudioUpload.objects.filter(pk=upload_id, staging_deleted_at__isnull=True).first()
        if upload is None or upload.upload_url_expires_at > timezone.now():
            return
        get_storage().delete(upload.staging_object_key)
        AudioUpload.objects.filter(pk=upload.pk, staging_deleted_at__isnull=True).update(
            staging_deleted_at=timezone.now()
        )
        logger.info("expired staging object deleted")


@shared_task(
    autoretry_for=(BrokerError,),
    retry_backoff=30,
    retry_backoff_max=300,
    max_retries=5,
)
def maintain_uploads() -> None:
    """Expire abandoned upload slots and republish idempotent storage cleanup."""
    now = timezone.now()
    stale_cutoff = now - timedelta(seconds=settings.UPLOAD_PENDING_TTL_SECONDS)

    AudioUpload.objects.filter(
        status=UploadStatus.PENDING_UPLOAD,
        created_at__lt=stale_cutoff,
    ).update(status=UploadStatus.DELETED, deleted_at=now)

    deleted_ids = list(
        AudioUpload.objects.filter(
            status=UploadStatus.DELETED,
            object_deleted_at__isnull=True,
        ).values_list("id", flat=True)[: settings.UPLOAD_MAINTENANCE_BATCH_SIZE]
    )
    staging_ids = list(
        AudioUpload.objects.filter(
            staging_deleted_at__isnull=True,
            upload_url_expires_at__lte=now,
        ).values_list("id", flat=True)[: settings.UPLOAD_MAINTENANCE_BATCH_SIZE]
    )

    for upload_id in deleted_ids:
        delete_upload_object.delay(str(upload_id))
    for upload_id in staging_ids:
        delete_staging_object.delay(str(upload_id))
