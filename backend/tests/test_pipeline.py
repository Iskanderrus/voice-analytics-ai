"""Stage behaviour under retries, duplicate delivery and failures.

Tasks are executed through Celery's `apply` (as a worker would run them) with
fake providers; follow-up tasks land in `celery_queue`.
"""

import pytest

from apps.analyses import tasks
from apps.analyses.models import AnalysisJob, AnalysisResult, JobStatus, ResultKind, Transcript
from apps.common.errors import ErrorCode, permanent, retryable
from tests.payloads import sales_template, standard

pytestmark = pytest.mark.django_db
S = JobStatus


def run(task, job):
    task.apply(args=(str(job.pk),))
    job.refresh_from_db()
    return job


def with_transcript(job, text="We discussed pricing."):
    Transcript.objects.create(
        analysis_job=job, text=text, provider="fake", provider_model="m", latency_ms=1
    )
    return job


# --- transcription -------------------------------------------------------------------


def test_transcription_persists_transcript_and_hands_over(make_job, transcriber, celery_queue):
    job = run(tasks.transcribe_audio, make_job(S.QUEUED))

    assert job.status == S.ANALYSING_STANDARD
    assert job.started_at is not None
    transcript = job.transcript
    assert transcript.text == "Hello, let us talk about pricing."
    assert (transcript.provider, transcript.provider_model) == ("fake_stt", "fake-stt-1")
    assert transcript.duration_seconds == 12.5
    assert celery_queue.names() == ["run_standard_analysis"]


def test_duplicate_transcription_delivery_is_a_noop(make_job, transcriber, celery_queue):
    job = run(tasks.transcribe_audio, make_job(S.QUEUED))
    celery_queue.sent.clear()

    run(tasks.transcribe_audio, job)

    assert transcriber.calls == 1
    assert Transcript.objects.filter(analysis_job=job).count() == 1
    # The redelivery re-publishes the next stage: covers a worker that died
    # after committing but before enqueueing. The next stage is idempotent too.
    assert celery_queue.names() == ["run_standard_analysis"]


def test_output_of_a_concurrent_duplicate_is_discarded(make_job, transcriber):
    job = make_job(S.QUEUED)

    def other_worker_finishes_first():
        AnalysisJob.objects.filter(pk=job.pk).update(status=S.ANALYSING_STANDARD)
        with_transcript(job, text="first writer wins")

    transcriber.on_call = other_worker_finishes_first

    job = run(tasks.transcribe_audio, job)

    assert job.status == S.ANALYSING_STANDARD
    assert list(Transcript.objects.values_list("text", flat=True)) == ["first writer wins"]


def test_transient_failure_is_retried_then_succeeds(make_job, transcriber):
    transcriber.outcomes = [
        retryable(ErrorCode.TRANSCRIPTION_FAILED, "provider hiccup"),
        "Recovered transcript.",
    ]

    job = run(tasks.transcribe_audio, make_job(S.QUEUED))

    assert transcriber.calls == 2
    assert job.status == S.ANALYSING_STANDARD
    assert job.transcript.text == "Recovered transcript."


def test_retries_are_bounded_then_the_job_fails(make_job, transcriber, settings):
    settings.PIPELINE_MAX_STAGE_ATTEMPTS = 3
    transcriber.outcomes = [retryable(ErrorCode.TRANSCRIPTION_TIMEOUT, "slow")]

    job = run(tasks.transcribe_audio, make_job(S.QUEUED))

    assert transcriber.calls == 3
    assert job.status == S.FAILED
    assert job.error_code == ErrorCode.TRANSCRIPTION_TIMEOUT
    assert job.error_stage == "transcription"


def test_permanent_failure_fails_immediately(make_job, transcriber, celery_queue):
    transcriber.outcomes = [permanent(ErrorCode.UNSUPPORTED_AUDIO, "Audio could not be decoded.")]

    job = run(tasks.transcribe_audio, make_job(S.QUEUED))

    assert transcriber.calls == 1
    assert (job.status, job.error_code) == (S.FAILED, ErrorCode.UNSUPPORTED_AUDIO)
    assert job.error_message == "Audio could not be decoded."
    assert celery_queue.sent == []


def test_redelivered_message_that_keeps_crashing_the_worker_is_capped(
    make_job, transcriber, settings
):
    # E.g. OOM-killed worker: the broker redelivers, the DB counter still grows.
    settings.PIPELINE_MAX_STAGE_ATTEMPTS = 2
    job = make_job(S.TRANSCRIBING, stage_attempts=2)

    job = run(tasks.transcribe_audio, job)

    assert transcriber.calls == 0
    assert (job.status, job.error_code) == (S.FAILED, ErrorCode.STAGE_ATTEMPTS_EXHAUSTED)


def test_unexpected_error_fails_job_with_safe_message(make_job, transcriber):
    transcriber.outcomes = [KeyError("secret-internal-detail")]

    job = run(tasks.transcribe_audio, make_job(S.QUEUED))

    assert job.status == S.FAILED
    assert job.error_code == ErrorCode.INTERNAL_PROCESSING_ERROR
    assert "secret" not in job.error_message


