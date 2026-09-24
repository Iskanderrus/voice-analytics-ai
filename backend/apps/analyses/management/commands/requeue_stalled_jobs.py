from datetime import timedelta
from typing import Any

from django.core.management.base import BaseCommand

from apps.analyses.services import requeue_stalled_jobs


class Command(BaseCommand):
    help = "Re-publish the current stage of non-terminal jobs that have not advanced recently."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--older-than", type=int, default=None, help="Seconds (default PIPELINE_STALL_SECONDS)"
        )

    def handle(self, *args: Any, older_than: int | None, **options: Any) -> None:
        delta = timedelta(seconds=older_than) if older_than is not None else None
        requeued = requeue_stalled_jobs(delta)
        self.stdout.write(f"requeued {len(requeued)} job(s)")
