"""Per-store WhatsApp (Meta Cloud API) — a free add-on for transactional
customer messages (order confirmed / shipped / delivered / refunded).

v1: the seller runs their own Meta app + WhatsApp Business Account and pastes
the phone-number id + a permanent access token. Meta bills the seller directly.
Embedded Signup / a platform BSP model can replace the paste later without a
schema change.
"""

import secrets

from django.core.cache import cache
from django.db import models
from django.db.models import Q

from apps.core.models import TenantScopedModel, TimeStampedModel

_CACHE_KEY = "whatsapp:account:{project_id}"
_CACHE_TTL = 600
_PN_INDEX_KEY = "whatsapp:pn:{phone_number_id}"


def _new_verify_token():
    return secrets.token_urlsafe(24)


class WhatsAppAccount(TenantScopedModel):
    phone_number_id = models.CharField(max_length=64, blank=True, db_index=True)
    waba_id = models.CharField("WhatsApp Business Account ID", max_length=64, blank=True)
    access_token = models.CharField(max_length=512, blank=True)
    # Optional: the Meta app secret. When set, inbound webhooks are HMAC-verified.
    app_secret = models.CharField(max_length=128, blank=True)
    # We generate this; the seller pastes it into their Meta app's webhook config.
    verify_token = models.CharField(max_length=64, default=_new_verify_token)
    graph_version = models.CharField(max_length=8, default="v21.0")

    # Filled from Meta when the credentials are validated.
    display_phone = models.CharField(max_length=32, blank=True)
    verified_name = models.CharField(max_length=120, blank=True)

    is_active = models.BooleanField(default=False)
    last_error = models.CharField(max_length=255, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["project"], name="uniq_whatsapp_account_per_project"),
            # Two stores must never be routable by the same phone_number_id —
            # the inbound webhook has no other way to pick which store a
            # message belongs to. Blank is allowed on multiple rows (not yet
            # configured).
            models.UniqueConstraint(
                fields=["phone_number_id"], name="uniq_whatsapp_phone_number_id",
                condition=~Q(phone_number_id=""),
            ),
        ]
        verbose_name = "WhatsApp account"

    def __str__(self):
        return f"WhatsApp<{self.project_id} {self.display_phone or self.phone_number_id}>"

    @property
    def ready(self):
        return bool(self.is_active and self.phone_number_id and self.access_token)

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        cache.delete(_CACHE_KEY.format(project_id=self.project_id))
        if self.phone_number_id:
            cache.delete(_PN_INDEX_KEY.format(phone_number_id=self.phone_number_id))

    def delete(self, *args, **kwargs):
        pid, pn = self.project_id, self.phone_number_id
        super().delete(*args, **kwargs)
        cache.delete(_CACHE_KEY.format(project_id=pid))
        if pn:
            cache.delete(_PN_INDEX_KEY.format(phone_number_id=pn))


def account_for(project):
    """Cached ``WhatsAppAccount`` for a project, or ``None``."""
    if project is None:
        return None
    key = _CACHE_KEY.format(project_id=project.pk)
    cached = cache.get(key)
    if cached is not None:
        return cached or None
    row = WhatsAppAccount.objects.filter(project=project).first()
    cache.set(key, row or False, _CACHE_TTL)
    return row


def account_by_phone_number_id(phone_number_id):
    """Route an inbound webhook (which only carries the phone-number id) to the
    right store. Only a verified (``is_active``) account may claim routing —
    a row left over from a failed Meta credential check must never intercept
    another store's messages."""
    if not phone_number_id:
        return None
    return (
        WhatsAppAccount.objects
        .filter(phone_number_id=phone_number_id, is_active=True)
        .select_related("project")
        .first()
    )


class WAStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    ACCEPTED = "accepted", "Accepted by Meta"
    SENT = "sent", "Sent"
    DELIVERED = "delivered", "Delivered"
    READ = "read", "Read"
    FAILED = "failed", "Failed"
    SKIPPED = "skipped", "Skipped"


class WhatsAppMessage(TenantScopedModel):
    to_number = models.CharField(max_length=32)
    wamid = models.CharField(max_length=128, blank=True, db_index=True)
    kind = models.CharField(max_length=12, default="template")  # template | text
    template_name = models.CharField(max_length=120, blank=True)
    event = models.CharField(max_length=40, blank=True)
    body_preview = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=12, choices=WAStatus.choices, default=WAStatus.QUEUED, db_index=True)
    error = models.CharField(max_length=255, blank=True)
    related_type = models.CharField(max_length=60, blank=True)
    related_id = models.CharField(max_length=40, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.template_name or self.kind} -> {self.to_number} ({self.status})"


class WhatsAppInbound(TimeStampedModel):
    """A message a customer sent us — kept so a store can see replies + opt-outs."""

    account = models.ForeignKey(WhatsAppAccount, on_delete=models.CASCADE, related_name="inbound")
    from_number = models.CharField(max_length=32, db_index=True)
    wamid = models.CharField(max_length=128, blank=True)
    text = models.TextField(blank=True)
    is_opt_out = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]
