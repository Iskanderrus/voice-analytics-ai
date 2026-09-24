"""Allowed job transitions are centralized so retries cannot skip or revive terminal state."""

from django.utils import timezone

from apps.analyses.models import AnalysisJob, JobStatus

S = JobStatus

TRANSITIONS: dict[str, frozenset[str]] = {
    S.CREATED: frozenset({S.QUEUED, S.TRANSCRIBING, S.FAILED}),
    S.QUEUED: frozenset({S.TRANSCRIBING, S.FAILED}),
    S.TRANSCRIBING: frozenset({S.ANALYSING_STANDARD, S.FAILED}),
    S.ANALYSING_STANDARD: frozenset({S.ANALYSING_CUSTOM, S.COMPLETED, S.FAILED}),
    S.ANALYSING_CUSTOM: frozenset({S.COMPLETED, S.FAILED}),
    S.COMPLETED: frozenset(),
    S.FAILED: frozenset(),
}

TERMINAL = frozenset({S.COMPLETED, S.FAILED})

STAGE_NAMES: dict[str, str] = {
    S.CREATED: "queued",
    S.QUEUED: "queued",
    S.TRANSCRIBING: "transcription",
    S.ANALYSING_STANDARD: "standard_analysis",
    S.ANALYSING_CUSTOM: "custom_analysis",
    S.COMPLETED: "done",
}


class InvalidTransition(Exception):
    pass


def can_transition(current: str, target: str) -> bool:
    return target in TRANSITIONS[current]


def current_stage(job: AnalysisJob) -> str:
    if job.status == S.FAILED:
        return job.error_stage or "unknown"
    return STAGE_NAMES[job.status]


def transition(
    job: AnalysisJob,
    target: JobStatus,
    *,
    error_code: str | None = None,
    error_message: str | None = None,
) -> list[str]:
    """Call while holding the job row lock; callers persist the returned fields."""
    if not can_transition(job.status, target):
        raise InvalidTransition(f"{job.status} -> {target} is not allowed for job {job.id}")

    now = timezone.now()
    fields = [
        "status",
        "stage_attempts",
        "stage_claim_id",
        "stage_claimed_at",
        "updated_at",
    ]
    if target == S.FAILED:
        job.error_stage = STAGE_NAMES.get(job.status, "unknown")
        job.error_code = error_code
        job.error_message = (error_message or "")[:500]
        job.failed_at = now
        fields += ["error_stage", "error_code", "error_message", "failed_at"]
    elif target == S.TRANSCRIBING and job.started_at is None:
        job.started_at = now
        fields.append("started_at")
    elif target == S.COMPLETED:
        job.completed_at = now
        fields.append("completed_at")

    job.status = target
    job.stage_claim_id = None
    job.stage_claimed_at = None
    if target != S.FAILED:
        job.stage_attempts = 0
    return fields
