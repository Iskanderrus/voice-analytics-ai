from typing import Any

from django.contrib import admin
from django.http import HttpRequest

from apps.analyses.models import AnalysisJob, AnalysisResult


@admin.register(AnalysisJob)
class AnalysisJobAdmin(admin.ModelAdmin):
    list_display = ["id", "status", "analysis_profile", "error_code", "created_at", "completed_at"]
    list_filter = ["status", "analysis_profile", "error_code"]
    list_select_related = ["audio_upload"]
    # State is owned by the pipeline; the admin is read-only.
    readonly_fields = [f.name for f in AnalysisJob._meta.fields]

    def has_add_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False


@admin.register(AnalysisResult)
class AnalysisResultAdmin(admin.ModelAdmin):
    list_display = ["analysis_job", "kind", "analysis_type", "provider", "model", "created_at"]
    list_filter = ["kind", "provider", "model"]
    readonly_fields = [f.name for f in AnalysisResult._meta.fields]
