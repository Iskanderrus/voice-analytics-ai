import uuid

from django.conf import settings
from django.db import models


class UploadStatus(models.TextChoices):
    PENDING_UPLOAD = "PENDING_UPLOAD"
    UPLOADED = "UPLOADED"
    DELETED = "DELETED"


class AudioUpload(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="audio_uploads"
    )
    # Clients can write only the staging key. Processing always reads the finalized key.
    object_key = models.CharField(max_length=512, unique=True)
    staging_object_key = models.CharField(max_length=512, unique=True)
    original_filename = models.CharField(max_length=255)
    content_type = models.CharField(max_length=100)
    declared_size = models.BigIntegerField()
    stored_size = models.BigIntegerField(null=True, blank=True)
    checksum = models.CharField(max_length=100, blank=True)
    status = models.CharField(
        max_length=20, choices=UploadStatus.choices, default=UploadStatus.PENDING_UPLOAD
    )
    created_at = models.DateTimeField(auto_now_add=True)
    upload_url_expires_at = models.DateTimeField()
    uploaded_at = models.DateTimeField(null=True, blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    object_deleted_at = models.DateTimeField(null=True, blank=True)
    staging_deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["owner", "-created_at"]),
            models.Index(fields=["status", "created_at"], name="upload_status_created_idx"),
            models.Index(
                fields=["status"],
                name="upload_pending_object_cleanup",
                condition=models.Q(status="DELETED", object_deleted_at__isnull=True),
            ),
        ]

    def __str__(self) -> str:
        return f"{self.original_filename} ({self.status})"
