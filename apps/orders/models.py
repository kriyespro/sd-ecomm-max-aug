"""Order models (project.md section 10).

Flow: Cart -> Checkout -> Order -> OrderItem.

An order carries snapshots of everything that matters at purchase time (line
prices, product titles, addresses) so later catalog edits never rewrite history.
Address fields are plain JSON blobs with the shape::

    {"name", "line1", "line2", "city", "state", "postal_code", "country", "phone"}

Every status change is appended to OrderStatusEvent for an auditable timeline.
"""

from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models

from apps.core.models import TenantScopedModel, TimeStampedModel

MONEY = dict(
    max_digits=12,
    decimal_places=2,
    default=Decimal("0"),
    validators=[MinValueValidator(Decimal("0"))],
)


class OrderStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    CONFIRMED = "confirmed", "Confirmed"
    PROCESSING = "processing", "Processing"
    PACKED = "packed", "Packed"
    SHIPPED = "shipped", "Shipped"
    DELIVERED = "delivered", "Delivered"
    CANCELLED = "cancelled", "Cancelled"
    FAILED = "failed", "Failed"
    RETURNED = "returned", "Returned"
    REFUNDED = "refunded", "Refunded"


class PaymentStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    AUTHORIZED = "authorized", "Authorized"
    PAID = "paid", "Paid"
    PARTIALLY_REFUNDED = "partially_refunded", "Partially refunded"
    REFUNDED = "refunded", "Refunded"
    FAILED = "failed", "Failed"


class FulfillmentStatus(models.TextChoices):
    UNFULFILLED = "unfulfilled", "Unfulfilled"
    PARTIAL = "partial", "Partially fulfilled"
    FULFILLED = "fulfilled", "Fulfilled"


class Order(TenantScopedModel):
    number = models.CharField(max_length=32, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orders",
    )
    customer = models.ForeignKey(
        "customers.Customer",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orders",
    )
    email = models.EmailField()
    phone = models.CharField(max_length=32, blank=True)

    status = models.CharField(
        max_length=20, choices=OrderStatus.choices, default=OrderStatus.PENDING, db_index=True
    )
    payment_status = models.CharField(
        max_length=20, choices=PaymentStatus.choices, default=PaymentStatus.PENDING, db_index=True
    )
    fulfillment_status = models.CharField(
        max_length=20, choices=FulfillmentStatus.choices, default=FulfillmentStatus.UNFULFILLED
    )
    shipping_status = models.CharField(max_length=60, blank=True)

    billing_address = models.JSONField(default=dict, blank=True)
    shipping_address = models.JSONField(default=dict, blank=True)

    currency = models.CharField(max_length=3, default="INR")
    subtotal = models.DecimalField(**MONEY)
    discount_total = models.DecimalField(**MONEY)
    tax_total = models.DecimalField(**MONEY)
    shipping_total = models.DecimalField(**MONEY)
    grand_total = models.DecimalField(**MONEY)
    coupon_code = models.CharField(max_length=60, blank=True)
    # Snapshot of the chosen shipping method: {id, name, carrier, rate, cod_fee,
    # min_days, max_days}. Set by apps.shipping.services.set_order_shipping.
    shipping_method = models.JSONField(default=dict, blank=True)

    warehouse = models.ForeignKey(
        "inventory.Warehouse",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orders",
    )
    stock_reserved = models.BooleanField(default=False)

    tracking_number = models.CharField(max_length=120, blank=True)
    courier = models.CharField(max_length=120, blank=True)

    customer_note = models.TextField(blank=True)
    admin_note = models.TextField(blank=True)

    placed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["project", "number"], name="uniq_order_number_per_project"),
        ]
        indexes = [
            models.Index(fields=["project", "status"]),
            models.Index(fields=["project", "payment_status"]),
        ]

    def __str__(self):
        return self.number

    def recalc_totals(self, *, save=True):
        self.subtotal = sum((i.line_total for i in self.items.all()), Decimal("0.00"))
        self.grand_total = (
            self.subtotal - self.discount_total + self.tax_total + self.shipping_total
        )
        if self.grand_total < 0:
            self.grand_total = Decimal("0.00")
        if save:
            self.save(update_fields=[
                "subtotal", "grand_total", "discount_total",
                "tax_total", "shipping_total", "updated_at",
            ])
        return self.grand_total

    @property
    def is_open(self):
        return self.status not in {
            OrderStatus.CANCELLED, OrderStatus.FAILED,
            OrderStatus.RETURNED, OrderStatus.REFUNDED, OrderStatus.DELIVERED,
        }


class OrderItem(TimeStampedModel):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(
        "catalog.Product", on_delete=models.SET_NULL, null=True, blank=True, related_name="order_items"
    )
    variant = models.ForeignKey(
        "catalog.Variant", on_delete=models.SET_NULL, null=True, blank=True, related_name="order_items"
    )
    product_title = models.CharField(max_length=255)
    variant_name = models.CharField(max_length=255, blank=True)
    sku = models.CharField(max_length=64, blank=True)
    unit_price = models.DecimalField(**MONEY)
    quantity = models.PositiveIntegerField(default=1)
    line_total = models.DecimalField(**MONEY)
    fulfilled_quantity = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.quantity}x {self.product_title}"

    @property
    def remaining_quantity(self):
        return max(0, self.quantity - self.fulfilled_quantity)


class OrderStatusEvent(TimeStampedModel):
    class Kind(models.TextChoices):
        STATUS = "status", "Status"
        PAYMENT = "payment", "Payment"
        FULFILLMENT = "fulfillment", "Fulfillment"
        NOTE = "note", "Note"

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="events")
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.STATUS)
    from_value = models.CharField(max_length=40, blank=True)
    to_value = models.CharField(max_length=40, blank=True)
    note = models.TextField(blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="order_events",
    )

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.order_id} {self.kind} {self.from_value}->{self.to_value}"
