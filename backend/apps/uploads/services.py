import logging
import unicodedata
import uuid
from dataclasses import dataclass

from django.conf import settings
from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone
from kombu.exceptions import OperationalError as BrokerError

from apps.common.errors import DomainError, ErrorCode
from apps.uploads.models import AudioUpload, UploadStatus
from apps.uploads.storage import (
    ObjectChanged,
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


def complete_upload(upload: AudioUpload) -> AudioUpload:
    """Freeze the verified staging object into a server-only key."""
    if upload.status == UploadStatus.UPLOADED:
        return upload
    if upload.status != UploadStatus.PENDING_UPLOAD:
        raise DomainError(ErrorCode.UPLOAD_NOT_FOUND, "Upload not found.", status_code=404)

    storage = get_storage()
    try:
        info = storage.head(upload.staging_object_key)
    except StorageUnavailable as exc:
        raise DomainError(
            ErrorCode.STORAGE_ERROR, "Storage is temporarily unavailable.", status_code=503
        ) from exc
    if info is None:
        raise DomainError(
            ErrorCode.UPLOAD_INCOMPLETE,
            "The file has not been uploaded to storage yet.",
            status_code=409,
        )
    if info.size != upload.declared_size or info.content_type != upload.content_type:
        logger.warning(
            "uploaded object does not match declaration",
            extra={
                "upload_id": str(upload.id),
                "declared_size": upload.declared_size,
                "stored_size": info.size,
            },
        )
        raise DomainError(
            ErrorCode.UPLOAD_OBJECT_MISMATCH,
            "Uploaded file does not match the declared size or content type.",
            status_code=422,
        )

    try:
        finalized = storage.copy_if_match(upload.staging_object_key, upload.object_key, info.etag)
    except ObjectChanged as exc:
        raise DomainError(
            ErrorCode.UPLOAD_OBJECT_MISMATCH,
            "Uploaded file changed while it was being finalized. Upload it again.",
            status_code=422,
        ) from exc
    except StorageUnavailable as exc:
        raise DomainError(
            ErrorCode.STORAGE_ERROR, "Storage is temporarily unavailable.", status_code=503
        ) from exc

    if finalized.size != upload.declared_size or finalized.content_type != upload.content_type:
        raise DomainError(
            ErrorCode.UPLOAD_OBJECT_MISMATCH,
            "Finalized file does not match the declared size or content type.",
            status_code=422,
        )

    with transaction.atomic():
        locked = AudioUpload.objects.select_for_update().get(pk=upload.pk)
        if locked.status == UploadStatus.UPLOADED:
            return locked
        if locked.status != UploadStatus.PENDING_UPLOAD:
            raise DomainError(ErrorCode.UPLOAD_NOT_FOUND, "Upload not found.", status_code=404)
        locked.status = UploadStatus.UPLOADED
        locked.stored_size = finalized.size
        locked.checksum = finalized.etag
        locked.uploaded_at = timezone.now()
        locked.save(update_fields=["status", "stored_size", "checksum", "uploaded_at"])

    logger.info("upload completed", extra={"upload_id": str(upload.id)})
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
        locked.status = UploadStatus.DELETED
        locked.deleted_at = timezone.now()
        locked.save(update_fields=["status", "deleted_at"])
        transaction.on_commit(lambda: _publish_delete_after_commit(str(locked.pk)))
    logger.info("upload deleted", extra={"upload_id": str(upload.id)})
