import logging
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.conf import settings
from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone
from kombu.exceptions import OperationalError as BrokerError

from apps.common.errors import DomainError, ErrorCode
from apps.uploads.models import AudioUpload, UploadStatus
from apps.uploads.storage import (
    ObjectChanged,
    ObjectStorage,
    PresignedPost,
    StorageUnavailable,
    get_storage,
)

logger = logging.getLogger(__name__)

ALLOWED_CONTENT_TYPES: dict[str, str] = {
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/m4a": ".m4a",
    "audio/aac": ".aac",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/wave": ".wav",
    "audio/webm": ".webm",
    "audio/ogg": ".ogg",
    "audio/flac": ".flac",
}


@dataclass(frozen=True)
class CreatedUpload:
    upload: AudioUpload
    presigned: PresignedPost


def _clean_filename(filename: str) -> str:
    name = filename.replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(ch for ch in name if unicodedata.category(ch)[0] != "C").strip()
    if not name:
        raise DomainError(ErrorCode.VALIDATION_ERROR, "Filename is empty.")
    return name[:255]


def create_upload(owner: User, filename: str, content_type: str, file_size: int) -> CreatedUpload:
    content_type = content_type.lower().split(";")[0].strip()
    extension = ALLOWED_CONTENT_TYPES.get(content_type)
    if extension is None:
        raise DomainError(
            ErrorCode.UNSUPPORTED_AUDIO,
            f"Content type '{content_type}' is not supported.",
            status_code=415,
        )
    if file_size <= 0:
        raise DomainError(ErrorCode.VALIDATION_ERROR, "File size must be positive.")
    if file_size > settings.UPLOAD_MAX_BYTES:
        raise DomainError(
            ErrorCode.UPLOAD_TOO_LARGE,
            f"File size must not exceed {settings.UPLOAD_MAX_BYTES} bytes.",
            status_code=413,
        )

    upload_id = uuid.uuid4()
    final_key = f"audio/{owner.pk}/{upload_id}/source{extension}"
    staging_key = f"staging/{owner.pk}/{upload_id}/source{extension}"
    presigned = get_storage().presign_post(
        staging_key,
        content_type=content_type,
        exact_bytes=file_size,
        ttl_seconds=settings.UPLOAD_URL_TTL_SECONDS,
    )
    upload = AudioUpload.objects.create(
        id=upload_id,
        owner=owner,
        object_key=final_key,
        staging_object_key=staging_key,
        original_filename=_clean_filename(filename),
        content_type=content_type,
        declared_size=file_size,
        upload_url_expires_at=presigned.expires_at,
    )
    logger.info("upload created", extra={"upload_id": str(upload.id), "file_size": file_size})
    return CreatedUpload(upload=upload, presigned=presigned)


def finalization_lease_is_live(
    upload: AudioUpload,
    *,
    now: datetime | None = None,
) -> bool:
    if upload.finalization_claim_id is None or upload.finalization_claimed_at is None:
        return False
    current = now or timezone.now()
    cutoff = current - timedelta(seconds=settings.UPLOAD_FINALIZATION_LEASE_SECONDS)
    return upload.finalization_claimed_at > cutoff


def _claim_finalization(upload_id: uuid.UUID, claim_id: str) -> AudioUpload:
    now = timezone.now()
    with transaction.atomic():
        locked = AudioUpload.objects.select_for_update().get(pk=upload_id)
        if locked.status == UploadStatus.UPLOADED:
            return locked
        if locked.status == UploadStatus.DELETED:
            raise DomainError(ErrorCode.UPLOAD_NOT_FOUND, "Upload not found.", status_code=404)
        if locked.status == UploadStatus.FINALIZING and finalization_lease_is_live(locked, now=now):
            raise DomainError(
                ErrorCode.UPLOAD_INCOMPLETE,
                "Upload finalization is already in progress.",
                status_code=409,
            )
        if locked.status not in {UploadStatus.PENDING_UPLOAD, UploadStatus.FINALIZING}:
            raise DomainError(ErrorCode.UPLOAD_NOT_FOUND, "Upload not found.", status_code=404)

        locked.status = UploadStatus.FINALIZING
        locked.finalization_claim_id = claim_id
        locked.finalization_claimed_at = now
        locked.save(
            update_fields=[
                "status",
                "finalization_claim_id",
                "finalization_claimed_at",
            ]
        )
        return locked


