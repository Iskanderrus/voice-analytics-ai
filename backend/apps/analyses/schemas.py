"""Validated shapes of AI output. Model output is only trusted after passing these."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Sentiment = Literal["positive", "neutral", "negative", "mixed", "unknown"]


class ActionItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    description: str = Field(min_length=1, max_length=500)
    owner: str | None = Field(default=None, max_length=200)


class StandardAnalysis(BaseModel):
    model_config = ConfigDict(extra="ignore")

    language: str | None = Field(
        default=None, max_length=16, description="ISO 639-1 code of the spoken language"
    )
    summary: str = Field(min_length=1, max_length=4000)
    topics: list[str] = Field(max_length=20, description="Short lowercase topic labels")
    sentiment: Sentiment
    speakers_count: int | None = Field(default=None, ge=1, le=100)
    action_items: list[ActionItem] = Field(max_length=50)
    key_points: list[str] = Field(max_length=30)
