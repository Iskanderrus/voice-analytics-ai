"""S3-compatible object storage (MinIO locally, S3 in AWS) behind one small class."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import cache
from pathlib import Path
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings


class StorageUnavailable(Exception):
    """Storage could not be reached or returned an unexpected error. Retryable."""


class ObjectNotFound(Exception):
    pass


class ObjectChanged(Exception):
    """The staging object changed after it was verified."""


@dataclass(frozen=True)
class PresignedPost:
    url: str
    fields: dict[str, str]
    expires_at: datetime


@dataclass(frozen=True)
class ObjectInfo:
    size: int
    content_type: str
    etag: str


_NOT_FOUND_CODES = {"404", "NoSuchKey", "NotFound"}
_PRECONDITION_CODES = {"412", "PreconditionFailed"}


class ObjectStorage:
    def __init__(
        self, bucket: str, region: str, endpoint_url: str | None, public_endpoint_url: str | None
    ):
        self.bucket = bucket
        config = Config(
            signature_version="s3v4",
            s3={"addressing_style": "path" if endpoint_url else "auto"},
            connect_timeout=5,
            read_timeout=60,
            retries={"max_attempts": 3, "mode": "standard"},
        )
        self._client = boto3.client(
            "s3", region_name=region, endpoint_url=endpoint_url, config=config
        )
        # Presigning is local; this client only makes the signed URL use a client-reachable host.
        self._presign_client = boto3.client(
            "s3", region_name=region, endpoint_url=public_endpoint_url, config=config
        )

    def presign_post(
        self, key: str, content_type: str, exact_bytes: int, ttl_seconds: int
    ) -> PresignedPost:
        """The POST policy constrains key, content type and the declared byte length."""
        post: dict[str, Any] = self._presign_client.generate_presigned_post(
            Bucket=self.bucket,
            Key=key,
            Fields={"Content-Type": content_type},
            Conditions=[
                {"Content-Type": content_type},
                ["content-length-range", exact_bytes, exact_bytes],
            ],
            ExpiresIn=ttl_seconds,
        )
        return PresignedPost(
            url=post["url"],
            fields=post["fields"],
            expires_at=datetime.now(UTC) + timedelta(seconds=ttl_seconds),
        )

    def head(self, key: str) -> ObjectInfo | None:
        try:
            response = self._client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in _NOT_FOUND_CODES:
                return None
            raise StorageUnavailable(str(exc)) from exc
        except BotoCoreError as exc:
            raise StorageUnavailable(str(exc)) from exc
        return ObjectInfo(
            size=response["ContentLength"],
            content_type=response.get("ContentType", ""),
            etag=response.get("ETag", "").strip('"'),
        )

    def copy_if_match(self, source_key: str, destination_key: str, etag: str) -> ObjectInfo:
        """Copy the verified staging version into the server-owned namespace."""
        try:
            self._client.copy_object(
                Bucket=self.bucket,
                Key=destination_key,
                CopySource={"Bucket": self.bucket, "Key": source_key},
                CopySourceIfMatch=etag,
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in _PRECONDITION_CODES:
                raise ObjectChanged(source_key) from exc
            if exc.response.get("Error", {}).get("Code") in _NOT_FOUND_CODES:
                raise ObjectChanged(source_key) from exc
            raise StorageUnavailable(str(exc)) from exc
        except BotoCoreError as exc:
            raise StorageUnavailable(str(exc)) from exc

        info = self.head(destination_key)
        if info is None:
            raise StorageUnavailable("Finalized object is missing after copy.")
        return info

    def download_to(self, key: str, destination: Path) -> None:
        """Streams the object to disk; audio never has to fit in memory."""
        try:
            self._client.download_file(self.bucket, key, str(destination))
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in _NOT_FOUND_CODES:
                raise ObjectNotFound(key) from exc
            raise StorageUnavailable(str(exc)) from exc
        except BotoCoreError as exc:
            raise StorageUnavailable(str(exc)) from exc

    def delete(self, key: str) -> None:
        """Idempotent: deleting a missing key succeeds."""
        try:
            self._client.delete_object(Bucket=self.bucket, Key=key)
        except (ClientError, BotoCoreError) as exc:
            raise StorageUnavailable(str(exc)) from exc

    def check_bucket(self) -> None:
        self._client.head_bucket(Bucket=self.bucket)

    def create_bucket_if_missing(self) -> bool:
        """Local development only (MinIO); in AWS the bucket is owned by Terraform."""
        try:
            self.check_bucket()
            return False
        except ClientError:
            self._client.create_bucket(Bucket=self.bucket)
            return True


@cache
def get_storage() -> ObjectStorage:
    return ObjectStorage(
        bucket=settings.S3_BUCKET,
        region=settings.S3_REGION,
        endpoint_url=settings.S3_ENDPOINT_URL,
        public_endpoint_url=settings.S3_PUBLIC_ENDPOINT_URL,
    )
