from typing import Any

from django.contrib import admin
from django.http import HttpRequest

from apps.prompts.models import CONTENT_FIELDS, PromptTemplate


@admin.register(PromptTemplate)
class PromptTemplateAdmin(admin.ModelAdmin):
    list_display = ["slug", "version", "name", "analysis_type", "active", "priority", "created_at"]
    list_filter = ["active", "analysis_type"]
    ordering = ["slug", "-version"]

    def get_readonly_fields(self, request: HttpRequest, obj: Any = None) -> list[str]:
        # Existing versions are immutable; edit by creating a new version.
        return list(CONTENT_FIELDS) if obj is not None else []

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False
