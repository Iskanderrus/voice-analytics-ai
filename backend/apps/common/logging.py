"""Structured JSON logging with a per-request / per-task context.

Context (request_id, job_id, stage, ...) is bound once via `bind_context` or
`log_context` and attached to every record emitted in that scope, so call sites
do not have to repeat identifiers.
"""

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

_context: ContextVar[dict[str, Any] | None] = ContextVar("log_context", default=None)

# Keys whose values are never written to logs, wherever they appear.
REDACTED_KEYS = frozenset(
    {"authorization", "token", "password", "secret", "api_key", "transcript", "text", "content"}
)
_RESERVED = frozenset(logging.makeLogRecord({}).__dict__) | {"message", "asctime", "context"}


def get_context() -> dict[str, Any]:
    return dict(_context.get() or {})


def bind_context(**values: Any) -> None:
    _context.set({**get_context(), **{k: v for k, v in values.items() if v is not None}})


def clear_context() -> None:
    _context.set(None)


@contextmanager
def log_context(**values: Any) -> Iterator[None]:
    token = _context.set({**get_context(), **{k: v for k, v in values.items() if v is not None}})
    try:
        yield
    finally:
        _context.reset(token)


def _redact(value: Any, key: str = "") -> Any:
    if key.lower() in REDACTED_KEYS:
        return "[REDACTED]"
    if isinstance(value, dict):
        return {k: _redact(v, str(k)) for k, v in value.items()}
    return value


class ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        extras = {k: v for k, v in record.__dict__.items() if k not in _RESERVED}
        record.context = _redact({**get_context(), **extras})
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            **getattr(record, "context", {}),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)
