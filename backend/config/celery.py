import os
from typing import Any

from celery import Celery
from celery.signals import before_task_publish, task_postrun, task_prerun

from apps.common.logging import bind_context, clear_context, get_context

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("voice_analysis")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


@before_task_publish.connect
def _propagate_request_id(headers: dict[str, Any] | None = None, **_: Any) -> None:
    """Carry the HTTP request id into the task so API and worker logs correlate."""
    request_id = get_context().get("request_id")
    if headers is not None and request_id:
        headers["request_id"] = request_id


@task_prerun.connect
def _bind_task_context(task: Any = None, **_: Any) -> None:
    clear_context()
    request_id = getattr(task.request, "request_id", None) if task else None
    if request_id:
        bind_context(request_id=request_id)


@task_postrun.connect
def _clear_task_context(**_: Any) -> None:
    clear_context()
