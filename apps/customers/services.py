"""Customer logic: create/link from an order, recompute stats + segment."""

from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Count, Max, Sum
from django.utils import timezone

from apps.core.events import Events, emit

from .models import Customer, CustomerGroup, Segment

# Order statuses that count as real revenue.
_REVENUE_STATUSES = {"confirmed", "processing", "packed", "shipped", "delivered"}
VIP_ORDER_COUNT = 5
HIGH_VALUE_TOTAL = Decimal("25000")
INACTIVE_DAYS = 180


def default_group(project):
    return CustomerGroup.objects.filter(project=project, is_default=True).first()


@transaction.atomic
def get_or_create_customer(*, project, email, user=None, first_name="", last_name="", phone=""):
    email = (email or "").strip().lower()
    if not email:
        raise ValueError("Customer email is required.")
    customer, created = Customer.objects.get_or_create(
        project=project, email=email,
        defaults={
            "user": user if (user is not None and getattr(user, "is_authenticated", False)) else None,
            "first_name": first_name, "last_name": last_name, "phone": phone,
            "group": default_group(project),
        },
    )
    changed = []
    if not created:
        if user is not None and getattr(user, "is_authenticated", False) and customer.user_id is None:
            customer.user = user
            changed.append("user")
        for field, value in (("first_name", first_name), ("last_name", last_name), ("phone", phone)):
            if value and not getattr(customer, field):
                setattr(customer, field, value)
                changed.append(field)
        if changed:
            customer.save(update_fields=changed + ["updated_at"])

    if created:
        payload = {"email": customer.email, "name": customer.full_name or "there",
                   "customer_id": customer.pk}
        transaction.on_commit(
            lambda: emit(Events.CUSTOMER_CREATED, project=project, payload=payload, instance=customer)
        )
    return customer


def _segment_for(*, orders_count, total_spent, last_order_at):
    if orders_count == 0:
        return Segment.NEW
    if last_order_at and last_order_at < timezone.now() - timedelta(days=INACTIVE_DAYS):
        return Segment.INACTIVE
    if total_spent >= HIGH_VALUE_TOTAL:
        return Segment.HIGH_VALUE
    if orders_count >= VIP_ORDER_COUNT:
        return Segment.VIP
    if orders_count > 1:
        return Segment.RETURNING
    return Segment.NEW


@transaction.atomic
def sync_customer_stats(customer):
    from apps.orders.models import Order

    agg = (
        Order.objects.filter(project=customer.project, email=customer.email,
                             status__in=_REVENUE_STATUSES)
        .aggregate(n=Count("id"), spent=Sum("grand_total"), last=Max("placed_at"))
    )
    customer.orders_count = agg["n"] or 0
    customer.total_spent = agg["spent"] or Decimal("0")
    customer.last_order_at = agg["last"]
    customer.segment = _segment_for(
        orders_count=customer.orders_count,
        total_spent=customer.total_spent,
        last_order_at=customer.last_order_at,
    )
    customer.save(update_fields=["orders_count", "total_spent", "last_order_at", "segment", "updated_at"])
    return customer


@transaction.atomic
def attach_customer(order, *, actor=None):
    """Link an order to a Customer row (creating it) and refresh stats."""
    name = (order.shipping_address or {}).get("name", "") or (order.billing_address or {}).get("name", "")
    first, _, last = name.partition(" ")
    customer = get_or_create_customer(
        project=order.project, email=order.email, user=order.user,
        first_name=first, last_name=last, phone=order.phone,
    )
    if order.customer_id != customer.pk:
        order.customer = customer
        order.save(update_fields=["customer", "updated_at"])
    sync_customer_stats(customer)
    return customer


def set_blocked(*, customer, blocked, actor=None):
    customer.is_blocked = blocked
    customer.is_active = not blocked
    customer.save(update_fields=["is_blocked", "is_active", "updated_at"])
    return customer
