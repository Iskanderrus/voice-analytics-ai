import pytest
from django.utils import timezone

from apps.analyses.models import AnalysisJob, JobStatus
from apps.common.errors import ErrorCode
from apps.uploads.models import UploadStatus
from tests.payloads import sales_template, standard

pytestmark = pytest.mark.django_db


def start(api, upload, **extra):
    return api.post("/api/v1/analyses", {"upload_id": str(upload.pk), **extra}, format="json")


def test_create_analysis_enqueues_and_returns_immediately(api, uploaded, celery_queue):
    response = start(api, uploaded)

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "QUEUED"
    assert body["current_stage"] == "queued"
    assert celery_queue.names() == ["transcribe_audio"]
    assert celery_queue.sent[0][1] == (body["id"],)


def test_incomplete_upload_is_rejected(api, uploaded, celery_queue):
    uploaded.status = UploadStatus.PENDING_UPLOAD
    uploaded.save()

    response = start(api, uploaded)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "UPLOAD_INCOMPLETE"
    assert not AnalysisJob.objects.exists()
    assert celery_queue.sent == []


def test_duplicate_requests_return_the_live_job(api, uploaded, celery_queue):
    first = start(api, uploaded)
    second = start(api, uploaded)

    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert AnalysisJob.objects.count() == 1
    assert celery_queue.names() == ["transcribe_audio"]
    # A different profile is a different analysis.
    assert start(api, uploaded, analysis_profile="standard_only").status_code == 202


def test_a_failed_analysis_can_be_requested_again(api, uploaded, make_job):
    failed = make_job(JobStatus.FAILED, error_code="LLM_INVALID_OUTPUT")

    response = start(api, uploaded)

    assert response.status_code == 202
    assert response.json()["id"] != str(failed.pk)


def test_status_and_result_while_processing(api, make_job):
    job = make_job(JobStatus.TRANSCRIBING)

    status = api.get(f"/api/v1/analyses/{job.pk}").json()
    result = api.get(f"/api/v1/analyses/{job.pk}/result")

    assert status["status"] == "TRANSCRIBING"
    assert status["current_stage"] == "transcription"
    assert status["completed_at"] is None
    assert result.status_code == 202
    assert result.json()["status"] == "TRANSCRIBING"


def test_result_of_failed_job(api, make_job):
    job = make_job(
        JobStatus.FAILED,
        error_code="TRANSCRIPTION_FAILED",
        error_message="No speech was detected in the audio.",
        error_stage="transcription",
    )

    response = api.get(f"/api/v1/analyses/{job.pk}/result")

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "ANALYSIS_FAILED"
    assert error["details"]["error"] == {
        "code": "TRANSCRIPTION_FAILED",
        "message": "No speech was detected in the audio.",
        "stage": "transcription",
    }


def test_analyses_are_private_to_the_upload_owner(api, other_api, client, make_job, uploaded):
    job = make_job(JobStatus.COMPLETED)

    assert client.get(f"/api/v1/analyses/{job.pk}").status_code == 401
    assert other_api.get(f"/api/v1/analyses/{job.pk}").status_code == 404
    assert other_api.get(f"/api/v1/analyses/{job.pk}/result").status_code == 404
    assert other_api.delete(f"/api/v1/analyses/{job.pk}").status_code == 404
    assert start(other_api, uploaded).json()["error"]["code"] == "UPLOAD_NOT_FOUND"


def test_full_pipeline_through_the_api(
    api, uploaded, celery_queue, transcriber, llm, django_assert_max_num_queries
):
    llm.outcomes = [standard(), sales_template()]
    job_id = start(api, uploaded).json()["id"]

    celery_queue.drain()

    with django_assert_max_num_queries(6):
        response = api.get(f"/api/v1/analyses/{job_id}/result")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "COMPLETED"
    assert body["current_stage"] == "done"
    assert body["transcript"]["text"] == "Hello, let us talk about pricing."
    assert body["standard_analysis"]["structured_output"]["sentiment"] == "neutral"
    assert body["standard_analysis"]["provenance"]["prompt_version"] == "1.0"
    custom = body["custom_analysis"]
    assert custom["template"] == {"slug": "sales-call", "name": "Sales Call Analysis", "version": 2}
    assert custom["structured_output"]["next_steps"] == ["Call next Tuesday"]
    # Fake providers have no price list entry.
    assert body["cost"]["total_usd"] is None
    assert {item["basis"] for item in body["cost"]["items"]} == {"unknown_price"}


def test_permanent_failure_is_visible_through_the_api(api, uploaded, celery_queue, transcriber):
    transcriber.outcomes = [""]  # silence
    job_id = start(api, uploaded).json()["id"]

    celery_queue.drain()

    body = api.get(f"/api/v1/analyses/{job_id}").json()
    assert body["status"] == "FAILED"
    assert body["current_stage"] == "transcription"
    assert body["error"]["code"] == ErrorCode.TRANSCRIPTION_FAILED


def test_clients_cannot_modify_job_state(api, make_job):
    job = make_job(JobStatus.QUEUED)

    for method in (api.put, api.patch):
        response = method(f"/api/v1/analyses/{job.pk}", {"status": "COMPLETED"}, format="json")
        assert response.status_code == 405
    job.refresh_from_db()
    assert job.status == JobStatus.QUEUED


def test_broker_outage_leaves_a_pollable_job_that_the_sweeper_requeues(
    api, uploaded, celery_queue, monkeypatch
):
    from datetime import timedelta

    from kombu.exceptions import OperationalError

    from apps.analyses import services

    def broker_down(*args, **kwargs):
        raise OperationalError("redis unavailable")

    with monkeypatch.context() as broken:
        broken.setattr(services, "enqueue_for_status", broker_down)
        response = start(api, uploaded)

    assert response.status_code == 202
    assert response.json()["status"] == "CREATED"

    requeued = services.requeue_stalled_jobs(older_than=timedelta(seconds=-1))

    assert requeued == [response.json()["id"]]
    assert celery_queue.names() == ["transcribe_audio"]


def test_analysis_creation_rechecks_stale_upload_after_delete(uploaded, celery_queue):
    from apps.analyses import services
    from apps.common.errors import DomainError

    stale = type(uploaded).objects.get(pk=uploaded.pk)
    type(uploaded).objects.filter(pk=uploaded.pk).update(
        status=UploadStatus.DELETED,
        deleted_at=timezone.now(),
    )

    with pytest.raises(DomainError) as exc:
        services.create_analysis(stale, "default")

    assert exc.value.code == ErrorCode.UPLOAD_NOT_FOUND
    assert not AnalysisJob.objects.exists()
    assert celery_queue.sent == []


def test_jobs_on_deleted_uploads_are_not_exposed(api, uploaded):
    job = AnalysisJob.objects.create(audio_upload=uploaded, status=JobStatus.CREATED)
    type(uploaded).objects.filter(pk=uploaded.pk).update(
        status=UploadStatus.DELETED,
        deleted_at=timezone.now(),
    )

    response = api.get(f"/api/v1/analyses/{job.pk}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == ErrorCode.ANALYSIS_NOT_FOUND
