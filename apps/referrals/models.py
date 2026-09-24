"""Per-store customer/influencer referral (affiliate) program.

A store owner turns this on, sets a commission rate and a cookie window.
Any signed-in storefront customer can become a Referrer and gets a
``?ref=<code>`` link. A visit through that link is attributed via a cookie
set through the analytics beacon POST (apps.shopfront.views.BeaconView) --
storefront GET pages are CDN-edge-cacheable (apps.shopfront.middleware), so
a ``Set-Cookie`` on one of those responses could get cached and handed to
the wrong visitor; the beacon is a POST, never cached, same reasoning the
visitor-id cookie already uses.

A click just gets a ``ReferralAttribution`` row on the order it eventually
buys through -- no ``ReferralCommission`` yet. The commission is only ever
created off ``Events.PAYMENT_SUCCESS`` (apps.referrals.signals), which
naturally covers both a real gateway AND a COD order (COD fires that event
only once the store owner captures the cash) -- this is what keeps a
cancelled/failed/uncollected order from ever generating a payable
commission, matching the platform's own existing commission-safety pattern
(apps.billing.services.admin_extend and friends never invoice speculatively
either).
"""

from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.crypto import get_random_string

from apps.core.models import TenantScopedModel


class CommissionType(models.TextChoices):
    PERCENT = "percent", "% of order value"
    FIXED = "fixed", "Fixed amount per order"


class ReferralProgram(TenantScopedModel):
    """One row per store (project) -- the on/off switch + commission rule."""

    is_active = models.BooleanField(default=False)
    commission_type = models.CharField(
        max_length=10, choices=CommissionType.choices, default=CommissionType.PERCENT,
    )
    commission_value = models.DecimalField(
        max_digits=8, decimal_places=2, default=Decimal("10"),
        validators=[MinValueValidator(Decimal("0"))],
        help_text="A percent (0-100) or a flat currency amount, depending on Commission type.",
    )
    cookie_days = models.PositiveIntegerField(
        default=30, validators=[MinValueValidator(1), MaxValueValidator(365)],
        help_text="How long a click keeps crediting a referrer's orders.",
    )
    minimum_payout = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0"))],
        help_text="Shown to referrers as the payout threshold. Informational only -- "
                  "payouts are still marked manually by the store owner.",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["project"], name="uniq_referralprogram_per_project"),
        ]
        verbose_name = "referral program"

    def __str__(self):
        return f"ReferralProgram<{self.project_id}>"

    def commission_for(self, order_amount):
        if self.commission_type == CommissionType.FIXED:
            return self.commission_value
        raw = (order_amount or Decimal("0")) * self.commission_value / Decimal("100")
        return raw.quantize(Decimal("0.01"))


def _generate_code():
    # No 0/O/1/I -- avoids a code that's ambiguous when read aloud or typed.
    return get_random_string(8, allowed_chars="ABCDEFGHJKLMNPQRSTUVWXYZ23456789")


class ReferrerStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    DISABLED = "disabled", "Disabled"


class Referrer(TenantScopedModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="referrer_profiles",
    )
    code = models.CharField(max_length=12, unique=True, db_index=True)
    status = models.CharField(
        max_length=10, choices=ReferrerStatus.choices, default=ReferrerStatus.ACTIVE,
    )

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["project", "user"], name="uniq_referrer_per_user_project"),
        ]

    def __str__(self):
        return f"{self.code} ({self.user_id})"

    def save(self, *args, **kwargs):
        if not self.code:
            code = _generate_code()
            while Referrer.objects.filter(code=code).exists():
                code = _generate_code()
            self.code = code
        super().save(*args, **kwargs)

    @property
    def is_active(self):
        return self.status == ReferrerStatus.ACTIVE

    @property
    def referral_path(self):
        return f"/?ref={self.code}"


class ReferralClick(TenantScopedModel):
    """One row per landing hit through a referral link -- logged from the
    analytics beacon, so it shares that endpoint's dedupe-free, best-effort
    nature (a real visitor may log more than one click across a session)."""

    referrer = models.ForeignKey(Referrer, on_delete=models.CASCADE, related_name="clicks")
    landing_path = models.CharField(max_length=200, blank=True)

    class Meta:
        indexes = [models.Index(fields=["referrer", "created_at"])]

    def __str__(self):
        return f"Click<{self.referrer_id}>"


class ReferralAttribution(TenantScopedModel):
    """Which referrer (if any) an order is credited to -- written at
    checkout, well before any payment has settled. Kept separate from
    ``ReferralCommission`` on purpose: existing is not the same as payable."""

    order = models.OneToOneField(
        "orders.Order", on_delete=models.CASCADE, related_name="referral_attribution",
    )
    referrer = models.ForeignKey(Referrer, on_delete=models.CASCADE, related_name="attributions")

    def __str__(self):
        return f"Attribution<order={self.order_id} referrer={self.referrer_id}>"


class CommissionStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    PAID = "paid", "Paid"


class ReferralCommission(TenantScopedModel):
    referrer = models.ForeignKey(Referrer, on_delete=models.CASCADE, related_name="commissions")
    order = models.OneToOneField(
        "orders.Order", on_delete=models.CASCADE, related_name="referral_commission",
    )
    order_amount = models.DecimalField(max_digits=12, decimal_places=2)
    commission_amount = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(
        max_length=10, choices=CommissionStatus.choices, default=CommissionStatus.PENDING,
        db_index=True,
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["referrer", "status"])]

    def __str__(self):
        return f"Commission<order={self.order_id}> {self.status}"
