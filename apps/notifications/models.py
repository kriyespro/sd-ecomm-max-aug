"""Notifications (project.md section 20).

Provider abstraction — no gateway hardcoded. Each project can override the
per-event template; anything without an override uses a built-in default.
Every send is recorded in NotificationLog.
"""

from django.db import models

from apps.core.models import TenantScopedModel


class Channel(models.TextChoices):
    EMAIL = "email", "Email"
    SMS = "sms", "SMS"
    WHATSAPP = "whatsapp", "WhatsApp"


class Event(models.TextChoices):
    ORDER_CONFIRMATION = "order_confirmation", "Order confirmation"
    PAYMENT_CONFIRMATION = "payment_confirmation", "Payment confirmation"
    SHIPMENT = "shipment", "Shipment dispatched"
    DELIVERY = "delivery", "Delivered"
    ORDER_CANCELLED = "order_cancelled", "Order cancelled"
    REFUND = "refund", "Refund processed"
    WELCOME = "welcome", "Welcome"
    PASSWORD_RESET = "password_reset", "Password reset"
    LOW_STOCK_ALERT = "low_stock_alert", "Low stock alert"


class NotificationSettings(TenantScopedModel):
    from_email = models.EmailField(blank=True)
    from_name = models.CharField(max_length=120, blank=True)
    reply_to = models.EmailField(blank=True)
    email_provider = models.CharField(max_length=30, default="django")
    email_config = models.JSONField(default=dict, blank=True)
    sms_provider = models.CharField(max_length=30, default="null")
    sms_config = models.JSONField(default=dict, blank=True)
    disabled_events = models.JSONField(default=list, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["project"], name="uniq_notifsettings_per_project"),
        ]
        verbose_name = "notification settings"
        verbose_name_plural = "notification settings"

    def __str__(self):
        return f"NotificationSettings<{self.project_id}>"

    def event_enabled(self, event):
        return event not in (self.disabled_events or [])


class NotificationTemplate(TenantScopedModel):
    event = models.CharField(max_length=40, choices=Event.choices)
    channel = models.CharField(max_length=12, choices=Channel.choices, default=Channel.EMAIL)
    subject = models.CharField(max_length=255, blank=True)
    body = models.TextField()
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["event", "channel"]
        constraints = [
            models.UniqueConstraint(fields=["project", "event", "channel"], name="uniq_notiftemplate"),
        ]

    def __str__(self):
        return f"{self.event}/{self.channel}"


class SendStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    SENT = "sent", "Sent"
    FAILED = "failed", "Failed"
    SKIPPED = "skipped", "Skipped"


class NotificationLog(TenantScopedModel):
    event = models.CharField(max_length=40, db_index=True)
    channel = models.CharField(max_length=12, choices=Channel.choices, default=Channel.EMAIL)
    to_address = models.CharField(max_length=255)
    subject = models.CharField(max_length=255, blank=True)
    body = models.TextField(blank=True)
    status = models.CharField(max_length=12, choices=SendStatus.choices, default=SendStatus.PENDING, db_index=True)
    provider = models.CharField(max_length=30, blank=True)
    error = models.CharField(max_length=255, blank=True)
    related_type = models.CharField(max_length=60, blank=True)
    related_id = models.CharField(max_length=40, blank=True)
    meta = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.event} -> {self.to_address} ({self.status})"
