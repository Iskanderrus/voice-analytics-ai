import base64
import json
import re
from datetime import timedelta

import pytest
from botocore.exceptions import ClientError
from django.conf import settings
from django.utils import timezone
from kombu.exceptions import OperationalError as BrokerError

from apps.analyses.models import AnalysisJob, JobStatus
from apps.uploads.models import AudioUpload, UploadStatus
from apps.uploads.tasks import delete_upload_object, maintain_uploads

pytestmark = pytest.mark.django_db

AUDIO = b"x" * 2048


def create(api, **overrides):
    body = {
        "filename": "../../etc/Call 1.m4a",
        "content_type": "audio/mp4",
        "file_size": len(AUDIO),
    }
    return api.post("/api/v1/uploads", {**body, **overrides}, format="json")


def put_staging(s3, upload, body=AUDIO):
    s3.put_object(
        Bucket=settings.S3_BUCKET,
        Key=upload.staging_object_key,
        Body=body,
        ContentType=upload.content_type,
    )


def test_create_upload_issues_presigned_post_for_a_staging_key(api, user):
    response = create(api)

    assert response.status_code == 201
    body = response.json()
    upload = AudioUpload.objects.get(pk=body["upload_id"])
    assert body["object_key"] == upload.object_key
    assert re.fullmatch(rf"audio/{user.pk}/{upload.pk}/source\.m4a", upload.object_key)
    assert re.fullmatch(rf"staging/{user.pk}/{upload.pk}/source\.m4a", upload.staging_object_key)
    assert upload.original_filename == "Call 1.m4a"
    assert upload.status == UploadStatus.PENDING_UPLOAD
    assert body["upload_method"] == "POST"
    assert body["upload_fields"]["key"] == upload.staging_object_key
    assert body["upload_fields"]["key"] != upload.object_key
    assert body["upload_fields"]["Content-Type"] == "audio/mp4"
    policy = json.loads(base64.b64decode(body["upload_fields"]["policy"]))
    assert ["content-length-range", len(AUDIO), len(AUDIO)] in policy["conditions"]


def test_rejects_unsupported_content_type(api):
    response = create(api, content_type="application/x-sh")

    assert response.status_code == 415
    assert response.json()["error"]["code"] == "UNSUPPORTED_AUDIO"
    assert not AudioUpload.objects.exists()


def test_rejects_files_above_the_size_limit(api):
    response = create(api, file_size=settings.UPLOAD_MAX_BYTES + 1)

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "UPLOAD_TOO_LARGE"
    assert not AudioUpload.objects.exists()


def test_complete_freezes_verified_object_into_final_key(api, s3):
    upload_id = create(api).json()["upload_id"]
    upload = AudioUpload.objects.get(pk=upload_id)
    put_staging(s3, upload)

    response = api.post(f"/api/v1/uploads/{upload_id}/complete")

    assert response.status_code == 200
    assert response.json()["status"] == "UPLOADED"
    upload.refresh_from_db()
    assert upload.stored_size == len(AUDIO)
    assert upload.checksum
    assert upload.uploaded_at is not None
    assert s3.get_object(Bucket=settings.S3_BUCKET, Key=upload.object_key)["Body"].read() == AUDIO

    # Reusing the still-valid client write slot can only replace staging, not processing input.
    put_staging(s3, upload, b"y" * len(AUDIO))
    assert api.post(f"/api/v1/uploads/{upload_id}/complete").status_code == 200
    assert s3.get_object(Bucket=settings.S3_BUCKET, Key=upload.object_key)["Body"].read() == AUDIO


def test_complete_rejects_missing_object(api):
    upload_id = create(api).json()["upload_id"]

    response = api.post(f"/api/v1/uploads/{upload_id}/complete")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "UPLOAD_INCOMPLETE"
    assert AudioUpload.objects.get(pk=upload_id).status == UploadStatus.PENDING_UPLOAD


