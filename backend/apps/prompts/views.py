from django.db.models import Q
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.api import request_user
from apps.common.errors import DomainError, ErrorCode
from apps.prompts.models import PromptTemplate
from apps.prompts.serializers import (
    PromptTemplateReadSerializer,
    PromptTemplateVersionSerializer,
    PromptTemplateWriteSerializer,
)


def _visible_templates(request: Request):
    user = request_user(request)
    return PromptTemplate.objects.filter(Q(owner=user) | Q(owner__isnull=True))


def _get_visible_template(request: Request, template_id: int) -> PromptTemplate:
    template = _visible_templates(request).filter(pk=template_id).first()
    if template is None:
        raise DomainError(
            ErrorCode.PROMPT_TEMPLATE_NOT_FOUND, "Prompt template not found.", status_code=404
        )
    return template


class PromptTemplateListView(APIView):
    def get(self, request: Request) -> Response:
        templates = (
            _visible_templates(request)
            .filter(active=True)
            .order_by("-owner_id", "-priority", "slug")
        )
        return Response(PromptTemplateReadSerializer(templates, many=True).data)

    def post(self, request: Request) -> Response:
        serializer = PromptTemplateWriteSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        template = serializer.save()
        return Response(PromptTemplateReadSerializer(template).data, status=status.HTTP_201_CREATED)


class PromptTemplateDetailView(APIView):
    def get(self, request: Request, template_id: int) -> Response:
        return Response(
            PromptTemplateReadSerializer(_get_visible_template(request, template_id)).data
        )

    def delete(self, request: Request, template_id: int) -> Response:
        template = _get_visible_template(request, template_id)
        if template.owner_id != request_user(request).id:
            raise DomainError(
                ErrorCode.PERMISSION_DENIED,
                "Built-in prompt templates are read-only.",
                status_code=403,
            )
        if template.active:
            template.active = False
            template.save(update_fields=["active"])
        return Response(status=status.HTTP_204_NO_CONTENT)


class PromptTemplateVersionsView(APIView):
    def get(self, request: Request, template_id: int) -> Response:
        template = _get_visible_template(request, template_id)
        versions = PromptTemplate.objects.filter(
            owner_id=template.owner_id,
            slug=template.slug,
        ).order_by("-version")
        return Response(PromptTemplateReadSerializer(versions, many=True).data)

    def post(self, request: Request, template_id: int) -> Response:
        template = _get_visible_template(request, template_id)
        if template.owner_id != request_user(request).id:
            raise DomainError(
                ErrorCode.PERMISSION_DENIED,
                "Built-in prompt templates are read-only.",
                status_code=403,
            )
        serializer = PromptTemplateVersionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        new_version = template.new_version(**serializer.validated_data)
        return Response(
            PromptTemplateReadSerializer(new_version).data,
            status=status.HTTP_201_CREATED,
        )
