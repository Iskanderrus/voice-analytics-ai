import logging
import re
import time
import uuid
from collections.abc import Callable

from django.http import HttpRequest, HttpResponse

from apps.common import health
from apps.common.logging import bind_context, clear_context

logger = logging.getLogger("apps.request")

_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class RequestContextMiddleware:
    """A stable request id keeps API and worker-side diagnostics correlated."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        incoming = request.headers.get("X-Request-ID", "")
        request_id = incoming if _VALID_REQUEST_ID.match(incoming) else uuid.uuid4().hex
        clear_context()
        bind_context(request_id=request_id)
        started = time.monotonic()
        try:
            response = self.get_response(request)
            response["X-Request-ID"] = request_id
            if not request.path.startswith("/health/"):
                logger.info(
                    "request",
                    extra={
                        "method": request.method,
                        "path": request.path,
                        "status": response.status_code,
                        "duration_ms": round((time.monotonic() - started) * 1000),
                    },
                )
            return response
        finally:
            clear_context()


class HealthCheckMiddleware:
    """Keep load-balancer probes independent of application host and auth policy.

    Target probes commonly use an IP Host header, so running them through normal
    host validation can make healthy processes look unavailable.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if request.method == "GET" and request.path == "/health/live":
            return health.live()
        if request.method == "GET" and request.path == "/health/ready":
            return health.ready()
        return self.get_response(request)
