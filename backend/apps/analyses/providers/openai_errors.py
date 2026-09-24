"""OpenAI SDK -> ProcessingError translation, shared by the STT and LLM adapters."""

import httpx2
import openai

from apps.common.errors import ErrorCode, ProcessingError, permanent, retryable


def openai_client(
    api_key: str, base_url: str | None, timeout: float, http_client: httpx2.Client | None = None
) -> openai.OpenAI:
    if not api_key:
        raise permanent(ErrorCode.PROVIDER_CONFIGURATION_ERROR, "OPENAI_API_KEY is not set.")
    # SDK-level retries are disabled: retries are owned by the pipeline so that
    # every attempt is counted and bounded in one place.
    return openai.OpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=timeout,
        max_retries=0,
        http_client=http_client,
    )


def translate_openai_error(
    exc: openai.OpenAIError,
    *,
    rate_limited: ErrorCode,
    timeout: ErrorCode,
    failure: ErrorCode,
    bad_request: ErrorCode,
) -> ProcessingError:
    if isinstance(exc, openai.RateLimitError):
        return retryable(rate_limited, "Provider rate limit reached.")
    if isinstance(exc, openai.APITimeoutError):
        return retryable(timeout, "Provider request timed out.")
    if isinstance(exc, openai.APIConnectionError):
        return retryable(failure, "Provider could not be reached.")
    if isinstance(exc, openai.AuthenticationError | openai.PermissionDeniedError):
        return permanent(ErrorCode.PROVIDER_CONFIGURATION_ERROR, "Provider rejected credentials.")
    if isinstance(exc, openai.NotFoundError):
        return permanent(ErrorCode.PROVIDER_CONFIGURATION_ERROR, "Provider model not found.")
    if isinstance(exc, openai.BadRequestError | openai.UnprocessableEntityError):
        return permanent(bad_request, "Provider rejected the request.")
    if isinstance(exc, openai.APIStatusError) and exc.status_code >= 500:
        return retryable(failure, f"Provider error (HTTP {exc.status_code}).")
    return permanent(failure, "Unexpected provider error.")
