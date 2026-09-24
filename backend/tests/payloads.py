import json
from typing import Any

SALES_STANDARD: dict[str, Any] = {
    "language": "en",
    "summary": "A prospect discussed pricing and a one-year contract.",
    "topics": ["pricing", "contract terms"],
    "sentiment": "neutral",
    "speakers_count": 2,
    "action_items": [{"description": "Send a quote", "owner": "Anna"}],
    "key_points": ["Budget approved for Q3"],
}

SALES_TEMPLATE: dict[str, Any] = {
    "summary": "Deal is progressing; quote requested.",
    "objections": ["Price is higher than the current vendor"],
    "buying_signals": ["Budget approved for Q3"],
    "competitors": ["Acme"],
    "commitments": ["Anna sends a quote"],
    "next_steps": ["Call next Tuesday"],
}


def standard(**overrides: Any) -> str:
    return json.dumps({**SALES_STANDARD, **overrides})


def sales_template(**overrides: Any) -> str:
    return json.dumps({**SALES_TEMPLATE, **overrides})
