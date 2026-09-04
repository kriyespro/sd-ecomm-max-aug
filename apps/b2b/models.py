"""Cross-tenant B2B / dropship marketplace.

A store owner (never a manager or staff — gated at the view layer via
``StoreRoleRequiredMixin`` with owner-only roles) can:

* Turn their store into a B2B seller (``Project.is_b2b_seller``) and mark
  specific products of theirs as wholesale-available (``B2BListing``).
* Browse other stores' listings and "import" one into their own catalog —
  this creates an independent ``Product`` row in their project, priced at
  ``wholesale_price * (1 + markup_pct / 100)`` (``B2BImport``).

When a customer buys an imported product from the reseller's storefront, the
original seller ships it (dropship). ``B2BOrderLedger`` records, per sold
line item, what the reseller owes the seller — settled outside the platform,
mirroring ``apps.billing.ManagerCommission`` — plus the fulfillment state the
seller updates from their own "B2B orders" screen. The seller has no access
to the reseller's ``Order``/``Customer`` rows, so the shipping address is
snapshotted onto the ledger row at order time.
"""

from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models

from apps.core.models import TimeStampedModel

MONEY = dict(max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0"))])


class B2BListing(TimeStampedModel):
    """A seller's product, made available for other stores to import."""

    product = models.OneToOneField(
        "catalog.Product", on_delete=models.CASCADE, related_name="b2b_listing"
    )
    # Denormalized from product.project — set in save() — so the marketplace
    # query never has to join through Product.
    seller_project = models.ForeignKey(
        "projects.Project", on_delete=models.CASCADE, related_name="b2b_listings"
    )
    wholesale_price = models.DecimalField(**MONEY)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["seller_project", "is_active"])]

    def __str__(self):
        return f"{self.product.title} @ {self.wholesale_price} ({self.seller_project.name})"

    def save(self, *args, **kwargs):
        self.seller_project_id = self.product.project_id
        super().save(*args, **kwargs)


class B2BImport(TimeStampedModel):
    """A reseller's copy of a seller's listing into their own catalog."""

    listing = models.ForeignKey(
        B2BListing, on_delete=models.SET_NULL, null=True, blank=True, related_name="imports"
    )
    # Denormalized from listing.seller_project at import time and kept even if
    # the listing/seller's product is later removed — record_b2b_sale() needs
    # a durable "who to bill" that doesn't depend on the listing surviving.
    # SET_NULL (not CASCADE) so a deleted seller store doesn't also delete this
    # row's own snapshot fields below.
    seller_project = models.ForeignKey(
        "projects.Project", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="b2b_sales",
    )
    buyer_project = models.ForeignKey(
        "projects.Project", on_delete=models.CASCADE, related_name="b2b_imports"
    )
    local_product = models.OneToOneField(
        "catalog.Product", on_delete=models.CASCADE, related_name="b2b_import"
    )
    # Snapshots — survive the source listing/seller being pulled or renamed.
    source_project_name = models.CharField(max_length=120, blank=True)
    source_product_title = models.CharField(max_length=220, blank=True)
    wholesale_price_at_import = models.DecimalField(**MONEY)
    markup_pct = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("0"))

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["buyer_project", "listing"], name="uniq_b2b_import_per_listing"
            ),
        ]

    def __str__(self):
        return f"{self.source_product_title} -> {self.buyer_project.name}"


class B2BLedgerStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    PAID = "paid", "Paid"


class B2BShipStatus(models.TextChoices):
    PENDING = "pending", "Awaiting shipment"
    SHIPPED = "shipped", "Shipped"
    DELIVERED = "delivered", "Delivered"


class B2BOrderLedger(TimeStampedModel):
    """One row per sold line item that came from a B2B import: what the
    reseller owes the seller, and what the seller needs to ship."""

    order_item = models.OneToOneField(
        "orders.OrderItem", on_delete=models.CASCADE, related_name="b2b_ledger"
    )
    import_ref = models.ForeignKey(
        B2BImport, on_delete=models.SET_NULL, null=True, blank=True, related_name="ledger_entries"
    )
    # SET_NULL: this is a financial record — it must outlive either store being
    # deleted later, same reasoning as AuditLog.project.
    seller_project = models.ForeignKey(
        "projects.Project", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="b2b_receivables",
    )
    buyer_project = models.ForeignKey(
        "projects.Project", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="b2b_payables",
    )

    product_title = models.CharField(max_length=220, blank=True)
    quantity = models.PositiveIntegerField(default=1)
    wholesale_unit_price = models.DecimalField(**MONEY)
    amount_owed = models.DecimalField(**MONEY)

    status = models.CharField(
        max_length=10, choices=B2BLedgerStatus.choices, default=B2BLedgerStatus.PENDING, db_index=True
    )
    paid_at = models.DateTimeField(null=True, blank=True)
    payout_ref = models.CharField(max_length=120, blank=True)

    ship_status = models.CharField(
        max_length=10, choices=B2BShipStatus.choices, default=B2BShipStatus.PENDING, db_index=True
    )
    tracking_number = models.CharField(max_length=120, blank=True)
    courier = models.CharField(max_length=120, blank=True)
    shipped_at = models.DateTimeField(null=True, blank=True)

    # Snapshot of Order.shipping_address — the seller has no access to the
    # buyer's Order/Customer rows across the tenant boundary.
    ship_to_name = models.CharField(max_length=200, blank=True)
    ship_to_phone = models.CharField(max_length=32, blank=True)
    ship_to_address = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["seller_project", "ship_status"]),
            models.Index(fields=["buyer_project", "status"]),
        ]

    def __str__(self):
        return f"{self.quantity}x {self.product_title} ({self.seller_project} <- {self.buyer_project})"
