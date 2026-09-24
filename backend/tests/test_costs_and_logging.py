import json
import logging
from decimal import Decimal

from apps.analyses.models import AnalysisResult, Transcript
from apps.analyses.pricing import cost_basis, estimate_analysis_cost, estimate_transcription_cost
from apps.common.logging import ContextFilter, JsonFormatter, log_context


def test_cloud_llm_cost_is_estimated_from_tokens():
    result = AnalysisResult(
        provider="openai",
        model="gpt-4o-mini",
        usage_metadata={"input_tokens": 10_000, "output_tokens": 2_000},
    )

    # 10k * 0.15/M + 2k * 0.60/M
    assert estimate_analysis_cost(result) == Decimal("0.002700")


def test_cloud_transcription_cost_is_estimated_from_duration():
    transcript = Transcript(provider="openai", provider_model="whisper-1", duration_seconds=90)

    assert estimate_transcription_cost(transcript) == Decimal("0.009000")


def test_local_inference_has_no_price_and_is_labelled_local_compute():
    result = AnalysisResult(
        provider="ollama", model="qwen2.5:3b-instruct", usage_metadata={"input_tokens": 1}
    )

    assert estimate_analysis_cost(result) is None
    assert cost_basis("ollama", None) == "local_compute"
    assert cost_basis("openai", None) == "unknown_price"


def test_logs_are_json_with_context_and_redact_sensitive_fields():
    record = logging.makeLogRecord(
        {"msg": "stage completed", "levelname": "INFO", "transcript": "private words"}
    )
    with log_context(job_id="j-1", stage="transcription"):
        ContextFilter().filter(record)

    line = json.loads(JsonFormatter().format(record))

    assert line["job_id"] == "j-1"
    assert line["stage"] == "transcription"
    assert line["transcript"] == "[REDACTED]"
    assert "private words" not in json.dumps(line)
