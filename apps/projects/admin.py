from django.contrib import admin

from .models import Domain, Project


class DomainInline(admin.TabularInline):
    model = Domain
    extra = 0


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "status", "primary_domain", "currency", "created_at")
    list_filter = ("status", "currency", "country")
    search_fields = ("name", "slug", "primary_domain")
    prepopulated_fields = {"slug": ("name",)}
    inlines = [DomainInline]


@admin.register(Domain)
class DomainAdmin(admin.ModelAdmin):
    list_display = ("host", "project", "is_primary", "is_verified", "verified_at")
    list_filter = ("is_primary", "is_verified")
    search_fields = ("host",)
    readonly_fields = ("verification_token", "verified_at", "last_checked_at", "last_check_error")
