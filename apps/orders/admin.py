from django.contrib import admin

from .models import Order, OrderItem, OrderStatusEvent


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    autocomplete_fields = ["product", "variant"]
    readonly_fields = ["product_title", "variant_name", "sku", "unit_price", "line_total", "fulfilled_quantity"]
    can_delete = False


class OrderStatusEventInline(admin.TabularInline):
    model = OrderStatusEvent
    extra = 0
    readonly_fields = ["kind", "from_value", "to_value", "note", "actor", "created_at"]
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = [
        "number", "project", "status", "payment_status", "fulfillment_status",
        "grand_total", "email", "created_at",
    ]
    list_filter = ["status", "payment_status", "fulfillment_status", "project"]
    search_fields = ["number", "email", "phone", "tracking_number"]
    readonly_fields = [
        "number", "subtotal", "grand_total", "stock_reserved", "placed_at",
        "created_at", "updated_at",
    ]
    inlines = [OrderItemInline, OrderStatusEventInline]
    date_hierarchy = "created_at"


@admin.register(OrderStatusEvent)
class OrderStatusEventAdmin(admin.ModelAdmin):
    list_display = ["order", "kind", "from_value", "to_value", "actor", "created_at"]
    list_filter = ["kind"]
    search_fields = ["order__number"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
