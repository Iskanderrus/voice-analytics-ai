"""A closed filter vocabulary keeps routing inspectable and avoids executable user rules."""

from pydantic import BaseModel, ConfigDict, Field

from apps.analyses.schemas import Sentiment, StandardAnalysis


class FilterConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    language: str | None = Field(default=None, min_length=2, max_length=16)
    language_in: list[str] | None = Field(default=None, min_length=1)
    sentiment_in: list[Sentiment] | None = Field(default=None, min_length=1)
    topics_contains_any: list[str] | None = Field(default=None, min_length=1)
    min_speakers: int | None = Field(default=None, ge=1)
    has_action_items: bool | None = None


def _lower(values: list[str]) -> set[str]:
    return {value.strip().lower() for value in values}


def matches(config: FilterConfig, analysis: StandardAnalysis) -> bool:
    language = (analysis.language or "").lower()
    if config.language is not None and language != config.language.lower():
        return False
    if config.language_in is not None and language not in _lower(config.language_in):
        return False
    if config.sentiment_in is not None and analysis.sentiment not in config.sentiment_in:
        return False
    if config.topics_contains_any is not None:
        topics = _lower(analysis.topics)
        terms = _lower(config.topics_contains_any)
        if not any(term in topic for term in terms for topic in topics):
            return False
    if config.min_speakers is not None and (analysis.speakers_count or 0) < config.min_speakers:
        return False
    return config.has_action_items is None or (
        bool(analysis.action_items) == config.has_action_items
    )
