"""Shipping models (project.md section 12).

Layers:
- ``ShippingZone``   — geographic match rules (country / state / pincode).
- ``ShippingMethod`` — a rate + delivery estimate + COD rule inside a zone.
- ``Shipment``       — a physical dispatch against an order, with a status
                       timeline (``ShipmentEvent``) fed by couriers/webhooks.

Rate maths and zone matching live on the models; orchestration (picking a
method, moving order status) lives in ``services``. Couriers are pluggable —
see ``apps.shipping.couriers``.
"""

from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models

from apps.core.models import TenantScopedModel, TimeStampedModel

MONEY = dict(
    max_digits=12, decimal_places=2, default=Decimal("0"),
    validators=[MinValueValidator(Decimal("0"))],
)


def _norm(value):
    return str(value or "").strip().lower()


class ShippingZone(TenantScopedModel):
    name = models.CharField(max_length=120)
    is_active = models.BooleanField(default=True)
    priority = models.PositiveIntegerField(default=100, help_text="Lower matches first.")
    # Empty list = matches anything for that dimension.
    countries = models.JSONField(default=list, blank=True, help_text="ISO codes, e.g. [\"IN\"].")
    states = models.JSONField(default=list, blank=True)
    postal_prefixes = models.JSONField(default=list, blank=True, help_text="Pincode prefixes, e.g. [\"56\", \"4000\"].")

    class Meta:
        ordering = ["priority", "name"]

    def __str__(self):
        return self.name

    def matches(self, address: dict) -> bool:
        if not self.is_active:
            return False
        country = _norm(address.get("country"))
        state = _norm(address.get("state"))
        postal = _norm(address.get("postal_code"))

        countries = [_norm(c) for c in (self.countries or [])]
        if countries and country not in countries:
            return False
        states = [_norm(s) for s in (self.states or [])]
        if states and state not in states:
            return False
        prefixes = [_norm(p) for p in (self.postal_prefixes or [])]
        if prefixes and not any(postal.startswith(p) for p in prefixes):
            return False
        return True


class RateType(models.TextChoices):
    FLAT = "flat", "Flat rate"
    FREE = "free", "Free"
    WEIGHT = "weight", "Weight based"
    PRICE = "price", "Price based"


class ShippingMethod(TenantScopedModel):
    zone = models.ForeignKey(ShippingZone, on_delete=models.CASCADE, related_name="methods")
    name = models.CharField(max_length=120)
    carrier = models.CharField(max_length=60, blank=True, help_text="Courier key or label.")
    rate_type = models.CharField(max_length=10, choices=RateType.choices, default=RateType.FLAT)

    base_rate = models.DecimalField(**MONEY)
    per_kg_rate = models.DecimalField(**MONEY)
    free_over = models.DecimalField(null=True, blank=True, max_digits=12, decimal_places=2,
                                    validators=[MinValueValidator(Decimal("0"))],
                                    help_text="Free when order subtotal is at or above this.")
    # Optional applicability bounds.
    min_subtotal = models.DecimalField(null=True, blank=True, max_digits=12, decimal_places=2)
    max_subtotal = models.DecimalField(null=True, blank=True, max_digits=12, decimal_places=2)
    max_weight = models.DecimalField(null=True, blank=True, max_digits=8, decimal_places=3)

    cod_available = models.BooleanField(default=True)
    cod_fee = models.DecimalField(**MONEY)

    min_days = models.PositiveIntegerField(default=2)
    max_days = models.PositiveIntegerField(default=7)

    is_active = models.BooleanField(default=True)
    priority = models.PositiveIntegerField(default=100)

    class Meta:
        ordering = ["priority", "name"]

    def __str__(self):
        return f"{self.name} ({self.zone_id})"

    def is_applicable(self, *, subtotal: Decimal, weight: Decimal, cod: bool) -> bool:
        if not self.is_active:
            return False
        if cod and not self.cod_available:
            return False
        if self.min_subtotal is not None and subtotal < self.min_subtotal:
            return False
        if self.max_subtotal is not None and subtotal > self.max_subtotal:
            return False
        if self.max_weight is not None and weight > self.max_weight:
            return False
        return True

    def quote(self, *, subtotal: Decimal, weight: Decimal, cod: bool = False) -> Decimal | None:
        """Shipping charge for this basket, or None if the method does not apply."""
        if not self.is_applicable(subtotal=subtotal, weight=weight, cod=cod):
            return None
        if self.rate_type == RateType.FREE:
            charge = Decimal("0")
        elif self.free_over is not None and subtotal >= self.free_over:
            charge = Decimal("0")
        elif self.rate_type == RateType.WEIGHT:
            charge = self.base_rate + self.per_kg_rate * weight
        else:  # FLAT / PRICE both use base_rate; PRICE bounds handled above
            charge = self.base_rate
        if cod:
            charge += self.cod_fee
        return charge.quantize(Decimal("0.01"))

    def estimate_label(self) -> str:
        if self.min_days == self.max_days:
            return f"{self.min_days} days"
        return f"{self.min_days}–{self.max_days} days"


class ShipmentStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    LABEL_CREATED = "label_created", "Label created"
    DISPATCHED = "dispatched", "Dispatched"
    IN_TRANSIT = "in_transit", "In transit"
    OUT_FOR_DELIVERY = "out_for_delivery", "Out for delivery"
    DELIVERED = "delivered", "Delivered"
    FAILED = "failed", "Failed"
    RETURNED = "returned", "Returned"


class Shipment(TenantScopedModel):
    order = models.ForeignKey("orders.Order", on_delete=models.CASCADE, related_name="shipments")
    method = models.ForeignKey(ShippingMethod, on_delete=models.SET_NULL, null=True, blank=True, related_name="shipments")
    carrier = models.CharField(max_length=60, blank=True)
    status = models.CharField(max_length=20, choices=ShipmentStatus.choices, default=ShipmentStatus.PENDING, db_index=True)

    tracking_number = models.CharField(max_length=120, blank=True, db_index=True)
    tracking_url = models.URLField(blank=True)
    label_url = models.URLField(blank=True)

    weight = models.DecimalField(max_digits=8, decimal_places=3, default=Decimal("0"))
    notes = models.CharField(max_length=255, blank=True)

    shipped_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    estimated_delivery = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.tracking_number or f"Shipment<{self.pk}>"


class ShipmentItem(TimeStampedModel):
    shipment = models.ForeignKey(Shipment, on_delete=models.CASCADE, related_name="items")
    order_item = models.ForeignKey("orders.OrderItem", on_delete=models.CASCADE, related_name="shipment_items")
    quantity = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.quantity}x item {self.order_item_id}"


class ShipmentEvent(TimeStampedModel):
    shipment = models.ForeignKey(Shipment, on_delete=models.CASCADE, related_name="events")
    status = models.CharField(max_length=20, choices=ShipmentStatus.choices, blank=True)
    description = models.CharField(max_length=255, blank=True)
    location = models.CharField(max_length=120, blank=True)
    occurred_at = models.DateTimeField(null=True, blank=True)
    raw = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-occurred_at", "-created_at", "-id"]

    def __str__(self):
        return f"{self.shipment_id} {self.status}"
