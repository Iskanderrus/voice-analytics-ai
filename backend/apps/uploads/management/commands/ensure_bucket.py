"""Creates the audio bucket on a local S3-compatible endpoint (MinIO).

In AWS the bucket is owned by Terraform; this command refuses to run there.
"""

import time
from typing import Any

from botocore.exceptions import EndpointConnectionError
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.uploads.storage import get_storage


class Command(BaseCommand):
    help = "Create the S3 bucket on a local endpoint if it does not exist."

    def handle(self, *args: Any, **options: Any) -> None:
        if not settings.S3_ENDPOINT_URL:
            raise CommandError("Refusing to create buckets on AWS; Terraform owns the bucket.")
        # MinIO may still be starting when this runs from the `migrate` container.
        for attempt in range(15):
            try:
                created = get_storage().create_bucket_if_missing()
                break
            except EndpointConnectionError:
                if attempt == 14:
                    raise
                time.sleep(2)
        self.stdout.write(f"bucket {settings.S3_BUCKET} {'created' if created else 'exists'}")
