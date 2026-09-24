from typing import Any

from rest_framework import serializers

from apps.analyses import state
from apps.analyses.models import AnalysisJob, AnalysisProfile, AnalysisResult, ResultKind
from apps.analyses.pricing import estimate_job_cost


class CreateAnalysisSerializer(serializers.Serializer):
    upload_id = serializers.UUIDField()
    analysis_profile = serializers.ChoiceField(
        choices=AnalysisProfile.choices, default=AnalysisProfile.DEFAULT
    )


def job_status_payload(job: AnalysisJob) -> dict[str, Any]:
    return {
        "id": str(job.id),
        "upload_id": str(job.audio_upload_id),
        "analysis_profile": job.analysis_profile,
        "status": job.status,
        "current_stage": state.current_stage(job),
        "error": (
            {"code": job.error_code, "message": job.error_message, "stage": job.error_stage}
            if job.error_code
            else None
        ),
        "created_at": job.created_at,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
        "failed_at": job.failed_at,
    }


def _result_payload(result: AnalysisResult | None) -> dict[str, Any] | None:
    if result is None:
        return None
    template = result.prompt_template
    return {
        "analysis_type": result.analysis_type,
        "summary": result.summary,
        "structured_output": result.structured_output,
        "template": (
            {"slug": template.slug, "name": template.name, "version": template.version}
            if template
            else None
        ),
        "provenance": {
            "provider": result.provider,
            "model": result.model,
            "prompt_version": result.prompt_version,
            "latency_ms": result.latency_ms,
            "output_attempts": result.output_attempts,
            "usage": result.usage_metadata,
        },
    }


def job_result_payload(job: AnalysisJob) -> dict[str, Any]:
    """Expects `transcript`, `results` and `results__prompt_template` to be prefetched."""
    results = {result.kind: result for result in job.results.all()}
    transcript = job.transcript
    return {
        **job_status_payload(job),
        "transcript": {
            "text": transcript.text,
            "language": transcript.detected_language,
            "duration_seconds": transcript.duration_seconds,
            "provider": transcript.provider,
            "model": transcript.provider_model,
            "latency_ms": transcript.latency_ms,
        },
        "standard_analysis": _result_payload(results.get(ResultKind.STANDARD)),
        "custom_analysis": _result_payload(results.get(ResultKind.TEMPLATE)),
        "cost": estimate_job_cost(job),
    }