def test_complete_rejects_object_that_differs_from_declaration(api, s3):
    upload_id = create(api).json()["upload_id"]
    upload = AudioUpload.objects.get(pk=upload_id)
    put_staging(s3, upload, AUDIO * 2)

    response = api.post(f"/api/v1/uploads/{upload_id}/complete")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "UPLOAD_OBJECT_MISMATCH"


def test_uploads_are_private_to_their_owner(api, other_api, client):
    upload_id = create(api).json()["upload_id"]

    assert client.get(f"/api/v1/uploads/{upload_id}").status_code == 401
    for response in (
        other_api.get(f"/api/v1/uploads/{upload_id}"),
        other_api.post(f"/api/v1/uploads/{upload_id}/complete"),
        other_api.delete(f"/api/v1/uploads/{upload_id}"),
    ):
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "UPLOAD_NOT_FOUND"


def test_delete_removes_derived_data_then_the_object(
    api, uploaded, s3, celery_queue, django_capture_on_commit_callbacks
):
    job = AnalysisJob.objects.create(audio_upload=uploaded, status=JobStatus.COMPLETED)

    with django_capture_on_commit_callbacks(execute=True):
        response = api.delete(f"/api/v1/uploads/{uploaded.pk}")

    assert response.status_code == 204
    uploaded.refresh_from_db()
    assert uploaded.status == UploadStatus.DELETED
    assert not AnalysisJob.objects.filter(pk=job.pk).exists()
    assert celery_queue.names() == ["delete_upload_object"]

    celery_queue.drain()

    with pytest.raises(ClientError):
        s3.head_object(Bucket=settings.S3_BUCKET, Key=uploaded.object_key)
    uploaded.refresh_from_db()
    assert uploaded.object_deleted_at is not None
    assert api.get(f"/api/v1/uploads/{uploaded.pk}").status_code == 404


def test_delete_stays_successful_when_cleanup_publication_fails(
    api, uploaded, monkeypatch, django_capture_on_commit_callbacks
):
    def fail_publish(*args, **kwargs):
        raise BrokerError("redis unavailable")

    monkeypatch.setattr(delete_upload_object, "delay", fail_publish)

    with django_capture_on_commit_callbacks(execute=True):
        response = api.delete(f"/api/v1/uploads/{uploaded.pk}")

    assert response.status_code == 204
    uploaded.refresh_from_db()
    assert uploaded.status == UploadStatus.DELETED
    assert uploaded.object_deleted_at is None


def test_reusing_upload_slot_after_delete_cannot_recreate_final_object(
    api, s3, celery_queue, django_capture_on_commit_callbacks
):
    upload_id = create(api).json()["upload_id"]
    upload = AudioUpload.objects.get(pk=upload_id)
    put_staging(s3, upload)
    assert api.post(f"/api/v1/uploads/{upload_id}/complete").status_code == 200

    with django_capture_on_commit_callbacks(execute=True):
        assert api.delete(f"/api/v1/uploads/{upload_id}").status_code == 204
    celery_queue.drain()

    put_staging(s3, upload, b"z" * len(AUDIO))

    with pytest.raises(ClientError):
        s3.head_object(Bucket=settings.S3_BUCKET, Key=upload.object_key)
    assert api.get(f"/api/v1/uploads/{upload_id}").status_code == 404


def test_maintenance_expires_abandoned_staging_upload(api, s3, celery_queue):
    upload_id = create(api).json()["upload_id"]
    upload = AudioUpload.objects.get(pk=upload_id)
    put_staging(s3, upload)

    old = timezone.now() - timedelta(days=2)
    AudioUpload.objects.filter(pk=upload.pk).update(
        created_at=old,
        upload_url_expires_at=old,
    )

    maintain_uploads.apply()
    celery_queue.drain()

    upload.refresh_from_db()
    assert upload.status == UploadStatus.DELETED
    assert upload.object_deleted_at is not None
    assert upload.staging_deleted_at is not None
    with pytest.raises(ClientError):
        s3.head_object(Bucket=settings.S3_BUCKET, Key=upload.staging_object_key)
