from django.contrib import admin

from .models import Redirect, SeoMeta, SeoSettings


@admin.register(SeoSettings)
class SeoSettingsAdmin(admin.ModelAdmin):
    list_display = ["project", "title_suffix", "default_robots", "sitemap_enabled"]


@admin.register(SeoMeta)
class SeoMetaAdmin(admin.ModelAdmin):
    list_display = ["path", "project", "title", "robots"]
    list_filter = ["project"]
    search_fields = ["path", "title"]


@admin.register(Redirect)
class RedirectAdmin(admin.ModelAdmin):
    list_display = ["from_path", "to_path", "project", "is_permanent", "is_active", "hits"]
    list_filter = ["project", "is_permanent", "is_active"]
    search_fields = ["from_path", "to_path"]
