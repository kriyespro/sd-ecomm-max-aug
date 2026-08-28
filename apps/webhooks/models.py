"""Outbound webhooks (project.md section 22).

A project registers endpoints; a domain event fans out to every active endpoint
subscribed to it. Each attempt is a signed POST recorded as a WebhookDelivery
with retry state.
"""

from django.db import models
from django.utils.crypto import get_random_string

from apps.core.models import TenantScopedModel


class WebhookEndpoint(TenantScopedModel):
    url = models.URLField(max_length=500)
    secret = models.CharField(max_length=64, blank=True, help_text="Used to sign payloads (auto if blank).")
    description = models.CharField(max_length=200, blank=True)
    events = models.JSONField(default=list, blank=True, help_text="Subscribed event keys; empty = all.")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.url

    def save(self, *args, **kwargs):
        if not self.secret:
            self.secret = get_random_string(40)
        super().save(*args, **kwargs)

    def wants(self, event):
        return self.is_active and (not self.events or event in self.events)


class DeliveryStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    SUCCESS = "success", "Success"
    FAILED = "failed", "Failed"
    EXHAUSTED = "exhausted", "Exhausted"


class WebhookDelivery(TenantScopedModel):
    endpoint = models.ForeignKey(WebhookEndpoint, on_delete=models.CASCADE, related_name="deliveries")
    event = models.CharField(max_length=40, db_index=True)
    payload = models.JSONField(default=dict)
    signature = models.CharField(max_length=100, blank=True)

    status = models.CharField(max_length=12, choices=DeliveryStatus.choices, default=DeliveryStatus.PENDING, db_index=True)
    attempts = models.PositiveIntegerField(default=0)
    response_status = models.PositiveIntegerField(null=True, blank=True)
    response_body = models.TextField(blank=True)
    error = models.CharField(max_length=255, blank=True)
    next_retry_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "next_retry_at"])]

    def __str__(self):
        return f"{self.event} -> {self.endpoint_id} ({self.status})"
