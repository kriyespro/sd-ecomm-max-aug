from django.contrib import admin

from .models import InventoryItem, InventoryTransfer, StockMovement, Warehouse


@admin.register(Warehouse)
class WarehouseAdmin(admin.ModelAdmin):
    list_display = ("name", "project", "code", "is_active", "is_default", "city", "country")
    list_filter = ("project", "is_active", "is_default")
    search_fields = ("name", "code")


class StockMovementInline(admin.TabularInline):
    model = StockMovement
    extra = 0
    can_delete = False
    readonly_fields = (
        "reason", "quantity_delta", "reserved_delta",
        "quantity_after", "reserved_after", "reference", "note", "actor", "created_at",
    )

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(InventoryItem)
class InventoryItemAdmin(admin.ModelAdmin):
    list_display = ("__str__", "warehouse", "quantity", "reserved", "available", "low_stock_threshold", "is_low")
    list_filter = ("warehouse", "warehouse__project")
    search_fields = ("product__title", "product__sku", "variant__sku")
    inlines = [StockMovementInline]

    @admin.display(boolean=True)
    def is_low(self, obj):
        return obj.is_low

    def available(self, obj):
        return obj.available


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ("created_at", "item", "reason", "quantity_delta", "reserved_delta", "quantity_after", "actor")
    list_filter = ("reason",)
    search_fields = ("item__product__title", "reference")
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(InventoryTransfer)
class InventoryTransferAdmin(admin.ModelAdmin):
    list_display = ("__str__", "project", "product", "quantity", "status", "created_at")
    list_filter = ("project", "status")
