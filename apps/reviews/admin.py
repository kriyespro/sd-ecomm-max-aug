from django.contrib import admin

from .models import Review, ReviewVote


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ["product", "rating", "author_name", "status", "is_verified_purchase", "helpful_count", "created_at"]
    list_filter = ["project", "status", "rating", "is_verified_purchase"]
    search_fields = ["author_name", "author_email", "title", "body", "product__title"]
    readonly_fields = ["is_verified_purchase", "helpful_count", "moderated_by", "moderated_at"]


@admin.register(ReviewVote)
class ReviewVoteAdmin(admin.ModelAdmin):
    list_display = ["review", "voter_email", "is_helpful", "created_at"]
    list_filter = ["is_helpful"]
