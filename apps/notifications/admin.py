from django.contrib import admin

from .models import NotificationLog, NotificationSettings, NotificationTemplate


@admin.register(NotificationSettings)
class NotificationSettingsAdmin(admin.ModelAdmin):
    list_display = ["project", "from_email", "email_provider", "sms_provider"]


@admin.register(NotificationTemplate)
class NotificationTemplateAdmin(admin.ModelAdmin):
    list_display = ["event", "channel", "project", "is_active"]
    list_filter = ["project", "event", "channel", "is_active"]


@admin.register(NotificationLog)
class NotificationLogAdmin(admin.ModelAdmin):
    list_display = ["event", "channel", "to_address", "status", "provider", "created_at"]
    list_filter = ["project", "status", "channel", "event"]
    search_fields = ["to_address", "subject"]
    readonly_fields = ["body", "meta"]

    def has_add_permission(self, request):
        return False
