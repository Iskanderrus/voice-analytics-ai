"""Liveness: the process answers. Readiness: its hard dependencies answer.

Readiness deliberately does not check AI providers: an Ollama/OpenAI outage
should fail jobs (retry, then FAILED), not pull the API out of the load balancer.
"""

import logging

import redis
from django.conf import settings
from django.db import connection
from django.http import JsonResponse

from apps.uploads.storage import get_storage

logger = logging.getLogger(__name__)


def live() -> JsonResponse:
    return JsonResponse({"status": "ok"})


def _check_database() -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")


def _check_redis() -> None:
    client = redis.Redis.from_url(
        settings.CELERY_BROKER_URL, socket_connect_timeout=2, socket_timeout=2
    )
    try:
        client.ping()
    finally:
        client.close()


def _check_storage() -> None:
    get_storage().check_bucket()


def ready() -> JsonResponse:
    checks = {"database": _check_database, "redis": _check_redis, "storage": _check_storage}
    results: dict[str, str] = {}
    for name, check in checks.items():
        try:
            check()
            results[name] = "ok"
        except Exception:  # noqa: BLE001 - any failure means "not ready"; details go to logs
            logger.warning("readiness check failed", extra={"check": name}, exc_info=True)
            results[name] = "error"
    healthy = all(value == "ok" for value in results.values())
    return JsonResponse(
        {"status": "ok" if healthy else "degraded", "checks": results},
        status=200 if healthy else 503,
    )
