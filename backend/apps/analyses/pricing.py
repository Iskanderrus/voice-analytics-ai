"""AI cost estimation. The only module that knows provider prices.

Prices are list prices in USD and change over time; they are estimates for
dashboards and per-job accounting, not billing. Local providers run on our own
compute and are reported as `local_compute` with no per-call price.
"""

from dataclasses import dataclass
from decimal import Decimal

from apps.analyses.models import AnalysisJob, AnalysisResult, Transcript

LOCAL_PROVIDERS = frozenset({"ollama", "whisper_local"})


@dataclass(frozen=True)
class TokenPrice:
    input_per_million: Decimal
    output_per_million: Decimal


@dataclass(frozen=True)
class AudioPrice:
    per_minute: Decimal


LLM_PRICES: dict[tuple[str, str], TokenPrice] = {
    ("openai", "gpt-4o-mini"): TokenPrice(Decimal("0.15"), Decimal("0.60")),
    ("openai", "gpt-4.1-mini"): TokenPrice(Decimal("0.40"), Decimal("1.60")),
    ("openai", "gpt-4.1-nano"): TokenPrice(Decimal("0.10"), Decimal("0.40")),
}

STT_PRICES: dict[tuple[str, str], AudioPrice] = {
    ("openai", "whisper-1"): AudioPrice(Decimal("0.006")),
    ("openai", "gpt-4o-transcribe"): AudioPrice(Decimal("0.006")),
    ("openai", "gpt-4o-mini-transcribe"): AudioPrice(Decimal("0.003")),
}

_QUANT = Decimal("0.000001")


def estimate_analysis_cost(result: AnalysisResult) -> Decimal | None:
    price = LLM_PRICES.get((result.provider, result.model))
    usage = result.usage_metadata or {}
    input_tokens, output_tokens = usage.get("input_tokens"), usage.get("output_tokens")
    if price is None or input_tokens is None or output_tokens is None:
        return None
    cost = (
        Decimal(input_tokens) * price.input_per_million
        + Decimal(output_tokens) * price.output_per_million
    ) / Decimal(1_000_000)
    return cost.quantize(_QUANT)


def estimate_transcription_cost(transcript: Transcript) -> Decimal | None:
    price = STT_PRICES.get((transcript.provider, transcript.provider_model))
    if price is None or transcript.duration_seconds is None:
        return None
    minutes = Decimal(str(transcript.duration_seconds)) / Decimal(60)
    return (minutes * price.per_minute).quantize(_QUANT)


def cost_basis(provider: str, estimate: Decimal | None) -> str:
    if provider in LOCAL_PROVIDERS:
        return "local_compute"
    return "estimated" if estimate is not None else "unknown_price"


def estimate_job_cost(job: AnalysisJob) -> dict[str, object]:
    """Per-component breakdown; `total_usd` is None unless every component is priced."""
    items: list[dict[str, object]] = []
    transcript = getattr(job, "transcript", None)
    if transcript is not None:
        estimate = estimate_transcription_cost(transcript)
        items.append(
            {
                "component": "transcription",
                "provider": transcript.provider,
                "model": transcript.provider_model,
                "usd": estimate,
                "basis": cost_basis(transcript.provider, estimate),
            }
        )
    for result in job.results.all():
        estimate = estimate_analysis_cost(result)
        items.append(
            {
                "component": result.analysis_type,
                "provider": result.provider,
                "model": result.model,
                "usd": estimate,
                "basis": cost_basis(result.provider, estimate),
            }
        )
    priced = [item["usd"] for item in items if isinstance(item["usd"], Decimal)]
    total = sum(priced, Decimal(0)) if items and len(priced) == len(items) else None
    return {"total_usd": total, "items": items}