def _renew_finalization_claim(upload_id: uuid.UUID, claim_id: str) -> bool:
    return (
        AudioUpload.objects.filter(
            pk=upload_id,
            status=UploadStatus.FINALIZING,
            finalization_claim_id=claim_id,
        ).update(finalization_claimed_at=timezone.now())
        == 1
    )


def _release_finalization_claim(upload_id: uuid.UUID, claim_id: str) -> None:
    with transaction.atomic():
        locked = AudioUpload.objects.select_for_update().filter(pk=upload_id).first()
        if locked is None or locked.finalization_claim_id != claim_id:
            return
        fields = ["finalization_claim_id", "finalization_claimed_at"]
        locked.finalization_claim_id = None
        locked.finalization_claimed_at = None
        if locked.status == UploadStatus.FINALIZING:
            locked.status = UploadStatus.PENDING_UPLOAD
            fields.append("status")
        locked.save(update_fields=fields)


def _raise_finalization_lost(upload_id: uuid.UUID) -> None:
    current = AudioUpload.objects.filter(pk=upload_id).first()
    if current is None or current.status == UploadStatus.DELETED:
        raise DomainError(ErrorCode.UPLOAD_NOT_FOUND, "Upload not found.", status_code=404)
    raise DomainError(
        ErrorCode.UPLOAD_INCOMPLETE,
        "Upload finalization ownership changed; retry the request.",
        status_code=409,
    )


def _discard_finalized_after_delete(
    upload_id: uuid.UUID,
    object_key: str,
    staging_object_key: str,
    storage: ObjectStorage,
) -> None:
    try:
        storage.delete(object_key)
        storage.delete(staging_object_key)
    except StorageUnavailable:
        logger.exception(
            "late finalized object could not be removed; maintenance will retry",
            extra={"upload_id": str(upload_id)},
        )
        return
    AudioUpload.objects.filter(
        pk=upload_id,
        status=UploadStatus.DELETED,
        finalization_claim_id__isnull=True,
    ).update(object_deleted_at=timezone.now())


