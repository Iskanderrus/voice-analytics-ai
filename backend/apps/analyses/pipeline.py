"""Provider I/O stays outside database transactions; short leases own each execution."""

import logging
import re
import tempfile
import time
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from pydantic import BaseModel, ValidationError

from apps.analyses import state
from apps.analyses.models import (
    AnalysisJob,
    AnalysisProfile,
    AnalysisResult,
    JobStatus,
    ResultKind,
    Transcript,
)
from apps.analyses.providers import get_llm_provider, get_transcription_provider
from apps.analyses.providers.llm import StructuredAnalysisRequest
from apps.analyses.providers.transcription import AudioSource
from apps.analyses.schemas import StandardAnalysis
from apps.common.errors import ErrorCode, permanent, retryable
from apps.common.logging import bind_context
from apps.prompts.builder import (
    BuiltPrompt,
    ChatMessage,
    build_standard_prompt,
    build_template_prompt,
)
from apps.prompts.selection import select_template
from apps.uploads.models import UploadStatus
from apps.uploads.storage import ObjectNotFound, StorageUnavailable, get_storage

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Claim:
    job: AnalysisJob | None
    status: JobStatus | None
    claim_id: str | None


def _elapsed_ms(started: float) -> int:
    return round((time.monotonic() - started) * 1000)


def claim_stage(
    job_id: str,
    stage_status: JobStatus,
    claim_id: str,
    entry_statuses: frozenset[str] = frozenset(),
) -> Claim:
    """Acquire an execution lease without charging live concurrent duplicates."""
    now = timezone.now()
    lease_cutoff = now - timedelta(seconds=settings.PIPELINE_STAGE_LEASE_SECONDS)

    with transaction.atomic():
        job = (
            AnalysisJob.objects.select_for_update(of=("self",))
            .select_related("audio_upload")
            .filter(pk=job_id)
            .first()
        )
        if job is None:
            logger.info("job no longer exists; dropping stage")
            return Claim(None, None, None)

        if job.status in entry_statuses:
            fields = state.transition(job, stage_status)
        elif job.status == stage_status:
            lease_is_live = job.stage_claimed_at is not None and job.stage_claimed_at > lease_cutoff
            if lease_is_live:
                logger.info("stage already has a live execution lease")
                return Claim(None, None, None)
            fields = []
        else:
            logger.info("stage already done; skipping", extra={"job_status": job.status})
            return Claim(None, JobStatus(job.status), None)

        if job.stage_attempts >= settings.PIPELINE_MAX_STAGE_ATTEMPTS:
            fields = state.transition(
                job,
                JobStatus.FAILED,
                error_code=ErrorCode.STAGE_ATTEMPTS_EXHAUSTED,
                error_message="Stage did not complete within the allowed attempts.",
            )
            job.save(update_fields=fields)
            logger.error("stage attempts exhausted", extra={"error_code": job.error_code})
            return Claim(None, JobStatus.FAILED, None)

        job.stage_attempts += 1
        job.stage_claim_id = claim_id
        job.stage_claimed_at = now
        job.save(
            update_fields=[
                *fields,
                "stage_attempts",
                "stage_claim_id",
                "stage_claimed_at",
                "updated_at",
            ]
        )

    bind_context(attempt=job.stage_attempts, upload_id=str(job.audio_upload_id))
    return Claim(job, JobStatus(job.status), claim_id)


def release_stage_claim(job_id: str, claim_id: str) -> bool:
    """Release only the lease owned by this execution before a controlled retry."""
    released = AnalysisJob.objects.filter(pk=job_id, stage_claim_id=claim_id).update(
        stage_claim_id=None,
        stage_claimed_at=None,
        updated_at=timezone.now(),
    )
    return released == 1


def _lock_if_in_stage(job_id: str, stage_status: JobStatus, claim_id: str) -> AnalysisJob | None:
    job = AnalysisJob.objects.select_for_update().filter(pk=job_id).first()
    if job is None:
        logger.info("job deleted while the stage ran; discarding output")
        return None
    if job.status != stage_status or job.stage_claim_id != claim_id:
        logger.warning(
            "stage ownership changed; discarding stale output",
            extra={"job_status": job.status},
        )
        return None
    return job


def fail_job(job_id: str, claim_id: str, code: str, message: str) -> bool:
    """Persist a failure only while this execution still owns the stage lease."""
    with transaction.atomic():
        job = AnalysisJob.objects.select_for_update().filter(pk=job_id).first()
        if job is None or job.status in state.TERMINAL or job.stage_claim_id != claim_id:
            return False
        job.save(
            update_fields=state.transition(
                job, JobStatus.FAILED, error_code=code, error_message=message
            )
        )
    logger.error("job failed", extra={"error_code": code})
    return True


