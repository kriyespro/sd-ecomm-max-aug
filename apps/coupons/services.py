"""Coupon validation, discount quoting, and apply/release against an order.

Usage is *reserved* the moment a coupon is applied to an order (``used_count``
bumped, a redemption row created) and released if the order is cancelled — so a
limited coupon can't be over-redeemed during a checkout race.
"""

from decimal import Decimal

from django.db import transaction
from django.db.models import F

from apps.core.models import AuditLog
from apps.core.services import record_audit

from .models import Coupon, CouponRedemption, DiscountType

CENTS = Decimal("0.01")


class CouponError(Exception):
    pass


def _get(project, code):
    code = (code or "").strip().upper()
    if not code:
        raise CouponError("Enter a coupon code.")
    try:
        return Coupon.objects.get(project=project, code=code)
    except Coupon.DoesNotExist:
        raise CouponError("That coupon code is not valid.")


def _eligible_item_total(coupon, order_items):
    """Subtotal of the lines a product/category coupon applies to."""
    if coupon.applies_to == "all":
        return sum((oi.line_total for oi in order_items), Decimal("0"))
    if coupon.applies_to == "products":
        ids = set(coupon.products.values_list("id", flat=True))
        return sum((oi.line_total for oi in order_items if oi.product_id in ids), Decimal("0"))
    if coupon.applies_to == "categories":
        cat_ids = set(coupon.categories.values_list("id", flat=True))
        return sum(
            (oi.line_total for oi in order_items
             if oi.product and oi.product.category_id in cat_ids),
            Decimal("0"),
        )
    return Decimal("0")


def validate_coupon(*, project, code, subtotal, customer_email="", customer=None, is_first_order=None):
    coupon = _get(project, code)
    if not coupon.is_active:
        raise CouponError("This coupon is no longer active.")
    if not coupon.is_scheduled_now:
        raise CouponError("This coupon is not currently valid.")
    if coupon.is_exhausted:
        raise CouponError("This coupon has reached its usage limit.")
    if subtotal < coupon.min_order_amount:
        raise CouponError(f"Minimum order of {coupon.min_order_amount} required.")

    if coupon.first_order_only and is_first_order is False:
        raise CouponError("This coupon is for first orders only.")

    if coupon.customer_groups.exists():
        group_id = getattr(customer, "group_id", None)
        if group_id not in set(coupon.customer_groups.values_list("id", flat=True)):
            raise CouponError("This coupon is not available for your account.")

    if coupon.usage_limit_per_customer and customer_email:
        used = coupon.redemptions.filter(
            customer_email=customer_email.strip().lower(), released=False
        ).count()
        if used >= coupon.usage_limit_per_customer:
            raise CouponError("You have already used this coupon.")
    return coupon


def quote_discount(coupon, *, order_items, subtotal, shipping_total=Decimal("0")):
    """Discount amount for this coupon against the given order state."""
    if coupon.discount_type == DiscountType.FREE_SHIPPING:
        return Decimal(shipping_total).quantize(CENTS)

    base = _eligible_item_total(coupon, order_items)
    if base <= 0:
        return Decimal("0")

    if coupon.discount_type == DiscountType.PERCENT:
        amount = base * coupon.value / Decimal("100")
        if coupon.max_discount is not None:
            amount = min(amount, coupon.max_discount)
    else:  # FIXED
        amount = min(coupon.value, base)

    return amount.quantize(CENTS)


@transaction.atomic
def apply_to_order(*, order, code, actor=None):
    items = list(order.items.select_related("product"))
    customer = getattr(order, "customer", None)
    is_first = None
    if customer is not None:
        is_first = customer.orders_count <= 1

    coupon = validate_coupon(
        project=order.project, code=code, subtotal=order.subtotal,
        customer_email=order.email, customer=customer, is_first_order=is_first,
    )
    discount = quote_discount(
        coupon, order_items=items, subtotal=order.subtotal, shipping_total=order.shipping_total,
    )
    if discount <= 0:
        raise CouponError("This coupon does not apply to anything in the order.")

    # Drop any previous redemption for this order before re-applying.
    _release_order_redemptions(order)

    order.coupon_code = coupon.code
    order.discount_total = discount
    order.save(update_fields=["coupon_code", "discount_total", "updated_at"])
    order.recalc_totals()

    Coupon.objects.filter(pk=coupon.pk).update(used_count=F("used_count") + 1)
    CouponRedemption.objects.create(
        coupon=coupon, order=order, customer=customer,
        customer_email=order.email.strip().lower(), amount=discount,
    )
    record_audit(actor=actor, project=order.project, action=AuditLog.Action.UPDATE,
                 target=order, changes={"coupon": coupon.code, "discount": str(discount)})
    return discount


@transaction.atomic
def _release_order_redemptions(order):
    for red in order.coupon_redemptions.select_related("coupon").filter(released=False):
        Coupon.objects.filter(pk=red.coupon_id, used_count__gt=0).update(used_count=F("used_count") - 1)
        red.released = True
        red.save(update_fields=["released"])


@transaction.atomic
def remove_from_order(*, order, actor=None):
    _release_order_redemptions(order)
    order.coupon_code = ""
    order.discount_total = Decimal("0")
    order.save(update_fields=["coupon_code", "discount_total", "updated_at"])
    order.recalc_totals()


def release_for_cancelled_order(order):
    """Called when an order is cancelled/failed — free the reserved usage."""
    _release_order_redemptions(order)
