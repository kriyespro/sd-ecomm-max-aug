from django.contrib import admin

from .models import BlogAuthor, BlogCategory, BlogPost, BlogTag


@admin.register(BlogCategory)
class BlogCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}
    search_fields = ("name",)


@admin.register(BlogTag)
class BlogTagAdmin(admin.ModelAdmin):
    list_display = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}
    search_fields = ("name",)


@admin.register(BlogAuthor)
class BlogAuthorAdmin(admin.ModelAdmin):
    list_display = ("name", "role")
    search_fields = ("name",)


@admin.register(BlogPost)
class BlogPostAdmin(admin.ModelAdmin):
    # Day-to-day authoring happens in Mission Control (/admin/blog/) — this is
    # here for platform-staff emergency edits / bulk fixes only.
    list_display = ("title", "category", "author", "is_published", "published_at")
    list_filter = ("is_published", "category")
    search_fields = ("title", "excerpt")
    autocomplete_fields = ("category", "author", "tags")
