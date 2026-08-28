from django.contrib import admin

from .models import Customer, CustomerAddress, CustomerGroup


class CustomerAddressInline(admin.TabularInline):
    model = CustomerAddress
    extra = 0


@admin.register(CustomerGroup)
class CustomerGroupAdmin(admin.ModelAdmin):
    list_display = ["name", "project", "discount_percent", "is_default"]
    list_filter = ["project", "is_default"]
    prepopulated_fields = {"slug": ["name"]}


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ["email", "full_name", "project", "segment", "group", "orders_count", "total_spent", "is_blocked"]
    list_filter = ["project", "segment", "is_blocked", "marketing_opt_in", "group"]
    search_fields = ["email", "first_name", "last_name", "phone"]
    readonly_fields = ["orders_count", "total_spent", "last_order_at", "segment"]
    inlines = [CustomerAddressInline]
