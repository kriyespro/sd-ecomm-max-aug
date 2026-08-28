"""Payment models (project.md section 11).

Providers are pluggable — order logic never imports a specific gateway. A
``PaymentProviderConfig`` row enables and configures one provider for one
project; a ``Payment`` records one attempt against one order; ``Refund`` and
``PaymentEvent`` give a full transaction/audit trail for reconciliation.
"""

from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models

from apps.core.models import TenantScopedModel, TimeStampedModel

MONEY = dict(
    max_digits=12, decimal_places=2, default=Decimal("0"),
    validators=[MinValueValidator(Decimal("0"))],
)


class Provider(models.TextChoices):
    COD = "cod", "Cash on delivery"
    RAZORPAY = "razorpay", "Razorpay"
    STRIPE = "stripe", "Stripe"
    PAYU = "payu", "PayU"
    MANUAL = "manual", "Manual / offline"


class PaymentProviderConfig(TenantScopedModel):
    provider = models.CharField(max_length=20, choices=Provider.choices)
    display_name = models.CharField(max_length=80, blank=True)
    is_enabled = models.BooleanField(default=False)
    is_test_mode = models.BooleanField(default=True)
    priority = models.PositiveIntegerField(default=100)
    # Gateway keys / secrets. Kept in a JSON blob so each provider defines its own
    # shape; encrypt at rest in production (see project.md security notes).
    credentials = models.JSONField(default=dict, blank=True)
    config = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["priority", "provider"]
        constraints = [
            models.UniqueConstraint(fields=["project", "provider"], name="uniq_provider_per_project"),
        ]

    def __str__(self):
        return f"{self.get_provider_display()} ({self.project_id})"

    @property
    def label(self):
        return self.display_name or self.get_provider_display()


class PaymentStatus(models.TextChoices):
    CREATED = "created", "Created"
    PENDING = "pending", "Pending"
    AUTHORIZED = "authorized", "Authorized"
    PAID = "paid", "Paid"
    FAILED = "failed", "Failed"
    CANCELLED = "cancelled", "Cancelled"
    REFUNDED = "refunded", "Refunded"
    PARTIALLY_REFUNDED = "partially_refunded", "Partially refunded"


class Payment(TenantScopedModel):
    order = models.ForeignKey(
        "orders.Order", on_delete=models.CASCADE, related_name="payments"
    )
    provider = models.CharField(max_length=20, choices=Provider.choices)
    status = models.CharField(
        max_length=20, choices=PaymentStatus.choices, default=PaymentStatus.CREATED, db_index=True
    )
    amount = models.DecimalField(**MONEY)
    amount_refunded = models.DecimalField(**MONEY)
    currency = models.CharField(max_length=3, default="INR")

    provider_order_id = models.CharField(max_length=120, blank=True)
    provider_payment_id = models.CharField(max_length=120, blank=True, db_index=True)
    provider_signature = models.CharField(max_length=255, blank=True)
    idempotency_key = models.CharField(max_length=64, blank=True)

    error_code = models.CharField(max_length=80, blank=True)
    error_message = models.CharField(max_length=255, blank=True)
    meta = models.JSONField(default=dict, blank=True)

    captured_at = models.DateTimeField(null=True, blank=True)
    failed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["project", "status"]),
            models.Index(fields=["order", "status"]),
        ]

    def __str__(self):
        return f"{self.provider}:{self.pk} {self.status}"

    @property
    def is_settled(self):
        return self.status in {PaymentStatus.PAID, PaymentStatus.PARTIALLY_REFUNDED, PaymentStatus.REFUNDED}

    @property
    def refundable_amount(self):
        if not self.is_settled:
            return Decimal("0")
        return max(Decimal("0"), self.amount - self.amount_refunded)


class RefundStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    PROCESSED = "processed", "Processed"
    FAILED = "failed", "Failed"


class Refund(TimeStampedModel):
    payment = models.ForeignKey(Payment, on_delete=models.CASCADE, related_name="refunds")
    amount = models.DecimalField(**MONEY)
    status = models.CharField(max_length=20, choices=RefundStatus.choices, default=RefundStatus.PENDING)
    reason = models.CharField(max_length=255, blank=True)
    provider_refund_id = models.CharField(max_length=120, blank=True)
    meta = models.JSONField(default=dict, blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="refunds",
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Refund {self.amount} ({self.status})"


class PaymentEvent(TimeStampedModel):
    class Kind(models.TextChoices):
        INITIATE = "initiate", "Initiate"
        VERIFY = "verify", "Verify"
        WEBHOOK = "webhook", "Webhook"
        CAPTURE = "capture", "Capture"
        REFUND = "refund", "Refund"
        ERROR = "error", "Error"

    payment = models.ForeignKey(
        Payment, on_delete=models.CASCADE, null=True, blank=True, related_name="events"
    )
    project = models.ForeignKey(
        "projects.Project", on_delete=models.CASCADE, related_name="payment_events"
    )
    provider = models.CharField(max_length=20, blank=True)
    kind = models.CharField(max_length=20, choices=Kind.choices)
    signature_valid = models.BooleanField(null=True, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.provider} {self.kind}"
