"""B2B / dropship marketplace business logic. Views stay thin.

Owner-only everywhere (never manager/staff) — enforced here too, in addition
to the view-level ``StoreRoleRequiredMixin`` gate, matching the
defense-in-depth pattern used by ``apps.accounts.team``.
"""

from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts.models import StoreRole
from apps.accounts.permissions import assert_store_role
from apps.catalog.models import Product, ProductImage, ProductKind, ProductStatus
from apps.core.models import AuditLog
from apps.core.services import record_audit

from .models import B2BImport, B2BLedgerStatus, B2BListing, B2BOrderLedger, B2BShipStatus

OWNER_ONLY = frozenset({StoreRole.OWNER})


class B2BError(Exception):
    """Expected, user-facing problem."""


def _require_owner(actor, project):
    assert_store_role(
        actor, project, OWNER_ONLY, "Only the store owner can manage B2B / wholesale."
    )


def set_b2b_seller(*, project, enabled, actor, request=None):
    """Owner toggles their store as a wholesale/dropship seller."""
    _require_owner(actor, project)
    enabled = bool(enabled)
    if project.is_b2b_seller == enabled:
        return project
    project.is_b2b_seller = enabled
    project.save(update_fields=["is_b2b_seller", "updated_at"])
    record_audit(
        actor=actor, project=project, action=AuditLog.Action.UPDATE, target=project,
        changes={"is_b2b_seller": enabled}, request=request,
    )
    return project


def my_listings(project):
    return (
        B2BListing.objects.filter(seller_project=project)
        .select_related("product").order_by("-created_at")
    )


def create_or_update_listing(*, project, actor, product, wholesale_price, request=None):
    """Mark (or re-price) one of the owner's own products as B2B-available."""
    _require_owner(actor, project)
    if product.project_id != project.pk:
        raise B2BError("That product isn't in this store.")
    try:
        wholesale_price = Decimal(wholesale_price)
    except (InvalidOperation, TypeError):
        raise B2BError("Enter a valid wholesale price.")
    if wholesale_price <= 0:
        raise B2BError("Wholesale price must be greater than zero.")

    listing, created = B2BListing.objects.update_or_create(
        product=product, defaults={"wholesale_price": wholesale_price, "is_active": True},
    )
    record_audit(
        actor=actor, project=project,
        action=AuditLog.Action.CREATE if created else AuditLog.Action.UPDATE,
        target=listing, changes={"wholesale_price": str(wholesale_price)}, request=request,
    )
    return listing


def set_listing_active(*, listing, active, actor, request=None):
    _require_owner(actor, listing.seller_project)
    active = bool(active)
    if listing.is_active == active:
        return listing
    listing.is_active = active
    listing.save(update_fields=["is_active", "updated_at"])
    record_audit(
        actor=actor, project=listing.seller_project, action=AuditLog.Action.UPDATE,
        target=listing, changes={"is_active": active}, request=request,
    )
    return listing


def marketplace_listings(*, exclude_project=None):
    """Active listings from every B2B-enabled seller, newest first."""
    qs = (
        B2BListing.objects.filter(is_active=True, seller_project__is_b2b_seller=True)
        .select_related("product", "product__category", "seller_project")
        .prefetch_related("product__images")
    )
    if exclude_project is not None:
        qs = qs.exclude(seller_project=exclude_project)
    return qs.order_by("-created_at")


def imported_listing_ids(buyer_project):
    """Listing pks ``buyer_project`` has already imported — lets the
    marketplace screen swap "Import" for "Already imported"."""
    return set(
        B2BImport.objects.filter(buyer_project=buyer_project)
        .exclude(listing__isnull=True)
        .values_list("listing_id", flat=True)
    )


