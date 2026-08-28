"""Coupon & discount engine (project.md section 14).

Reusable, project-scoped. One ``Coupon`` = one code + one discount rule +
eligibility constraints. ``CouponRedemption`` records each use (for usage limits
and reporting). Discount maths lives in ``services.quote_discount``.
"""

from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from apps.core.models import TenantScopedModel, TimeStampedModel

MONEY = dict(max_digits=12, decimal_places=2, default=Decimal("0"),
             validators=[MinValueValidator(Decimal("0"))])


class DiscountType(models.TextChoices):
    PERCENT = "percent", "Percentage"
    FIXED = "fixed", "Fixed amount"
    FREE_SHIPPING = "free_shipping", "Free shipping"


class AppliesTo(models.TextChoices):
    ALL = "all", "Whole order"
    PRODUCTS = "products", "Specific products"
    CATEGORIES = "categories", "Specific categories"


class Coupon(TenantScopedModel):
    code = models.CharField(max_length=40, db_index=True)
    description = models.CharField(max_length=255, blank=True)

    discount_type = models.CharField(max_length=20, choices=DiscountType.choices, default=DiscountType.PERCENT)
    value = models.DecimalField(**MONEY, help_text="Percent (0-100) or fixed amount.")
    max_discount = models.DecimalField(null=True, blank=True, max_digits=12, decimal_places=2,
                                       help_text="Cap for percentage discounts.")
    min_order_amount = models.DecimalField(**MONEY)

    applies_to = models.CharField(max_length=20, choices=AppliesTo.choices, default=AppliesTo.ALL)
    products = models.ManyToManyField("catalog.Product", blank=True, related_name="coupons")
    categories = models.ManyToManyField("categories.Category", blank=True, related_name="coupons")
    customer_groups = models.ManyToManyField("customers.CustomerGroup", blank=True, related_name="coupons")
    first_order_only = models.BooleanField(default=False)

    usage_limit = models.PositiveIntegerField(null=True, blank=True)
    usage_limit_per_customer = models.PositiveIntegerField(null=True, blank=True)
    used_count = models.PositiveIntegerField(default=0)

    starts_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["project", "code"], name="uniq_coupon_code_per_project"),
        ]

    def __str__(self):
        return self.code

    def save(self, *args, **kwargs):
        self.code = (self.code or "").strip().upper()
        super().save(*args, **kwargs)

    @property
    def is_scheduled_now(self):
        now = timezone.now()
        if self.starts_at and now < self.starts_at:
            return False
        if self.expires_at and now > self.expires_at:
            return False
        return True

    @property
    def is_exhausted(self):
        return self.usage_limit is not None and self.used_count >= self.usage_limit

    @property
    def is_live(self):
        return self.is_active and self.is_scheduled_now and not self.is_exhausted


class CouponRedemption(TimeStampedModel):
    coupon = models.ForeignKey(Coupon, on_delete=models.CASCADE, related_name="redemptions")
    order = models.ForeignKey("orders.Order", on_delete=models.SET_NULL, null=True, blank=True,
                              related_name="coupon_redemptions")
    customer = models.ForeignKey("customers.Customer", on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name="coupon_redemptions")
    customer_email = models.EmailField(db_index=True)
    amount = models.DecimalField(**MONEY)
    released = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.coupon_id} {self.amount}"
