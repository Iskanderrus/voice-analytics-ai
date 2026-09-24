from typing import Any

from django.core.management.base import BaseCommand
from django.db.models import Q

from apps.uploads.models import AudioUpload, UploadStatus
from apps.uploads.tasks import delete_upload_object


class Command(BaseCommand):
    help = "Re-enqueue storage cleanup for deleted uploads with unfinished object cleanup."

    def handle(self, *args: Any, **options: Any) -> None:
        pending = list(
            AudioUpload.objects.filter(status=UploadStatus.DELETED)
            .filter(Q(object_deleted_at__isnull=True) | Q(staging_deleted_at__isnull=True))
            .values_list("id", flat=True)
        )
        for upload_id in pending:
            delete_upload_object.delay(str(upload_id))
        self.stdout.write(f"enqueued cleanup for {len(pending)} upload(s)")
