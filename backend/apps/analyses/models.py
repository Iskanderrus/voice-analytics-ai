import uuid

from django.db import models

from apps.prompts.models import PromptTemplate
from apps.uploads.models import AudioUpload


class JobStatus(models.TextChoices):
    CREATED = "CREATED"
    QUEUED = "QUEUED"
    TRANSCRIBING = "TRANSCRIBING"
    ANALYSING_STANDARD = "ANALYSING_STANDARD"
    ANALYSING_CUSTOM = "ANALYSING_CUSTOM"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class AnalysisProfile(models.TextChoices):
    DEFAULT = "default"
    STANDARD_ONLY = "standard_only"


class AnalysisJob(models.Model):
    """PostgreSQL owns processing state so broker redelivery can be reconciled safely."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    audio_upload = models.ForeignKey(
        AudioUpload, on_delete=models.CASCADE, related_name="analysis_jobs"
    )
    analysis_profile = models.CharField(
        max_length=30, choices=AnalysisProfile.choices, default=AnalysisProfile.DEFAULT
    )
    status = models.CharField(max_length=30, choices=JobStatus.choices, default=JobStatus.CREATED)
    # Attempts count actual lease acquisitions, not duplicate messages that arrive
    # while another worker still owns a live lease.
    stage_attempts = models.PositiveSmallIntegerField(default=0)
    stage_claim_id = models.CharField(max_length=64, null=True, blank=True)
    stage_claimed_at = models.DateTimeField(null=True, blank=True)
    selected_template = models.ForeignKey(
        PromptTemplate, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    error_code = models.CharField(max_length=50, null=True, blank=True)
    error_message = models.CharField(max_length=500, null=True, blank=True)
    error_stage = models.CharField(max_length=30, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    failed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["audio_upload", "analysis_profile"],
                condition=~models.Q(status="FAILED"),
                name="analysis_one_live_job_per_upload_profile",
            ),
        ]
        indexes = [models.Index(fields=["status", "updated_at"])]

    def __str__(self) -> str:
        return f"{self.id} ({self.status})"


class Transcript(models.Model):
    analysis_job = models.OneToOneField(
        AnalysisJob, on_delete=models.CASCADE, related_name="transcript"
    )
    text = models.TextField()
    detected_language = models.CharField(max_length=32, blank=True)
    duration_seconds = models.FloatField(null=True, blank=True)
    provider = models.CharField(max_length=50)
    provider_model = models.CharField(max_length=100)
    provider_metadata = models.JSONField(default=dict, blank=True)
    latency_ms = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"Transcript for {self.analysis_job_id}"


class ResultKind(models.TextChoices):
    STANDARD = "STANDARD"
    TEMPLATE = "TEMPLATE"


class AnalysisResult(models.Model):
    analysis_job = models.ForeignKey(AnalysisJob, on_delete=models.CASCADE, related_name="results")
    kind = models.CharField(max_length=20, choices=ResultKind.choices)
    analysis_type = models.CharField(max_length=50)
    prompt_template = models.ForeignKey(
        PromptTemplate, null=True, blank=True, on_delete=models.PROTECT, related_name="results"
    )
    prompt_version = models.CharField(max_length=120)
    provider = models.CharField(max_length=50)
    model = models.CharField(max_length=100)
    structured_output = models.JSONField()
    summary = models.TextField()
    usage_metadata = models.JSONField(default=dict, blank=True)
    output_attempts = models.PositiveSmallIntegerField(default=1)
    latency_ms = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["analysis_job", "kind"], name="result_one_per_kind"),
            models.CheckConstraint(
                condition=models.Q(kind="STANDARD", prompt_template__isnull=True)
                | models.Q(kind="TEMPLATE", prompt_template__isnull=False),
                name="result_template_matches_kind",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.analysis_type} result for {self.analysis_job_id}"
