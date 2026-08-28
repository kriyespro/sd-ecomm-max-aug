from django.contrib import admin

from .models import Wishlist, WishlistItem


class WishlistItemInline(admin.TabularInline):
    model = WishlistItem
    extra = 0
    autocomplete_fields = ["product", "variant"]


@admin.register(Wishlist)
class WishlistAdmin(admin.ModelAdmin):
    list_display = ["name", "project", "customer", "item_count", "is_public"]
    list_filter = ["project", "is_public"]
    inlines = [WishlistItemInline]
