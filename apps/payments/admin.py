from django.contrib import admin

from .models import Payment, PaymentEvent, PaymentProviderConfig, Refund


class RefundInline(admin.TabularInline):
    model = Refund
    extra = 0
    readonly_fields = ["amount", "status", "provider_refund_id", "reason", "actor", "created_at"]
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


class PaymentEventInline(admin.TabularInline):
    model = PaymentEvent
    extra = 0
    readonly_fields = ["kind", "provider", "signature_valid", "note", "created_at"]
    fields = readonly_fields
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(PaymentProviderConfig)
class PaymentProviderConfigAdmin(admin.ModelAdmin):
    list_display = ["provider", "project", "is_enabled", "is_test_mode", "priority"]
    list_filter = ["provider", "is_enabled", "is_test_mode"]


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ["id", "order", "provider", "status", "amount", "amount_refunded", "created_at"]
    list_filter = ["provider", "status", "project"]
    search_fields = ["order__number", "provider_payment_id", "provider_order_id"]
    readonly_fields = ["idempotency_key", "captured_at", "failed_at", "created_at", "updated_at"]
    inlines = [RefundInline, PaymentEventInline]


@admin.register(Refund)
class RefundAdmin(admin.ModelAdmin):
    list_display = ["id", "payment", "amount", "status", "provider_refund_id", "created_at"]
    list_filter = ["status"]

    def has_add_permission(self, request):
        return False


@admin.register(PaymentEvent)
class PaymentEventAdmin(admin.ModelAdmin):
    list_display = ["id", "provider", "kind", "signature_valid", "payment", "created_at"]
    list_filter = ["provider", "kind", "signature_valid"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
