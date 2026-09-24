import pytest
from django.core.exceptions import ValidationError
from pydantic import ValidationError as PydanticValidationError

from apps.analyses.schemas import StandardAnalysis
from apps.prompts.filters import FilterConfig, matches
from apps.prompts.models import PromptTemplate
from apps.prompts.selection import select_template
from tests.payloads import SALES_STANDARD


def analysis(**overrides):
    return StandardAnalysis.model_validate({**SALES_STANDARD, **overrides})


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"topics_contains_any": ["PRICING"]},
        {"topics_contains_any": ["contract"]},  # substring of "contract terms"
        {"language": "EN", "sentiment_in": ["neutral", "negative"]},
        {"language_in": ["de", "en"], "min_speakers": 2, "has_action_items": True},
    ],
)
def test_matching_filters(config):
    assert matches(FilterConfig.model_validate(config), analysis())


@pytest.mark.parametrize(
    "config",
    [
        {"topics_contains_any": ["refund", "outage"]},
        {"language": "de"},
        {"sentiment_in": ["positive"]},
        {"min_speakers": 3},
        {"has_action_items": False},
        # Conditions are ANDed: one failing condition rejects the template.
        {"topics_contains_any": ["pricing"], "sentiment_in": ["negative"]},
    ],
)
def test_non_matching_filters(config):
    assert not matches(FilterConfig.model_validate(config), analysis())


@pytest.mark.parametrize(
    "config",
    [
        {"topics_contains": ["pricing"]},  # unknown key
        {"sentiment_in": ["furious"]},
        {"__import__": "os"},
        {"topics_contains_any": []},
    ],
)
def test_filter_vocabulary_is_closed(config):
    with pytest.raises(PydanticValidationError):
        FilterConfig.model_validate(config)


@pytest.mark.django_db
def test_seeded_templates_are_valid_and_selected_by_topic():
    assert select_template(analysis()).slug == "sales-call"
    assert select_template(analysis(topics=["login problem"])).slug == "support-call"
    assert select_template(analysis(topics=["weather"])) is None
    # A complaint about a subscription is a support call (sales-call v1 claimed it).
    complaint = analysis(topics=["subscription cancellation", "refund request"])
    assert select_template(complaint).slug == "support-call"


@pytest.mark.django_db
def test_multiple_matches_resolve_by_priority_then_slug():
    base = {
        "analysis_type": "generic",
        "analysis_instructions": "x",
        "output_schema": {"fields": {"notes": "n"}},
        "filter_config": {},
    }
    PromptTemplate.objects.create(name="B", slug="b-catch-all", priority=50, **base)
    PromptTemplate.objects.create(name="A", slug="a-catch-all", priority=50, **base)
    PromptTemplate.objects.create(name="Off", slug="off", priority=99, active=False, **base)

    # Both catch-alls outrank the seeded sales template (priority 20); slug breaks the tie.
    assert select_template(analysis()).slug == "a-catch-all"


@pytest.mark.django_db
def test_template_versions_are_immutable_and_evolve_by_new_version():
    template = PromptTemplate.objects.get(slug="sales-call", active=True)

    template.analysis_instructions = "Something else"
    with pytest.raises(ValidationError, match="immutable"):
        template.save()

    template.refresh_from_db()
    template.active = False  # operational flags stay editable
    template.save()
    template.active = True
    template.save()

    v3 = template.new_version(analysis_instructions="Extract objections only.")
    template.refresh_from_db()
    assert (v3.version, v3.active, template.active) == (3, True, False)
    assert select_template(analysis()).pk == v3.pk


@pytest.mark.django_db
def test_invalid_template_config_is_rejected_on_save():
    with pytest.raises(ValidationError):
        PromptTemplate.objects.create(
            name="Bad",
            slug="bad",
            analysis_type="bad",
            analysis_instructions="x",
            filter_config={"python": "__import__('os').system('id')"},
            output_schema={"fields": {"notes": "n"}},
        )
