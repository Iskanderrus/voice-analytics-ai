from django.db.models import QuerySet
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.analyses import services
from apps.analyses.models import AnalysisJob, JobStatus
from apps.analyses.serializers import (
    CreateAnalysisSerializer,
    job_result_payload,
    job_status_payload,
)
from apps.common.api import error_response, request_user
from apps.common.errors import DomainError, ErrorCode
from apps.uploads.views import get_owned_upload


def _owned_jobs(request: Request) -> QuerySet[AnalysisJob]:
    return AnalysisJob.objects.filter(audio_upload__owner=request_user(request))


def _get_owned_job(queryset: QuerySet[AnalysisJob], job_id: str) -> AnalysisJob:
    job = queryset.filter(pk=job_id).first()
    if job is None:
        raise DomainError(ErrorCode.ANALYSIS_NOT_FOUND, "Analysis not found.", status_code=404)
    return job


class AnalysisListView(APIView):
    def post(self, request: Request) -> Response:
        payload = CreateAnalysisSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        upload = get_owned_upload(request, payload.validated_data["upload_id"])
        outcome = services.create_analysis(upload, payload.validated_data["analysis_profile"])
        return Response(
            job_status_payload(outcome.job),
            status=status.HTTP_202_ACCEPTED if outcome.created else status.HTTP_200_OK,
        )


class AnalysisDetailView(APIView):
    def get(self, request: Request, job_id: str) -> Response:
        return Response(job_status_payload(_get_owned_job(_owned_jobs(request), job_id)))

    def delete(self, request: Request, job_id: str) -> Response:
        services.delete_analysis(_get_owned_job(_owned_jobs(request), job_id))
        return Response(status=status.HTTP_204_NO_CONTENT)


class AnalysisResultView(APIView):
    def get(self, request: Request, job_id: str) -> Response:
        queryset = (
            _owned_jobs(request)
            .select_related("transcript")
            .prefetch_related("results__prompt_template")
        )
        job = _get_owned_job(queryset, job_id)
        if job.status == JobStatus.COMPLETED:
            return Response(job_result_payload(job))
        if job.status == JobStatus.FAILED:
            return error_response(
                ErrorCode.ANALYSIS_FAILED,
                job.error_message or "Analysis failed.",
                status.HTTP_409_CONFLICT,
                details=job_status_payload(job),
            )
        # Still processing: 202 with the current status, the client keeps polling.
        return Response(job_status_payload(job), status=status.HTTP_202_ACCEPTED)