def current_attempts(job_id: str) -> int:
    return (
        AnalysisJob.objects.filter(pk=job_id).values_list("stage_attempts", flat=True).first() or 0
    )


def _probe_duration(path: Path) -> float | None:
    import av

    try:
        with av.open(str(path)) as container:
            return round(container.duration / 1_000_000, 2) if container.duration else None
    except av.error.FFmpegError:
        return None


def transcribe(job_id: str, claim_id: str) -> JobStatus | None:
    claim = claim_stage(
        job_id,
        JobStatus.TRANSCRIBING,
        claim_id,
        frozenset({JobStatus.CREATED, JobStatus.QUEUED}),
    )
    if claim.job is None:
        return claim.status
    upload = claim.job.audio_upload
    if upload.status != UploadStatus.UPLOADED:
        raise permanent(ErrorCode.UPLOAD_NOT_FOUND, "The audio upload is no longer available.")

    provider = get_transcription_provider()
    bind_context(provider=provider.name, model=provider.model)
    suffix = Path(upload.object_key).suffix
    with tempfile.TemporaryDirectory(prefix="va-") as tmp:
        path = Path(tmp) / f"source{suffix}"
        try:
            get_storage().download_to(upload.object_key, path)
        except ObjectNotFound as exc:
            raise permanent(ErrorCode.UPLOAD_NOT_FOUND, "Audio object is missing.") from exc
        except StorageUnavailable as exc:
            raise retryable(
                ErrorCode.STORAGE_ERROR, "Object storage is temporarily unavailable."
            ) from exc
        started = time.monotonic()
        result = provider.transcribe(
            AudioSource(path=path, content_type=upload.content_type, size_bytes=path.stat().st_size)
        )
        latency_ms = _elapsed_ms(started)
        duration = result.duration_seconds or _probe_duration(path)

    if not result.text.strip():
        raise permanent(ErrorCode.TRANSCRIPTION_FAILED, "No speech was detected in the audio.")

    with transaction.atomic():
        job = _lock_if_in_stage(job_id, JobStatus.TRANSCRIBING, claim_id)
        if job is None:
            return _status_of(job_id)
        try:
            with transaction.atomic():
                Transcript.objects.create(
                    analysis_job=job,
                    text=result.text,
                    detected_language=result.language or "",
                    duration_seconds=duration,
                    provider=result.provider,
                    provider_model=result.model,
                    provider_metadata=result.metadata,
                    latency_ms=latency_ms,
                )
        except IntegrityError:
            logger.warning("transcript already exists; keeping the first one")
        job.save(update_fields=state.transition(job, JobStatus.ANALYSING_STANDARD))
    logger.info(
        "stage completed",
        extra={
            "event": "stage_completed",
            "duration_ms": latency_ms,
            "audio_seconds": duration,
            "language": result.language,
        },
    )
    return JobStatus.ANALYSING_STANDARD


def _status_of(job_id: str) -> JobStatus | None:
    status = AnalysisJob.objects.filter(pk=job_id).values_list("status", flat=True).first()
    return JobStatus(status) if status else None


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


@dataclass(frozen=True)
class ValidatedOutput:
    output: BaseModel
    provider: str
    model: str
    attempts: int
    latency_ms: int
    usage: dict[str, object]


def _parse(content: str, output_model: type[BaseModel]) -> BaseModel:
    return output_model.model_validate_json(_FENCE.sub("", content.strip()))


def _describe_errors(exc: ValidationError) -> str:
    errors = exc.errors(include_input=False, include_url=False)[:10]
    return "; ".join(f"{'.'.join(map(str, e['loc'])) or '<root>'}: {e['msg']}" for e in errors)


