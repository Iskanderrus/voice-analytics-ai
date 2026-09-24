from rest_framework import serializers

from apps.uploads.models import AudioUpload


class CreateUploadSerializer(serializers.Serializer):
    filename = serializers.CharField(max_length=1024)
    content_type = serializers.CharField(max_length=100)
    file_size = serializers.IntegerField(min_value=1)


class AudioUploadSerializer(serializers.ModelSerializer):
    class Meta:
        model = AudioUpload
        fields = [
            "id",
            "original_filename",
            "content_type",
            "declared_size",
            "stored_size",
            "checksum",
            "status",
            "created_at",
            "uploaded_at",
        ]
        read_only_fields = fields
