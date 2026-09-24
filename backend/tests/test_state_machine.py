import pytest

from apps.analyses import state
from apps.analyses.models import AnalysisJob, JobStatus

S = JobStatus

HAPPY_PATH = [
    (S.CREATED, S.QUEUED),
    (S.QUEUED, S.TRANSCRIBING),
    (S.TRANSCRIBING, S.ANALYSING_STANDARD),
    (S.ANALYSING_STANDARD, S.ANALYSING_CUSTOM),
    (S.ANALYSING_CUSTOM, S.COMPLETED),
    (S.ANALYSING_STANDARD, S.COMPLETED),
    (S.CREATED, S.TRANSCRIBING),
]


@pytest.mark.parametrize(("source", "target"), HAPPY_PATH)
def test_valid_transitions(source, target):
    job = AnalysisJob(
        status=source,
        stage_attempts=2,
        stage_claim_id="delivery-1",
        stage_claimed_at="2026-09-24T12:00:00Z",
    )

    state.transition(job, target)

    assert job.status == target
    assert job.stage_attempts == 0
    assert job.stage_claim_id is None
    assert job.stage_claimed_at is None


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (S.QUEUED, S.COMPLETED),
        (S.TRANSCRIBING, S.ANALYSING_CUSTOM),
        (S.ANALYSING_CUSTOM, S.TRANSCRIBING),
        (S.COMPLETED, S.FAILED),
        (S.FAILED, S.QUEUED),
        (S.COMPLETED, S.TRANSCRIBING),
    ],
)
def test_invalid_transitions_are_rejected(source, target):
    job = AnalysisJob(status=source)

    with pytest.raises(state.InvalidTransition):
        state.transition(job, target)
    assert job.status == source


@pytest.mark.parametrize("source", [s for s in S if s not in state.TERMINAL])
def test_every_non_terminal_state_can_fail_and_records_where(source):
    job = AnalysisJob(
        status=source,
        stage_attempts=3,
        stage_claim_id="delivery-1",
        stage_claimed_at="2026-09-24T12:00:00Z",
    )

    state.transition(job, S.FAILED, error_code="LLM_INVALID_OUTPUT", error_message="bad")

    assert job.status == S.FAILED
    assert job.error_code == "LLM_INVALID_OUTPUT"
    assert job.failed_at is not None
    assert job.stage_attempts == 3
    assert job.stage_claim_id is None
    assert job.stage_claimed_at is None
    assert state.current_stage(job) == state.STAGE_NAMES[source]
