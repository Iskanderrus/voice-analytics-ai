from typing import Any

from django.db.models import QuerySet
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.api import request_user
from apps.common.errors import DomainError, ErrorCode
from apps.uploads import services
from apps.uploads.models import AudioUpload, UploadStatus
from apps.uploads.serializers import AudioUploadSerializer, CreateUploadSerializer


def _owned_uploads(request: Request) -> QuerySet[AudioUpload]:
    return AudioUpload.objects.filter(owner=request_user(request)).exclude(
        status=UploadStatus.DELETED
    )


def get_owned_upload(request: Request, upload_id: Any) -> AudioUpload:
    # Another user's upload is indistinguishable from a missing one.
    upload = _owned_uploads(request).filter(pk=upload_id).first()
    if upload is None:
        raise DomainError(ErrorCode.UPLOAD_NOT_FOUND, "Upload not found.", status_code=404)
    return upload


class UploadListView(APIView):
    def post(self, request: Request) -> Response:
        payload = CreateUploadSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        created = services.create_upload(request_user(request), **payload.validated_data)
        return Response(
            {
                "upload_id": str(created.upload.id),
                "object_key": created.upload.object_key,
                "upload_method": "POST",
                "upload_url": created.presigned.url,
                "upload_fields": created.presigned.fields,
                "expires_at": created.presigned.expires_at.isoformat(),
            },
            status=status.HTTP_201_CREATED,
        )


class UploadDetailView(APIView):
    def get(self, request: Request, upload_id: str) -> Response:
        return Response(AudioUploadSerializer(get_owned_upload(request, upload_id)).data)

    def delete(self, request: Request, upload_id: str) -> Response:
        services.delete_upload(get_owned_upload(request, upload_id))
        return Response(status=status.HTTP_204_NO_CONTENT)


class UploadCompleteView(APIView):
    def post(self, request: Request, upload_id: str) -> Response:
        upload = services.complete_upload(get_owned_upload(request, upload_id))
        return Response(AudioUploadSerializer(upload).data)