def complete_upload(upload: AudioUpload) -> AudioUpload:
    """Finalize one immutable storage snapshot under a durable ownership lease."""
    claim_id = str(uuid.uuid4())
    claimed = _claim_finalization(upload.pk, claim_id)
    if claimed.status == UploadStatus.UPLOADED:
        return claimed

    storage = get_storage()
    try:
        info = storage.head(claimed.staging_object_key)
    except StorageUnavailable as exc:
        _release_finalization_claim(claimed.pk, claim_id)
        raise DomainError(
            ErrorCode.STORAGE_ERROR, "Storage is temporarily unavailable.", status_code=503
        ) from exc
    if info is None:
        _release_finalization_claim(claimed.pk, claim_id)
        raise DomainError(
            ErrorCode.UPLOAD_INCOMPLETE,
            "The file has not been uploaded to storage yet.",
            status_code=409,
        )
    if info.size != claimed.declared_size or info.content_type != claimed.content_type:
        _release_finalization_claim(claimed.pk, claim_id)
        logger.warning(
            "uploaded object does not match declaration",
            extra={
                "upload_id": str(claimed.id),
                "declared_size": claimed.declared_size,
                "stored_size": info.size,
            },
        )
        raise DomainError(
            ErrorCode.UPLOAD_OBJECT_MISMATCH,
            "Uploaded file does not match the declared size or content type.",
            status_code=422,
        )

    # Refreshing immediately before COPY fences deletion/expiry that wins the row race.
    if not _renew_finalization_claim(claimed.pk, claim_id):
        _raise_finalization_lost(claimed.pk)

    try:
        finalized = storage.copy_if_match(
            claimed.staging_object_key,
            claimed.object_key,
            info.etag,
        )
    except ObjectChanged as exc:
        _release_finalization_claim(claimed.pk, claim_id)
        raise DomainError(
            ErrorCode.UPLOAD_OBJECT_MISMATCH,
            "Uploaded file changed while it was being finalized. Upload it again.",
            status_code=422,
        ) from exc
    except StorageUnavailable as exc:
        # COPY may have reached storage even when the response is lost. Keep the lease
        # until expiry so another finalizer cannot overlap that ambiguous operation.
        raise DomainError(
            ErrorCode.STORAGE_ERROR, "Storage is temporarily unavailable.", status_code=503
        ) from exc

    if finalized.size != claimed.declared_size or finalized.content_type != claimed.content_type:
        try:
            storage.delete(claimed.object_key)
        finally:
            _release_finalization_claim(claimed.pk, claim_id)
        raise DomainError(
            ErrorCode.UPLOAD_OBJECT_MISMATCH,
            "Finalized file does not match the declared size or content type.",
            status_code=422,
        )

    deleted = False
    with transaction.atomic():
        locked = AudioUpload.objects.select_for_update().get(pk=claimed.pk)
        if locked.status == UploadStatus.FINALIZING and locked.finalization_claim_id == claim_id:
            locked.status = UploadStatus.UPLOADED
            locked.stored_size = finalized.size
            locked.checksum = finalized.etag
            locked.uploaded_at = timezone.now()
            locked.finalization_claim_id = None
            locked.finalization_claimed_at = None
            locked.save(
                update_fields=[
                    "status",
                    "stored_size",
                    "checksum",
                    "uploaded_at",
                    "finalization_claim_id",
                    "finalization_claimed_at",
                ]
            )
        elif locked.status == UploadStatus.DELETED:
            if locked.finalization_claim_id == claim_id:
                locked.finalization_claim_id = None
                locked.finalization_claimed_at = None
                locked.save(
                    update_fields=[
                        "finalization_claim_id",
                        "finalization_claimed_at",
                    ]
                )
            deleted = True
        elif locked.status == UploadStatus.UPLOADED:
            return locked
        else:
            _raise_finalization_lost(locked.pk)

    if deleted:
        _discard_finalized_after_delete(
            claimed.pk,
            claimed.object_key,
            claimed.staging_object_key,
            storage,
        )
        raise DomainError(ErrorCode.UPLOAD_NOT_FOUND, "Upload not found.", status_code=404)

    logger.info("upload completed", extra={"upload_id": str(claimed.id)})
    return locked


def _publish_delete_after_commit(upload_id: str) -> None:
    from apps.uploads.tasks import delete_upload_object

    try:
        delete_upload_object.delay(upload_id)
    except BrokerError:
        # The DB state is already committed. Periodic maintenance will republish cleanup.
        logger.exception(
            "could not publish object cleanup; deletion remains committed",
            extra={"upload_id": upload_id},
        )


def delete_upload(upload: AudioUpload) -> None:
    """Commit customer-data deletion before best-effort storage cleanup publication."""
    from apps.analyses.models import AnalysisJob

    with transaction.atomic():
        locked = AudioUpload.objects.select_for_update().get(pk=upload.pk)
        if locked.status == UploadStatus.DELETED:
            return
        AnalysisJob.objects.filter(audio_upload=locked).delete()
        was_finalizing = locked.status == UploadStatus.FINALIZING
        locked.status = UploadStatus.DELETED
        locked.deleted_at = timezone.now()
        fields = ["status", "deleted_at"]
        if not was_finalizing:
            locked.finalization_claim_id = None
            locked.finalization_claimed_at = None
            fields += ["finalization_claim_id", "finalization_claimed_at"]
        locked.save(update_fields=fields)
        transaction.on_commit(lambda: _publish_delete_after_commit(str(locked.pk)))
    logger.info("upload deleted", extra={"upload_id": str(upload.id)})