def generate_validated(prompt: BuiltPrompt) -> ValidatedOutput:
    """A bounded repair attempt handles schema slips without accepting malformed model data."""
    provider = get_llm_provider()
    bind_context(provider=provider.name, model=provider.model)
    messages = list(prompt.messages)
    input_tokens = output_tokens = 0
    started = time.monotonic()
    last_error = ""

    for attempt in range(1, settings.LLM_MAX_OUTPUT_ATTEMPTS + 1):
        response = provider.generate_structured(
            StructuredAnalysisRequest(
                messages=messages, json_schema=prompt.json_schema, schema_name=prompt.schema_name
            )
        )
        input_tokens += response.input_tokens or 0
        output_tokens += response.output_tokens or 0
        try:
            output = _parse(response.content, prompt.output_model)
        except ValidationError as exc:
            last_error = _describe_errors(exc)
            logger.warning(
                "LLM output failed validation",
                extra={"output_attempt": attempt, "validation_errors": last_error},
            )
            messages = [
                *prompt.messages,
                ChatMessage(role="assistant", content=response.content[:8000]),
                ChatMessage(
                    role="user",
                    content=(
                        "The previous response was not valid for the JSON schema: "
                        f"{last_error}. Return only the corrected JSON object."
                    ),
                ),
            ]
            continue
        return ValidatedOutput(
            output=output,
            provider=response.provider,
            model=response.model,
            attempts=attempt,
            latency_ms=_elapsed_ms(started),
            usage={
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                **response.metadata,
                **prompt.metadata,
            },
        )
    raise permanent(
        ErrorCode.LLM_INVALID_OUTPUT,
        f"Model output failed validation after {settings.LLM_MAX_OUTPUT_ATTEMPTS} attempts.",
    )


def run_standard_analysis(job_id: str, claim_id: str) -> JobStatus | None:
    claim = claim_stage(job_id, JobStatus.ANALYSING_STANDARD, claim_id)
    if claim.job is None:
        return claim.status
    transcript = Transcript.objects.get(analysis_job=claim.job)

    prompt = build_standard_prompt(transcript.text)
    validated = generate_validated(prompt)
    analysis = validated.output
    assert isinstance(analysis, StandardAnalysis)

    with transaction.atomic():
        job = _lock_if_in_stage(job_id, JobStatus.ANALYSING_STANDARD, claim_id)
        if job is None:
            return _status_of(job_id)
        AnalysisResult.objects.create(
            analysis_job=job,
            kind=ResultKind.STANDARD,
            analysis_type="standard",
            prompt_version=prompt.prompt_version,
            provider=validated.provider,
            model=validated.model,
            structured_output=analysis.model_dump(mode="json"),
            summary=analysis.summary,
            usage_metadata=validated.usage,
            output_attempts=validated.attempts,
            latency_ms=validated.latency_ms,
        )
        template = (
            select_template(analysis, owner_id=job.audio_upload.owner_id)
            if job.analysis_profile == AnalysisProfile.DEFAULT
            else None
        )
        if template is None:
            fields = state.transition(job, JobStatus.COMPLETED)
        else:
            job.selected_template = template
            fields = [*state.transition(job, JobStatus.ANALYSING_CUSTOM), "selected_template"]
        job.save(update_fields=fields)

    logger.info(
        "stage completed",
        extra={
            "event": "stage_completed",
            "duration_ms": validated.latency_ms,
            "input_tokens": validated.usage.get("input_tokens"),
            "output_tokens": validated.usage.get("output_tokens"),
            "template": str(template) if template else None,
        },
    )
    return JobStatus(job.status)


def run_custom_analysis(job_id: str, claim_id: str) -> JobStatus | None:
    claim = claim_stage(job_id, JobStatus.ANALYSING_CUSTOM, claim_id)
    if claim.job is None:
        return claim.status
    template = claim.job.selected_template
    if template is None:
        raise permanent(ErrorCode.NO_MATCHING_TEMPLATE, "No template was selected for this job.")
    bind_context(template=str(template))

    transcript = Transcript.objects.get(analysis_job=claim.job)
    standard = AnalysisResult.objects.get(analysis_job=claim.job, kind=ResultKind.STANDARD)
    prompt = build_template_prompt(
        template, transcript.text, StandardAnalysis.model_validate(standard.structured_output)
    )
    validated = generate_validated(prompt)
    output = validated.output.model_dump(mode="json")

    with transaction.atomic():
        job = _lock_if_in_stage(job_id, JobStatus.ANALYSING_CUSTOM, claim_id)
        if job is None:
            return _status_of(job_id)
        AnalysisResult.objects.create(
            analysis_job=job,
            kind=ResultKind.TEMPLATE,
            analysis_type=template.analysis_type,
            prompt_template=template,
            prompt_version=prompt.prompt_version,
            provider=validated.provider,
            model=validated.model,
            structured_output=output,
            summary=str(output.get("summary", "")),
            usage_metadata=validated.usage,
            output_attempts=validated.attempts,
            latency_ms=validated.latency_ms,
        )
        job.save(update_fields=state.transition(job, JobStatus.COMPLETED))

    logger.info(
        "stage completed",
        extra={
            "event": "stage_completed",
            "duration_ms": validated.latency_ms,
            "input_tokens": validated.usage.get("input_tokens"),
            "output_tokens": validated.usage.get("output_tokens"),
        },
    )
    return JobStatus.COMPLETED
