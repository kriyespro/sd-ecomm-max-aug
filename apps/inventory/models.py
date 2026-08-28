"""Inventory: warehouses, per-location stock, and an append-only movement ledger
(project.md section 9).

Stock lives per (warehouse, product, variant). ``quantity`` is physical on-hand;
``reserved`` is allocated to unfulfilled orders; ``available`` is the difference.
Every change to either number is written as a :class:`StockMovement` row so the
history is auditable and reconstructable.
"""

from django.conf import settings
from django.db import models
from django.utils.text import slugify

from apps.core.models import TimeStampedModel, TenantScopedModel


class Warehouse(TenantScopedModel):
    name = models.CharField(max_length=140)
    code = models.SlugField(max_length=60, blank=True)
    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False)

    address_line1 = models.CharField(max_length=200, blank=True)
    address_line2 = models.CharField(max_length=200, blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=100, blank=True)
    postal_code = models.CharField(max_length=20, blank=True)
    country = models.CharField(max_length=2, blank=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["project", "code"], name="uniq_warehouse_project_code"),
            models.UniqueConstraint(
                fields=["project"],
                condition=models.Q(is_default=True),
                name="uniq_default_warehouse_per_project",
            ),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.code:
            base = slugify(self.name)[:55] or "wh"
            code = base
            i = 2
            qs = Warehouse.objects.filter(project=self.project).exclude(pk=self.pk)
            while qs.filter(code=code).exists():
                code = f"{base}-{i}"
                i += 1
            self.code = code
        super().save(*args, **kwargs)


class InventoryItem(TimeStampedModel):
    warehouse = models.ForeignKey(Warehouse, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(
        "catalog.Product", on_delete=models.CASCADE, related_name="inventory_items"
    )
    variant = models.ForeignKey(
        "catalog.Variant",
        on_delete=models.CASCADE,
        related_name="inventory_items",
        null=True,
        blank=True,
    )

    quantity = models.IntegerField(default=0)
    reserved = models.IntegerField(default=0)
    low_stock_threshold = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["product__title"]
        constraints = [
            models.UniqueConstraint(
                fields=["warehouse", "product", "variant"],
                name="uniq_inventory_wh_product_variant",
            )
        ]
        indexes = [models.Index(fields=["product", "variant"])]

    def __str__(self):
        target = self.variant or self.product
        return f"{target} @ {self.warehouse.name}"

    @property
    def available(self):
        return self.quantity - self.reserved

    @property
    def is_low(self):
        return self.low_stock_threshold > 0 and self.available <= self.low_stock_threshold


class StockMovement(TimeStampedModel):
    class Reason(models.TextChoices):
        PURCHASE = "purchase", "Purchase / restock"
        SALE = "sale", "Sale"
        RETURN = "return", "Customer return"
        DAMAGE = "damage", "Damaged / written off"
        ADJUSTMENT = "adjustment", "Manual adjustment"
        TRANSFER_IN = "transfer_in", "Transfer in"
        TRANSFER_OUT = "transfer_out", "Transfer out"
        RESERVE = "reserve", "Reservation"
        RELEASE = "release", "Reservation released"

    item = models.ForeignKey(InventoryItem, on_delete=models.CASCADE, related_name="movements")
    reason = models.CharField(max_length=20, choices=Reason.choices)
    quantity_delta = models.IntegerField(default=0)
    reserved_delta = models.IntegerField(default=0)
    quantity_after = models.IntegerField()
    reserved_after = models.IntegerField()
    reference = models.CharField(max_length=120, blank=True)
    note = models.CharField(max_length=255, blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="stock_movements",
    )

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["item", "-created_at"])]

    def __str__(self):
        return f"{self.get_reason_display()} {self.quantity_delta:+d} ({self.item_id})"


class InventoryTransfer(TenantScopedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    source = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="transfers_out")
    destination = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="transfers_in")
    product = models.ForeignKey("catalog.Product", on_delete=models.CASCADE, related_name="+")
    variant = models.ForeignKey(
        "catalog.Variant", on_delete=models.CASCADE, related_name="+", null=True, blank=True
    )
    quantity = models.PositiveIntegerField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Transfer {self.quantity} {self.source} -> {self.destination}"
