"""Platform billing — subscription plans, per-store subscriptions, invoices and
platform-manager commissions.

Money model: the **store pays retail INR**. The platform manager who signed the
store up accrues a commission on every paid invoice (monthly plans: recurring;
yearly plans: once per annual payment). Commission is a ledger the platform
settles out-of-band and marks paid.
"""

from decimal import Decimal

from django.conf import settings
from django.db import models

from apps.core.models import TimeStampedModel

_MONEY = dict(max_digits=12, decimal_places=2)


class BillingPeriod(models.TextChoices):
    MONTHLY = "monthly", "Monthly"
    YEARLY = "yearly", "Yearly"


class SubscriptionStatus(models.TextChoices):
    TRIALING = "trialing", "Trialing"
    ACTIVE = "active", "Active"
    PAST_DUE = "past_due", "Past due"
    SUSPENDED = "suspended", "Suspended"
    CANCELLED = "cancelled", "Cancelled"


class InvoiceStatus(models.TextChoices):
    OPEN = "open", "Open"
    PAID = "paid", "Paid"
    VOID = "void", "Void"
    UNCOLLECTIBLE = "uncollectible", "Uncollectible"


class CommissionStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    APPROVED = "approved", "Approved"
    PAID = "paid", "Paid"


class BillingSettings(TimeStampedModel):
    """Singleton — the platform's own Razorpay account + billing policy."""

    razorpay_key_id = models.CharField(max_length=120, blank=True)
    razorpay_key_secret = models.CharField(max_length=200, blank=True)
    razorpay_webhook_secret = models.CharField(max_length=200, blank=True)
    is_test_mode = models.BooleanField(default=True)

    trial_days = models.PositiveIntegerField(default=14)
    grace_days = models.PositiveIntegerField(default=7, help_text="Days after due date before suspend.")
    invoice_prefix = models.CharField(max_length=12, default="INV")
    currency = models.CharField(max_length=3, default="INR")

    default_commission_monthly_pct = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("20"))
    default_commission_yearly_pct = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("30"))

    class Meta:
        verbose_name = "billing settings"
        verbose_name_plural = "billing settings"

    def __str__(self):
        return "Billing settings"

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)


class Plan(TimeStampedModel):
    """A subscription tier. Prices are retail INR and editable by a super admin."""

    code = models.SlugField(max_length=40, unique=True)
    name = models.CharField(max_length=80)
    tagline = models.CharField(max_length=160, blank=True)
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    is_public = models.BooleanField(default=True, help_text="Shown on the plan picker.")

    price_monthly = models.DecimalField(**_MONEY, default=Decimal("0"))
    price_yearly = models.DecimalField(**_MONEY, default=Decimal("0"), help_text="Total for 12 months.")

    commission_monthly_pct = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("20"))
    commission_yearly_pct = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("30"))

    # Limits — null = unlimited.
    max_products = models.PositiveIntegerField(null=True, blank=True)
    max_staff = models.PositiveIntegerField(null=True, blank=True)
    max_custom_domains = models.PositiveIntegerField(null=True, blank=True)
    storage_gb = models.PositiveIntegerField(null=True, blank=True)
    allow_skin_upload = models.BooleanField(default=False)
    remove_platform_branding = models.BooleanField(default=False)
    priority_support = models.BooleanField(default=False)
    transaction_fee_pct = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0"),
                                              help_text="Platform fee on storefront sales, %.")

    features = models.JSONField(default=list, blank=True, help_text="Marketing bullet list.")

    class Meta:
        ordering = ["sort_order", "price_monthly"]

    def __str__(self):
        return self.name

    def price_for(self, period) -> Decimal:
        return self.price_yearly if period == BillingPeriod.YEARLY else self.price_monthly

    def commission_pct_for(self, period) -> Decimal:
        return self.commission_yearly_pct if period == BillingPeriod.YEARLY else self.commission_monthly_pct

    @property
    def yearly_monthly_equivalent(self) -> Decimal:
        return (self.price_yearly / 12).quantize(Decimal("0.01")) if self.price_yearly else Decimal("0")


class Subscription(TimeStampedModel):
    project = models.OneToOneField("projects.Project", on_delete=models.CASCADE, related_name="subscription")
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="subscriptions")
    period = models.CharField(max_length=10, choices=BillingPeriod.choices, default=BillingPeriod.MONTHLY)
    status = models.CharField(max_length=12, choices=SubscriptionStatus.choices,
                              default=SubscriptionStatus.TRIALING, db_index=True)

    current_period_start = models.DateTimeField()
    current_period_end = models.DateTimeField(db_index=True)
    trial_end = models.DateTimeField(null=True, blank=True)
    cancel_at_period_end = models.BooleanField(default=False)

    # Platform manager credited for this store (nullable — direct signups have none).
    manager = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                on_delete=models.SET_NULL, related_name="managed_subscriptions")

    # Optional per-store price override (deal pricing). Null = use the plan price.
    override_price = models.DecimalField(**_MONEY, null=True, blank=True)

    def __str__(self):
        return f"{self.project} · {self.plan.name} ({self.period})"

    @property
    def is_live(self) -> bool:
        return self.status in {SubscriptionStatus.TRIALING, SubscriptionStatus.ACTIVE, SubscriptionStatus.PAST_DUE}

    def current_price(self) -> Decimal:
        if self.override_price is not None:
            return self.override_price
        return self.plan.price_for(self.period)


class Invoice(TimeStampedModel):
    subscription = models.ForeignKey(Subscription, on_delete=models.CASCADE, related_name="invoices")
    number = models.CharField(max_length=32, unique=True)
    period_start = models.DateTimeField()
    period_end = models.DateTimeField()

    description = models.CharField(max_length=200, blank=True)
    amount = models.DecimalField(**_MONEY)
    currency = models.CharField(max_length=3, default="INR")

    status = models.CharField(max_length=14, choices=InvoiceStatus.choices,
                              default=InvoiceStatus.OPEN, db_index=True)
    issued_at = models.DateTimeField(auto_now_add=True)
    due_at = models.DateTimeField()
    paid_at = models.DateTimeField(null=True, blank=True)

    provider_order_id = models.CharField(max_length=120, blank=True)
    provider_payment_id = models.CharField(max_length=120, blank=True)

    class Meta:
        ordering = ["-issued_at"]
        indexes = [models.Index(fields=["status", "due_at"])]

    def __str__(self):
        return self.number


class ManagerCommission(TimeStampedModel):
    manager = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name="commissions")
    subscription = models.ForeignKey(Subscription, on_delete=models.CASCADE, related_name="commissions")
    invoice = models.OneToOneField(Invoice, on_delete=models.CASCADE, related_name="commission")

    period = models.CharField(max_length=10, choices=BillingPeriod.choices)
    base_amount = models.DecimalField(**_MONEY)
    rate_pct = models.DecimalField(max_digits=5, decimal_places=2)
    amount = models.DecimalField(**_MONEY)

    status = models.CharField(max_length=10, choices=CommissionStatus.choices,
                              default=CommissionStatus.PENDING, db_index=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    payout_ref = models.CharField(max_length=120, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.manager} · {self.amount} ({self.status})"
