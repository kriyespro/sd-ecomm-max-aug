from django.contrib import admin

from .models import (
    Shipment,
    ShipmentEvent,
    ShipmentItem,
    ShippingMethod,
    ShippingZone,
)


class ShippingMethodInline(admin.TabularInline):
    model = ShippingMethod
    extra = 0
    fields = ["name", "carrier", "rate_type", "base_rate", "cod_available", "is_active", "priority"]


@admin.register(ShippingZone)
class ShippingZoneAdmin(admin.ModelAdmin):
    list_display = ["name", "project", "is_active", "priority"]
    list_filter = ["is_active", "project"]
    inlines = [ShippingMethodInline]


@admin.register(ShippingMethod)
class ShippingMethodAdmin(admin.ModelAdmin):
    list_display = ["name", "zone", "rate_type", "base_rate", "cod_available", "is_active", "priority"]
    list_filter = ["rate_type", "cod_available", "is_active", "project"]


class ShipmentItemInline(admin.TabularInline):
    model = ShipmentItem
    extra = 0
    raw_id_fields = ["order_item"]


class ShipmentEventInline(admin.TabularInline):
    model = ShipmentEvent
    extra = 0
    readonly_fields = ["status", "description", "location", "occurred_at", "created_at"]
    fields = readonly_fields
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Shipment)
class ShipmentAdmin(admin.ModelAdmin):
    list_display = ["id", "order", "carrier", "status", "tracking_number", "created_at"]
    list_filter = ["status", "carrier", "project"]
    search_fields = ["tracking_number", "order__number"]
    inlines = [ShipmentItemInline, ShipmentEventInline]


@admin.register(ShipmentEvent)
class ShipmentEventAdmin(admin.ModelAdmin):
    list_display = ["id", "shipment", "status", "location", "occurred_at"]
    list_filter = ["status"]

    def has_add_permission(self, request):
        return False
