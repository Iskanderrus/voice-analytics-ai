"""Downloads the faster-whisper model into HF_HOME. Run once, with network access,
before starting the offline stack (`make models`)."""

from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Download the configured faster-whisper model (STT_MODEL) into the local cache."

    def handle(self, *args: Any, **options: Any) -> None:
        from faster_whisper import download_model

        path = download_model(settings.STT_MODEL)
        self.stdout.write(f"whisper model '{settings.STT_MODEL}' available at {path}")
