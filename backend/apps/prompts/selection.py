import logging

from pydantic import ValidationError

from apps.analyses.schemas import StandardAnalysis
from apps.prompts.filters import matches
from apps.prompts.models import PromptTemplate

logger = logging.getLogger(__name__)


def _first_match(queryset, analysis: StandardAnalysis) -> PromptTemplate | None:
    for template in queryset.order_by("-priority", "slug"):
        try:
            config = template.filter
        except ValidationError:
            # Model validation normally prevents this. Keeping the guard means a
            # bad row inserted outside Django cannot break every analysis job.
            logger.error("template has invalid filter_config", extra={"template": str(template)})
            continue
        if matches(config, analysis):
            return template
    return None


def select_template(
    analysis: StandardAnalysis,
    *,
    owner_id: int | None = None,
) -> PromptTemplate | None:
    active = PromptTemplate.objects.filter(active=True)

    # A user's own rules should be able to override the built-in classification
    # without changing or copying application-owned templates.
    if owner_id is not None:
        selected = _first_match(active.filter(owner_id=owner_id), analysis)
        if selected is not None:
            return selected

    return _first_match(active.filter(owner__isnull=True), analysis)
