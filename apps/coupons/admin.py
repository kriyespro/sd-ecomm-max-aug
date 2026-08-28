from django.contrib import admin

from .models import Coupon, CouponRedemption


class CouponRedemptionInline(admin.TabularInline):
    model = CouponRedemption
    extra = 0
    readonly_fields = ["order", "customer_email", "amount", "released", "created_at"]
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Coupon)
class CouponAdmin(admin.ModelAdmin):
    list_display = ["code", "project", "discount_type", "value", "used_count", "usage_limit", "is_active", "expires_at"]
    list_filter = ["project", "discount_type", "applies_to", "is_active", "first_order_only"]
    search_fields = ["code", "description"]
    filter_horizontal = ["products", "categories", "customer_groups"]
    readonly_fields = ["used_count"]
    inlines = [CouponRedemptionInline]


@admin.register(CouponRedemption)
class CouponRedemptionAdmin(admin.ModelAdmin):
    list_display = ["coupon", "customer_email", "amount", "order", "released", "created_at"]
    list_filter = ["released"]
    search_fields = ["customer_email", "coupon__code"]

    def has_add_permission(self, request):
        return False
