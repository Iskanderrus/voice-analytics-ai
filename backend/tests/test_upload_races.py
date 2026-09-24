import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from botocore.exceptions import ClientError
from django.conf import settings
from django.db import close_old_connections
from django.utils import timezone

from apps.common.errors import DomainError, ErrorCode
from apps.uploads import services
from apps.uploads.models import AudioUpload, UploadStatus
from apps.uploads.storage import get_storage
from apps.uploads.tasks import maintain_uploads

pytestmark = pytest.mark.django_db(transaction=True)

AUDIO = b"race-audio" * 256


def make_pending_upload(user, s3) -> AudioUpload:
    upload_id = uuid.uuid4()
    upload = AudioUpload.objects.create(
        id=upload_id,
        owner=user,
        object_key=f"audio/{user.pk}/{upload_id}/source.wav",
        staging_object_key=f"staging/{user.pk}/{upload_id}/source.wav",
        original_filename="race.wav",
        content_type="audio/wav",
        declared_size=len(AUDIO),
        upload_url_expires_at=timezone.now() + timedelta(minutes=15),
    )
    s3.put_object(
        Bucket=settings.S3_BUCKET,
        Key=upload.staging_object_key,
        Body=AUDIO,
        ContentType=upload.content_type,
    )
    return upload


def in_thread(fn):
    close_old_connections()
    try:
        return fn()
    finally:
        close_old_connections()


def assert_missing(s3, key: str) -> None:
    with pytest.raises(ClientError):
        s3.head_object(Bucket=settings.S3_BUCKET, Key=key)


def test_complete_complete_allows_only_one_live_finalizer(user, s3, monkeypatch):
    upload = make_pending_upload(user, s3)
    storage = get_storage()
    original_copy = storage.copy_if_match
    copy_entered = threading.Event()
    release_copy = threading.Event()
    calls = 0
    calls_lock = threading.Lock()

    def blocking_copy(source_key: str, destination_key: str, etag: str):
        nonlocal calls
        with calls_lock:
            calls += 1
        copy_entered.set()
        assert release_copy.wait(timeout=10)
        return original_copy(source_key, destination_key, etag)

    monkeypatch.setattr(storage, "copy_if_match", blocking_copy)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            in_thread,
            lambda: services.complete_upload(AudioUpload.objects.get(pk=upload.pk)),
        )
        assert copy_entered.wait(timeout=10)
        second = pool.submit(
            in_thread,
            lambda: services.complete_upload(AudioUpload.objects.get(pk=upload.pk)),
        )
        try:
            with pytest.raises(DomainError) as exc:
                second.result(timeout=10)
            assert exc.value.code == ErrorCode.UPLOAD_INCOMPLETE
        finally:
            release_copy.set()
        completed = first.result(timeout=10)

    completed.refresh_from_db()
    assert calls == 1
    assert completed.status == UploadStatus.UPLOADED
    assert completed.finalization_claim_id is None
    assert completed.checksum


def test_complete_delete_cannot_recreate_final_object(
    user,
    s3,
    monkeypatch,
    celery_queue,
):
    upload = make_pending_upload(user, s3)
    storage = get_storage()
    original_copy = storage.copy_if_match
    copy_entered = threading.Event()
    release_copy = threading.Event()

    def blocking_copy(source_key: str, destination_key: str, etag: str):
        copy_entered.set()
        assert release_copy.wait(timeout=10)
        return original_copy(source_key, destination_key, etag)

    monkeypatch.setattr(storage, "copy_if_match", blocking_copy)

    with ThreadPoolExecutor(max_workers=1) as pool:
        finalizer = pool.submit(
            in_thread,
            lambda: services.complete_upload(AudioUpload.objects.get(pk=upload.pk)),
        )
        assert copy_entered.wait(timeout=10)

        services.delete_upload(AudioUpload.objects.get(pk=upload.pk))
        upload.refresh_from_db()
        assert upload.status == UploadStatus.DELETED
        assert upload.finalization_claim_id is not None

        celery_queue.drain()
        upload.refresh_from_db()
        assert upload.object_deleted_at is None

        release_copy.set()
        with pytest.raises(DomainError) as exc:
            finalizer.result(timeout=10)
        assert exc.value.code == ErrorCode.UPLOAD_NOT_FOUND

    upload.refresh_from_db()
    assert upload.finalization_claim_id is None
    assert upload.object_deleted_at is not None
    assert_missing(s3, upload.object_key)


def test_live_finalization_is_not_expired_by_maintenance(
    user,
    s3,
    monkeypatch,
    celery_queue,
):
    upload = make_pending_upload(user, s3)
    old = timezone.now() - timedelta(days=2)
    AudioUpload.objects.filter(pk=upload.pk).update(
        created_at=old,
        upload_url_expires_at=old,
    )

    storage = get_storage()
    original_copy = storage.copy_if_match
    copy_entered = threading.Event()
    release_copy = threading.Event()

    def blocking_copy(source_key: str, destination_key: str, etag: str):
        copy_entered.set()
        assert release_copy.wait(timeout=10)
        return original_copy(source_key, destination_key, etag)

    monkeypatch.setattr(storage, "copy_if_match", blocking_copy)

    with ThreadPoolExecutor(max_workers=1) as pool:
        finalizer = pool.submit(
            in_thread,
            lambda: services.complete_upload(AudioUpload.objects.get(pk=upload.pk)),
        )
        assert copy_entered.wait(timeout=10)

        maintain_uploads.apply()
        celery_queue.drain()
        upload.refresh_from_db()
        assert upload.status == UploadStatus.FINALIZING
        assert upload.finalization_claim_id is not None

        release_copy.set()
        completed = finalizer.result(timeout=10)

    completed.refresh_from_db()
    assert completed.status == UploadStatus.UPLOADED
    assert completed.finalization_claim_id is None
    assert s3.head_object(Bucket=settings.S3_BUCKET, Key=completed.object_key)


def test_expired_finalization_is_recovered_by_maintenance(user, s3, settings, celery_queue):
    settings.UPLOAD_FINALIZATION_LEASE_SECONDS = 60
    upload = make_pending_upload(user, s3)
    old = timezone.now() - timedelta(days=2)
    AudioUpload.objects.filter(pk=upload.pk).update(
        status=UploadStatus.FINALIZING,
        created_at=old,
        upload_url_expires_at=old,
        finalization_claim_id="lost-finalizer",
        finalization_claimed_at=timezone.now() - timedelta(seconds=61),
    )

    maintain_uploads.apply()
    celery_queue.drain()

    upload.refresh_from_db()
    assert upload.status == UploadStatus.DELETED
    assert upload.object_deleted_at is not None
    assert upload.finalization_claim_id is None
    assert_missing(s3, upload.object_key)
    assert_missing(s3, upload.staging_object_key)
