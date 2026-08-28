from django.contrib import admin

from .models import WebhookDelivery, WebhookEndpoint


@admin.register(WebhookEndpoint)
class WebhookEndpointAdmin(admin.ModelAdmin):
    list_display = ["url", "project", "is_active", "created_at"]
    list_filter = ["project", "is_active"]
    readonly_fields = ["secret"]


@admin.register(WebhookDelivery)
class WebhookDeliveryAdmin(admin.ModelAdmin):
    list_display = ["event", "endpoint", "status", "attempts", "response_status", "created_at"]
    list_filter = ["project", "status", "event"]
    search_fields = ["event", "endpoint__url"]
    readonly_fields = ["payload", "signature", "response_body"]

    def has_add_permission(self, request):
        return False
