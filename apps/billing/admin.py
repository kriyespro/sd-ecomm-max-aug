from django.contrib import admin

from .models import (
    BillingSettings,
    Invoice,
    ManagerCommission,
    Plan,
    Subscription,
)


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ["name", "code", "price_monthly", "price_yearly",
                    "commission_monthly_pct", "commission_yearly_pct", "is_active", "is_public"]
    list_editable = ["price_monthly", "price_yearly", "is_active", "is_public"]
    ordering = ["sort_order"]


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ["project", "plan", "period", "status", "current_period_end", "manager"]
    list_filter = ["status", "period", "plan"]
    raw_id_fields = ["project", "manager"]
    search_fields = ["project__name"]


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ["number", "subscription", "amount", "status", "issued_at", "due_at", "paid_at"]
    list_filter = ["status"]
    search_fields = ["number", "subscription__project__name"]


@admin.register(ManagerCommission)
class ManagerCommissionAdmin(admin.ModelAdmin):
    list_display = ["manager", "amount", "rate_pct", "period", "status", "created_at", "paid_at"]
    list_filter = ["status", "period"]
    raw_id_fields = ["manager", "subscription", "invoice"]


@admin.register(BillingSettings)
class BillingSettingsAdmin(admin.ModelAdmin):
    list_display = ["__str__", "is_test_mode", "trial_days", "grace_days"]
