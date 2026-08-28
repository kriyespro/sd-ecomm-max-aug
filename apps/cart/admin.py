from django.contrib import admin

from .models import Cart, CartItem


class CartItemInline(admin.TabularInline):
    model = CartItem
    extra = 0
    autocomplete_fields = ["product", "variant"]
    readonly_fields = ["line_total"]

    def line_total(self, obj):
        return obj.line_total if obj.pk else "-"


@admin.register(Cart)
class CartAdmin(admin.ModelAdmin):
    list_display = ["id", "project", "user", "session_key", "is_active", "item_count", "subtotal", "created_at"]
    list_filter = ["is_active", "project"]
    search_fields = ["session_key", "email", "user__username"]
    inlines = [CartItemInline]

    def item_count(self, obj):
        return obj.item_count

    def subtotal(self, obj):
        return obj.subtotal