@transaction.atomic
def import_listing(*, listing, buyer_project, actor, markup_pct, request=None):
    """Copy a seller's listed product into the buyer's own catalog as a new,
    independent ``Product`` (draft, uncategorized — the buyer's category tree
    is their own). Images are shared by storage path, not re-uploaded:
    ``ProductImage`` deletion never touches the underlying file (there is no
    delete signal on it), so two projects can safely reference the same file.
    """
    _require_owner(actor, buyer_project)
    if not listing.is_active or not listing.seller_project.is_b2b_seller:
        raise B2BError("This listing is no longer available.")
    if listing.seller_project_id == buyer_project.pk:
        raise B2BError("You can't import your own listing.")
    if B2BImport.objects.filter(buyer_project=buyer_project, listing=listing).exists():
        raise B2BError("You've already imported this product.")
    try:
        markup_pct = Decimal(markup_pct or 0)
    except InvalidOperation:
        raise B2BError("Enter a valid markup percentage.")
    if markup_pct < 0:
        raise B2BError("Markup can't be negative.")

    source = listing.product
    price = (listing.wholesale_price * (Decimal("1") + markup_pct / Decimal("100"))).quantize(
        Decimal("0.01")
    )
    try:
        with transaction.atomic():
            # Variants (options like size/color) aren't copied yet — land as a
            # simple product so it's never sellable-but-optionless.
            local = Product.objects.create(
                project=buyer_project,
                title=source.title,
                short_description=source.short_description,
                description=source.description,
                kind=ProductKind.SIMPLE,
                price=price,
                status=ProductStatus.DRAFT,
                weight=source.weight, length=source.length, width=source.width, height=source.height,
            )
            for img in source.images.all():
                ProductImage.objects.create(
                    product=local,
                    image=img.image.name,
                    original=img.original.name if img.original else "",
                    alt=img.alt, order=img.order, is_primary=img.is_primary,
                    width=img.width, height=img.height, bytes=img.bytes,
                    renditions=img.renditions, optimized_at=img.optimized_at,
                )

            b2b_import = B2BImport.objects.create(
                listing=listing, seller_project=listing.seller_project, buyer_project=buyer_project,
                local_product=local, source_project_name=listing.seller_project.name,
                source_product_title=source.title, wholesale_price_at_import=listing.wholesale_price,
                markup_pct=markup_pct,
            )
    except IntegrityError:
        raise B2BError("You've already imported this product.")
    record_audit(
        actor=actor, project=buyer_project, action=AuditLog.Action.CREATE, target=local,
        changes={"b2b_imported_from": listing.seller_project.name}, request=request,
    )
    return b2b_import


def record_b2b_sale(order_item):
    """Called from ``apps.orders.services.place_order`` for every line item
    once the order exists. A no-op unless the product is a B2B import.
    Idempotent, mirroring ``apps.billing.services._accrue_commission``."""
    if hasattr(order_item, "b2b_ledger"):
        return None
    product = order_item.product
    if product is None:
        return None
    b2b_import = getattr(product, "b2b_import", None)
    if b2b_import is None:
        return None

    order = order_item.order
    addr = order.shipping_address or {}
    unit_price = b2b_import.wholesale_price_at_import
    return B2BOrderLedger.objects.create(
        order_item=order_item, import_ref=b2b_import,
        seller_project=b2b_import.seller_project, buyer_project=b2b_import.buyer_project,
        product_title=order_item.product_title, quantity=order_item.quantity,
        wholesale_unit_price=unit_price, amount_owed=unit_price * order_item.quantity,
        ship_to_name=addr.get("name", ""), ship_to_phone=addr.get("phone", ""),
        ship_to_address=addr,
    )


def orders_to_fulfill(seller_project):
    return (
        B2BOrderLedger.objects.filter(seller_project=seller_project)
        .select_related("buyer_project").order_by("ship_status", "-created_at")
    )


def payables(buyer_project):
    return (
        B2BOrderLedger.objects.filter(buyer_project=buyer_project)
        .select_related("seller_project").order_by("status", "-created_at")
    )


def _require_ledger_party(actor, ledger):
    """Either side of the trade (or a platform admin, via ``has_store_role``'s
    own bypass) may operate on a ledger row from their own store's screen."""
    for project in (ledger.seller_project, ledger.buyer_project):
        if project is not None:
            try:
                _require_owner(actor, project)
                return
            except PermissionDenied:
                continue
    raise PermissionDenied("You're not a party to this order.")


def mark_shipped(*, ledger, tracking_number, courier, actor, request=None):
    if ledger.seller_project is None:
        raise B2BError("The seller's store is no longer on the platform.")
    _require_owner(actor, ledger.seller_project)
    ledger.ship_status = B2BShipStatus.SHIPPED
    ledger.tracking_number = (tracking_number or "").strip()[:120]
    ledger.courier = (courier or "").strip()[:120]
    ledger.shipped_at = timezone.now()
    ledger.save(update_fields=[
        "ship_status", "tracking_number", "courier", "shipped_at", "updated_at",
    ])
    record_audit(
        actor=actor, project=ledger.seller_project, action=AuditLog.Action.UPDATE,
        target=ledger, changes={"ship_status": "shipped"}, request=request,
    )
    return ledger


def mark_paid(*, ledger, payout_ref, actor, request=None):
    _require_ledger_party(actor, ledger)
    ledger.status = B2BLedgerStatus.PAID
    ledger.paid_at = timezone.now()
    ledger.payout_ref = (payout_ref or "").strip()[:120]
    ledger.save(update_fields=["status", "paid_at", "payout_ref", "updated_at"])
    record_audit(
        actor=actor, project=ledger.buyer_project, action=AuditLog.Action.UPDATE,
        target=ledger, changes={"status": "paid"}, request=request,
    )
    return ledger
