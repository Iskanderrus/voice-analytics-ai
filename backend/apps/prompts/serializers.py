from typing import Any

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError
from rest_framework import serializers

from apps.prompts.filters import FilterConfig
from apps.prompts.models import PromptTemplate
from apps.prompts.output import OutputSpec


class PromptTemplateReadSerializer(serializers.ModelSerializer):
    scope = serializers.SerializerMethodField()

    class Meta:
        model = PromptTemplate
        fields = (
            "id",
            "name",
            "slug",
            "version",
            "active",
            "priority",
            "analysis_type",
            "analysis_instructions",
            "filter_config",
            "output_schema",
            "scope",
            "created_at",
        )

    def get_scope(self, obj: PromptTemplate) -> str:
        return "built_in" if obj.owner_id is None else "user"


def _validated_json(value: Any, schema: type[BaseModel]) -> Any:
    try:
        return schema.model_validate(value).model_dump(mode="json")
    except PydanticValidationError as exc:
        raise serializers.ValidationError(
            [error["msg"] for error in exc.errors(include_input=False, include_url=False)]
        ) from exc


class PromptTemplateWriteSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=200)
    slug = serializers.SlugField(max_length=100)
    priority = serializers.IntegerField(default=0)
    analysis_type = serializers.SlugField(max_length=50)
    analysis_instructions = serializers.CharField(max_length=4000)
    filter_config = serializers.JSONField(default=dict)
    output_schema = serializers.JSONField()
    system_instructions = serializers.CharField(required=False, write_only=True)

    def validate_system_instructions(self, value: str) -> str:
        raise serializers.ValidationError("System instructions are application-controlled.")

    def validate_filter_config(self, value: Any) -> dict[str, Any]:
        return _validated_json(value, FilterConfig)

    def validate_output_schema(self, value: Any) -> dict[str, Any]:
        return _validated_json(value, OutputSpec)

    def create(self, validated_data: dict[str, Any]) -> PromptTemplate:
        validated_data.pop("system_instructions", None)
        owner = self.context["request"].user
        if PromptTemplate.objects.filter(owner=owner, slug=validated_data["slug"]).exists():
            raise serializers.ValidationError(
                {"slug": "A template with this slug already exists. Create a new version instead."}
            )
        try:
            return PromptTemplate.objects.create(
                owner=owner,
                version=1,
                active=True,
                system_instructions="",
                **validated_data,
            )
        except (DjangoValidationError, IntegrityError) as exc:
            raise serializers.ValidationError(str(exc)) from exc


class PromptTemplateVersionSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=200, required=False)
    priority = serializers.IntegerField(required=False)
    analysis_type = serializers.SlugField(max_length=50, required=False)
    analysis_instructions = serializers.CharField(max_length=4000, required=False)
    filter_config = serializers.JSONField(required=False)
    output_schema = serializers.JSONField(required=False)
    system_instructions = serializers.CharField(required=False, write_only=True)

    def validate_system_instructions(self, value: str) -> str:
        raise serializers.ValidationError("System instructions are application-controlled.")

    def validate_filter_config(self, value: Any) -> dict[str, Any]:
        return _validated_json(value, FilterConfig)

    def validate_output_schema(self, value: Any) -> dict[str, Any]:
        return _validated_json(value, OutputSpec)
