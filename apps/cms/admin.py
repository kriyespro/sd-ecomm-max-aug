from django.contrib import admin

from .models import Banner, ContentBlock, FAQ, Menu, MenuItem, Page, Skin, ThemeSettings


@admin.register(Skin)
class SkinAdmin(admin.ModelAdmin):
    list_display = ["label", "slug", "source", "status", "is_active", "is_default", "project"]
    list_filter = ["source", "status", "is_active", "is_default"]
    search_fields = ["label", "slug"]
    raw_id_fields = ["project", "uploaded_by"]


@admin.register(Page)
class PageAdmin(admin.ModelAdmin):
    list_display = ["title", "project", "kind", "status", "published_at", "show_in_sitemap"]
    list_filter = ["project", "kind", "status"]
    search_fields = ["title", "slug", "body"]
    prepopulated_fields = {"slug": ["title"]}


@admin.register(ContentBlock)
class ContentBlockAdmin(admin.ModelAdmin):
    list_display = ["key", "project", "name", "block_type", "is_active"]
    list_filter = ["project", "block_type", "is_active"]
    search_fields = ["key", "name"]


@admin.register(Banner)
class BannerAdmin(admin.ModelAdmin):
    list_display = ["name", "project", "placement", "priority", "is_active", "starts_at", "ends_at"]
    list_filter = ["project", "placement", "is_active"]


@admin.register(FAQ)
class FAQAdmin(admin.ModelAdmin):
    list_display = ["question", "project", "group", "order", "is_active"]
    list_filter = ["project", "group", "is_active"]
    search_fields = ["question", "answer"]


class MenuItemInline(admin.TabularInline):
    model = MenuItem
    extra = 0
    fields = ["label", "link_type", "url", "page", "category", "parent", "order", "is_active"]
    raw_id_fields = ["parent", "page", "category"]


@admin.register(Menu)
class MenuAdmin(admin.ModelAdmin):
    list_display = ["name", "project", "location", "is_active"]
    list_filter = ["project", "location", "is_active"]
    inlines = [MenuItemInline]


@admin.register(ThemeSettings)
class ThemeSettingsAdmin(admin.ModelAdmin):
    list_display = ["project", "primary_color", "secondary_color", "accent_color"]
