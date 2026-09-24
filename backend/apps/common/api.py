"""Keep DRF and Django failures on one stable client-facing error contract."""

import logging
from typing import Any

from django.contrib.auth.models import User
from django.http import HttpRequest, JsonResponse
from rest_framework import exceptions, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from apps.common.errors import DomainError, ErrorCode

logger = logging.getLogger(__name__)


def request_user(request: Request) -> User:
    user = request.user
    assert isinstance(user, User)
    return user


def error_response(
    code: str, message: str, http_status: int, details: Any | None = None
) -> Response:
    body: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        body["details"] = details
    return Response({"error": body}, status=http_status)


def exception_handler(exc: Exception, context: dict[str, Any]) -> Response | None:
    if isinstance(exc, DomainError):
        return error_response(exc.code, exc.message, exc.status_code)

    response = drf_exception_handler(exc, context)
    if response is None:
        # Internal exceptions stay server-side; exposing them would leak implementation details.
        logger.exception("unhandled API error")
        return error_response(
            ErrorCode.INTERNAL_PROCESSING_ERROR,
            "Internal server error.",
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    if isinstance(exc, exceptions.ValidationError):
        return error_response(
            ErrorCode.VALIDATION_ERROR, "Invalid request.", response.status_code, response.data
        )
    code = getattr(exc, "default_code", "error")
    detail = response.data.get("detail", "") if isinstance(response.data, dict) else ""
    return error_response(str(code).upper(), str(detail), response.status_code)


# Some failures happen before DRF can apply its handler, so outer Django
# handlers preserve the same public error shape in production.


def _django_error(code: str, message: str, status_code: int) -> JsonResponse:
    return JsonResponse({"error": {"code": code, "message": message}}, status=status_code)


def bad_request(request: HttpRequest, exception: Exception) -> JsonResponse:
    return _django_error("BAD_REQUEST", "Bad request.", 400)


def permission_denied(request: HttpRequest, exception: Exception) -> JsonResponse:
    return _django_error("PERMISSION_DENIED", "Permission denied.", 403)


def not_found(request: HttpRequest, exception: Exception) -> JsonResponse:
    return _django_error("NOT_FOUND", "Not found.", 404)


def server_error(request: HttpRequest) -> JsonResponse:
    return _django_error(ErrorCode.INTERNAL_PROCESSING_ERROR, "Internal server error.", 500)
