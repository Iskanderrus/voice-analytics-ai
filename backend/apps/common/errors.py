"""Application error model shared by the API and the processing pipeline."""

from enum import StrEnum


class ErrorCode(StrEnum):
    # Client / input errors
    VALIDATION_ERROR = "VALIDATION_ERROR"
    UPLOAD_NOT_FOUND = "UPLOAD_NOT_FOUND"
    UPLOAD_INCOMPLETE = "UPLOAD_INCOMPLETE"
    UPLOAD_OBJECT_MISMATCH = "UPLOAD_OBJECT_MISMATCH"
    UPLOAD_TOO_LARGE = "UPLOAD_TOO_LARGE"
    UNSUPPORTED_AUDIO = "UNSUPPORTED_AUDIO"
    ANALYSIS_NOT_FOUND = "ANALYSIS_NOT_FOUND"
    ANALYSIS_FAILED = "ANALYSIS_FAILED"
    PROMPT_TEMPLATE_NOT_FOUND = "PROMPT_TEMPLATE_NOT_FOUND"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    # Pipeline errors
    STORAGE_ERROR = "STORAGE_ERROR"
    TRANSCRIPTION_FAILED = "TRANSCRIPTION_FAILED"
    TRANSCRIPTION_TIMEOUT = "TRANSCRIPTION_TIMEOUT"
    LLM_RATE_LIMITED = "LLM_RATE_LIMITED"
    LLM_INVALID_OUTPUT = "LLM_INVALID_OUTPUT"
    LLM_PROVIDER_ERROR = "LLM_PROVIDER_ERROR"
    NO_MATCHING_TEMPLATE = "NO_MATCHING_TEMPLATE"
    PROVIDER_CONFIGURATION_ERROR = "PROVIDER_CONFIGURATION_ERROR"
    STAGE_ATTEMPTS_EXHAUSTED = "STAGE_ATTEMPTS_EXHAUSTED"
    INTERNAL_PROCESSING_ERROR = "INTERNAL_PROCESSING_ERROR"


class DomainError(Exception):
    """Raised by application services; rendered by the API as a safe error body."""

    status_code = 400

    def __init__(self, code: ErrorCode, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        if status_code is not None:
            self.status_code = status_code


class ProcessingError(Exception):
    """Raised inside pipeline stages.

    `retryable` decides whether the stage is retried with backoff or the job is
    failed immediately. `message` is persisted on the job and shown to clients,
    so it must never contain secrets, stack traces or transcript content.
    """

    def __init__(self, code: ErrorCode, message: str, *, retryable: bool):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.retryable = retryable


def retryable(code: ErrorCode, message: str) -> ProcessingError:
    return ProcessingError(code, message, retryable=True)


def permanent(code: ErrorCode, message: str) -> ProcessingError:
    return ProcessingError(code, message, retryable=False)
