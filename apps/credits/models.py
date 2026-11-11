"""Store credit: one balance per customer, fed by gift-card redemption and
loyalty earn, spent at checkout. A single ledger (``CreditLedger``) is the
audit trail for both sources and the one spend path -- so a store owner (and
a dispute) can always see where a balance came from.
"""

from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from apps.core.models import TenantScopedModel

MONEY = dict(max_digits=12, decimal_places=2, default=Decimal("0"),
             validators=[MinValueValidator(Decimal("0"))])

# Ledger amounts are signed (a debit is negative) so they can't reuse MONEY's
# non-negative validator.
SIGNED_MONEY = dict(max_digits=12, decimal_places=2)


class CreditAccount(TenantScopedModel):
    customer = models.OneToOneField(
        "customers.Customer", on_delete=models.CASCADE, related_name="credit_account",
    )
    balance = models.DecimalField(**MONEY)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"CreditAccount<{self.customer_id}> {self.balance}"


class CreditKind(models.TextChoices):
    GIFT_CARD = "gift_card", "Gift card redeemed"
    LOYALTY_EARN = "loyalty_earn", "Loyalty reward earned"
    REDEEMED = "redeemed", "Applied to an order"
    REFUND_REVERSAL = "refund_reversal", "Returned (order cancelled)"
    RETURN_REFUND = "return_refund", "Return/exchange refunded as credit"
    MANUAL = "manual", "Manual adjustment"


class CreditLedger(TenantScopedModel):
    account = models.ForeignKey(CreditAccount, on_delete=models.CASCADE, related_name="entries")
    kind = models.CharField(max_length=20, choices=CreditKind.choices, db_index=True)
    amount = models.DecimalField(**SIGNED_MONEY)
    balance_after = models.DecimalField(max_digits=12, decimal_places=2)
    order = models.ForeignKey(
        "orders.Order", on_delete=models.SET_NULL, null=True, blank=True, related_name="credit_entries",
    )
    gift_card = models.ForeignKey(
        "credits.GiftCard", on_delete=models.SET_NULL, null=True, blank=True, related_name="ledger_entries",
    )
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["account", "created_at"])]

    def __str__(self):
        return f"CreditLedger<{self.account_id}> {self.kind} {self.amount}"


class GiftCardStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    REDEEMED = "redeemed", "Redeemed"
    EXPIRED = "expired", "Expired"
    CANCELLED = "cancelled", "Cancelled"


class GiftCard(TenantScopedModel):
    code = models.CharField(max_length=24, db_index=True)
    value = models.DecimalField(**MONEY)
    status = models.CharField(
        max_length=10, choices=GiftCardStatus.choices, default=GiftCardStatus.ACTIVE, db_index=True,
    )
    issued_to_email = models.EmailField(blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    redeemed_account = models.ForeignKey(
        CreditAccount, on_delete=models.SET_NULL, null=True, blank=True, related_name="redeemed_gift_cards",
    )
    redeemed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["project", "code"], name="uniq_giftcard_code_per_project"),
        ]

    def __str__(self):
        return self.code

    def save(self, *args, **kwargs):
        self.code = (self.code or "").strip().upper()
        super().save(*args, **kwargs)

    @property
    def is_expired(self):
        return bool(self.expires_at and timezone.now() > self.expires_at)

    @property
    def is_redeemable(self):
        return self.status == GiftCardStatus.ACTIVE and not self.is_expired


class LoyaltyProgram(TenantScopedModel):
    """One row per store -- the on/off switch + earn rate."""

    is_active = models.BooleanField(default=False)
    earn_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0"))],
        help_text="Percent of each paid order's subtotal credited back as store credit.",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["project"], name="uniq_loyaltyprogram_per_project"),
        ]
        verbose_name = "loyalty program"

    def __str__(self):
        return f"LoyaltyProgram<{self.project_id}>"

    def earn_for(self, order_subtotal, *, rate=None):
        rate = self.earn_percent if rate is None else rate
        raw = (order_subtotal or Decimal("0")) * rate / Decimal("100")
        return raw.quantize(Decimal("0.01"))


class LoyaltyTier(TenantScopedModel):
    """Optional spend-based tiers layered on top of ``LoyaltyProgram``'s flat
    rate -- a customer whose lifetime ``Customer.total_spent`` clears a tier's
    ``min_spend`` earns that tier's rate instead of the flat one. No rows
    configured means every customer just earns the flat rate, unchanged."""

    name = models.CharField(max_length=60)
    min_spend = models.DecimalField(**MONEY, help_text="Lifetime spend required to reach this tier.")
    earn_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0"))],
    )

    class Meta:
        ordering = ["min_spend"]

    def __str__(self):
        return f"{self.name} (>= {self.min_spend})"


class LoyaltyEarning(TenantScopedModel):
    """One row per order that has ever earned loyalty credit -- the
    OneToOneField is the idempotency guard: a re-delivered PAYMENT_SUCCESS
    signal can never double-credit the same order (same trick as
    apps.referrals.models.ReferralCommission)."""

    order = models.OneToOneField(
        "orders.Order", on_delete=models.CASCADE, related_name="loyalty_earning",
    )
    account = models.ForeignKey(CreditAccount, on_delete=models.CASCADE, related_name="loyalty_earnings")
    amount = models.DecimalField(**MONEY)

    def __str__(self):
        return f"LoyaltyEarning<order={self.order_id}> {self.amount}"
