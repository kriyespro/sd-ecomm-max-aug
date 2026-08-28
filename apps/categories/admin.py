from django.contrib import admin

from .models import Category


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "project", "parent", "order", "is_active", "is_featured")
    list_filter = ("project", "is_active", "is_featured")
    search_fields = ("name", "slug")
    autocomplete_fields = ("parent",)
