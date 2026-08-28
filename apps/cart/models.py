"""Cart models.

A cart belongs to a project and to either an authenticated user or an anonymous
session key. Line prices are snapshotted when an item is added so later price
changes to the catalog do not silently move the cart total.
"""

from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models

from apps.core.models import TimeStampedModel

MONEY = dict(max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0"))])


class Cart(TimeStampedModel):
    project = models.ForeignKey(
        "projects.Project", on_delete=models.CASCADE, related_name="carts"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="carts",
    )
    session_key = models.CharField(max_length=40, blank=True, db_index=True)
    email = models.EmailField(blank=True)
    is_active = models.BooleanField(default=True)
    converted_order_id = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["project", "user"]),
            models.Index(fields=["project", "session_key"]),
        ]

    def __str__(self):
        who = self.user_id or self.session_key or "anon"
        return f"Cart<{self.pk} {who}>"

    @property
    def subtotal(self):
        return sum((i.line_total for i in self.items.all()), Decimal("0.00"))

    @property
    def item_count(self):
        return sum(i.quantity for i in self.items.all())


class CartItem(TimeStampedModel):
    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(
        "catalog.Product", on_delete=models.PROTECT, related_name="cart_items"
    )
    variant = models.ForeignKey(
        "catalog.Variant",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="cart_items",
    )
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(**MONEY)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(
                fields=["cart", "product", "variant"], name="uniq_cartitem_line"
            ),
        ]

    def __str__(self):
        return f"{self.quantity}x {self.product_id}"

    @property
    def line_total(self):
        return self.unit_price * self.quantity
