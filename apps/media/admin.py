from django.contrib import admin

from .models import MediaAsset


@admin.register(MediaAsset)
class MediaAssetAdmin(admin.ModelAdmin):
    list_display = ["original_name", "project", "kind", "size", "width", "height", "created_at"]
    list_filter = ["project", "kind"]
    search_fields = ["original_name", "alt", "title", "folder"]
    readonly_fields = ["checksum", "size", "width", "height", "content_type", "thumbnails"]