def test_stage_for_a_deleted_job_is_dropped(make_job, transcriber):
    job = make_job(S.QUEUED)
    job_id = str(job.pk)
    job.delete()

    tasks.transcribe_audio.apply(args=(job_id,))

    assert transcriber.calls == 0


# --- standard analysis ------------------------------------------------------------


def test_standard_analysis_validates_persists_and_selects_template(make_job, llm, celery_queue):
    llm.outcomes = [standard()]
    job = run(tasks.run_standard_analysis, with_transcript(make_job(S.ANALYSING_STANDARD)))

    assert job.status == S.ANALYSING_CUSTOM
    assert job.selected_template.slug == "sales-call"
    result = job.results.get()
    assert result.kind == ResultKind.STANDARD
    assert result.structured_output["topics"] == ["pricing", "contract terms"]
    assert (result.provider, result.model, result.prompt_version) == (
        "fake_llm",
        "fake-llm-1",
        "1.0",
    )
    assert result.usage_metadata["input_tokens"] == 100
    assert celery_queue.names() == ["run_custom_analysis"]


def test_no_matching_template_completes_after_standard(make_job, llm, celery_queue):
    llm.outcomes = [standard(topics=["weather", "holidays"])]
    job = run(tasks.run_standard_analysis, with_transcript(make_job(S.ANALYSING_STANDARD)))

    assert job.status == S.COMPLETED
    assert job.selected_template is None
    assert celery_queue.sent == []


def test_standard_only_profile_skips_template_selection(make_job, llm):
    llm.outcomes = [standard()]
    job = make_job(S.ANALYSING_STANDARD, analysis_profile="standard_only")
    job = run(tasks.run_standard_analysis, with_transcript(job))

    assert job.status == S.COMPLETED
    assert job.selected_template is None


@pytest.mark.parametrize(
    "invalid",
    [
        "Sure! Here is the analysis: {not json",
        standard(sentiment="ecstatic"),
        '{"summary": "missing everything else"}',
    ],
    ids=["invalid-json", "invalid-enum", "missing-fields"],
)
def test_invalid_output_is_retried_with_feedback(make_job, llm, invalid):
    llm.outcomes = [invalid, f"```json\n{standard()}\n```"]
    job = run(tasks.run_standard_analysis, with_transcript(make_job(S.ANALYSING_STANDARD)))

    assert job.status == S.ANALYSING_CUSTOM
    assert job.results.get().output_attempts == 2
    feedback = llm.requests[1].messages[-1]
    assert feedback.role == "user"
    assert "not valid" in feedback.content
    assert "pricing" not in feedback.content  # errors are reported without input values


def test_invalid_output_exhaustion_fails_the_job(make_job, llm, settings):
    settings.LLM_MAX_OUTPUT_ATTEMPTS = 2
    llm.outcomes = ['{"summary": ""}']
    job = run(tasks.run_standard_analysis, with_transcript(make_job(S.ANALYSING_STANDARD)))

    assert len(llm.requests) == 2
    assert (job.status, job.error_code) == (S.FAILED, ErrorCode.LLM_INVALID_OUTPUT)
    assert not AnalysisResult.objects.exists()


def test_rate_limit_is_retried(make_job, llm):
    llm.outcomes = [retryable(ErrorCode.LLM_RATE_LIMITED, "429"), standard()]
    job = run(tasks.run_standard_analysis, with_transcript(make_job(S.ANALYSING_STANDARD)))

    assert job.status == S.ANALYSING_CUSTOM
    assert job.results.count() == 1


# --- custom (template) analysis ------------------------------------------------------


@pytest.fixture
def custom_ready_job(make_job, llm):
    llm.outcomes = [standard()]
    job = run(tasks.run_standard_analysis, with_transcript(make_job(S.ANALYSING_STANDARD)))
    llm.requests.clear()
    return job


def test_custom_analysis_runs_selected_template(custom_ready_job, llm):
    llm.outcomes = [sales_template()]
    job = run(tasks.run_custom_analysis, custom_ready_job)

    assert job.status == S.COMPLETED
    assert job.completed_at is not None
    result = job.results.get(kind=ResultKind.TEMPLATE)
    assert result.prompt_template.slug == "sales-call"
    assert result.prompt_version == "sales-call@2"
    assert result.structured_output["competitors"] == ["Acme"]
    assert result.summary == "Deal is progressing; quote requested."


def test_custom_analysis_reexecution_is_idempotent(custom_ready_job, llm):
    llm.outcomes = [sales_template()]
    run(tasks.run_custom_analysis, custom_ready_job)
    job = run(tasks.run_custom_analysis, custom_ready_job)

    assert len(llm.requests) == 1
    assert job.results.filter(kind=ResultKind.TEMPLATE).count() == 1
    assert job.status == S.COMPLETED
