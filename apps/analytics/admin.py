from django.contrib import admin

from .models import DailyMetric, EventCounter


@admin.register(DailyMetric)
class DailyMetricAdmin(admin.ModelAdmin):
    list_display = ["date", "project", "orders_count", "revenue", "aov", "new_customers", "cancelled_count"]
    list_filter = ["project"]
    date_hierarchy = "date"


@admin.register(EventCounter)
class EventCounterAdmin(admin.ModelAdmin):
    list_display = ["date", "project", "key", "count"]
    list_filter = ["project", "key"]
