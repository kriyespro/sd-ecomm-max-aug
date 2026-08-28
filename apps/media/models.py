"""Media library (project.md section 23).

Project-scoped assets with checksum de-duplication and, for images, generated
thumbnails. Storage is Django's configured backend (local volume in dev, S3 /
R2 in prod via STORAGES) — this app never talks to a storage SDK directly.
"""

from django.conf import settings
from django.db import models

from apps.core.models import TenantScopedModel


class AssetKind(models.TextChoices):
    IMAGE = "image", "Image"
    VIDEO = "video", "Video"
    DOCUMENT = "document", "Document"
    OTHER = "other", "Other"


class MediaAsset(TenantScopedModel):
    file = models.FileField(upload_to="media_library/%Y/%m/")
    kind = models.CharField(max_length=12, choices=AssetKind.choices, default=AssetKind.OTHER)
    original_name = models.CharField(max_length=255, blank=True)
    content_type = models.CharField(max_length=100, blank=True)
    size = models.PositiveIntegerField(default=0)
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    checksum = models.CharField(max_length=64, db_index=True, blank=True)

    folder = models.CharField(max_length=200, blank=True)
    alt = models.CharField(max_length=255, blank=True)
    title = models.CharField(max_length=255, blank=True)
    thumbnails = models.JSONField(default=dict, blank=True)

    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="media_uploads",
    )

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["project", "checksum"], name="uniq_media_checksum",
                                    condition=~models.Q(checksum="")),
        ]

    def __str__(self):
        return self.original_name or self.file.name

    @property
    def url(self):
        return self.file.url if self.file else ""
