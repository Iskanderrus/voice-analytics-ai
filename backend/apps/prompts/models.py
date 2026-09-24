from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from pydantic import ValidationError as PydanticValidationError

from apps.prompts.filters import FilterConfig
from apps.prompts.output import OutputSpec

# These fields define the behaviour that produced an analysis result. Keeping
# them immutable makes stored prompt_version values meaningful months later.
CONTENT_FIELDS = (
    "slug",
    "version",
    "analysis_type",
    "priority",
    "system_instructions",
    "analysis_instructions",
    "filter_config",
    "output_schema",
)


class PromptTemplate(models.Model):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="prompt_templates",
    )
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=100)
    version = models.PositiveIntegerField(default=1)
    active = models.BooleanField(default=True)
    priority = models.IntegerField(default=0)
    analysis_type = models.CharField(max_length=50)
    system_instructions = models.TextField(blank=True)
    analysis_instructions = models.TextField()
    filter_config = models.JSONField(default=dict, blank=True)
    output_schema = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["slug", "version"],
                condition=models.Q(owner__isnull=True),
                name="prompt_builtin_slug_version_unique",
            ),
            models.UniqueConstraint(
                fields=["owner", "slug", "version"],
                condition=models.Q(owner__isnull=False),
                name="prompt_user_slug_version_unique",
            ),
            models.UniqueConstraint(
                fields=["slug"],
                condition=models.Q(owner__isnull=True, active=True),
                name="prompt_builtin_active_slug_unique",
            ),
            models.UniqueConstraint(
                fields=["owner", "slug"],
                condition=models.Q(owner__isnull=False, active=True),
                name="prompt_user_active_slug_unique",
            ),
        ]
        ordering = ["-priority", "slug", "-version"]

    def __str__(self) -> str:
        return f"{self.slug} v{self.version}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.full_clean()
        if self.pk is not None:
            stored = (
                PromptTemplate.objects.filter(pk=self.pk)
                .values("owner_id", *CONTENT_FIELDS)
                .first()
            )
            if stored is not None:
                changed = [
                    field for field in CONTENT_FIELDS if stored[field] != getattr(self, field)
                ]
                if stored["owner_id"] != self.owner_id:
                    changed.append("owner")
                if changed:
                    raise ValidationError(
                        f"Prompt template versions are immutable (changed: {', '.join(changed)}). "
                        "Create a new version instead."
                    )
        super().save(*args, **kwargs)

    def clean(self) -> None:
        super().clean()
        errors: dict[str, Any] = {}
        for field, schema in (("filter_config", FilterConfig), ("output_schema", OutputSpec)):
            try:
                schema.model_validate(getattr(self, field))
            except PydanticValidationError as exc:
                errors[field] = str(exc)

        # User-authored instructions deliberately stay below the system-message
        # trust boundary. Built-in templates may use this field because they are
        # shipped with the application rather than supplied through the API.
        if self.owner_id is not None and self.system_instructions.strip():
            errors["system_instructions"] = "User templates cannot define system instructions."

        if errors:
            raise ValidationError(errors)

    @property
    def filter(self) -> FilterConfig:
        return FilterConfig.model_validate(self.filter_config)

    @property
    def output_spec(self) -> OutputSpec:
        return OutputSpec.model_validate(self.output_schema)

    def new_version(self, **changes: Any) -> "PromptTemplate":
        with transaction.atomic():
            scope = PromptTemplate.objects.select_for_update().filter(
                owner_id=self.owner_id,
                slug=self.slug,
            )
            latest = scope.order_by("-version").first()
            next_version = (latest.version if latest else self.version) + 1
            scope.filter(active=True).update(active=False)

            values = {field: getattr(self, field) for field in CONTENT_FIELDS}
            values.update(
                owner_id=self.owner_id,
                name=self.name,
                priority=self.priority,
                active=True,
                version=next_version,
            )
            values.update(changes)
            values["owner_id"] = self.owner_id
            values["slug"] = self.slug
            values["version"] = next_version
            values["active"] = True
            return PromptTemplate.objects.create(**values)
