"""Order lifecycle logic.

Rules enforced here:
- Placing an order reserves stock (inventory RESERVE movements).
- Cancelling / failing / returning an unshipped order releases that reservation.
- Fulfilling an order consumes the reserved stock (inventory SALE movements).
- Every state change writes an OrderStatusEvent and an AuditLog row.
Status transitions follow the machine in project.md section 10.
"""

from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.crypto import get_random_string

from apps.core.events import Events, emit
from apps.core.models import AuditLog
from apps.core.services import record_audit
from apps.inventory import services as inv

from .models import (
    FulfillmentStatus,
    Order,
    OrderItem,
    OrderStatus,
    OrderStatusEvent,
    PaymentStatus,
)

# Allowed status transitions. Terminal states map to an empty set.
TRANSITIONS = {
    OrderStatus.PENDING: {OrderStatus.CONFIRMED, OrderStatus.CANCELLED, OrderStatus.FAILED},
    OrderStatus.CONFIRMED: {OrderStatus.PROCESSING, OrderStatus.CANCELLED},
    OrderStatus.PROCESSING: {OrderStatus.PACKED, OrderStatus.CANCELLED},
    OrderStatus.PACKED: {OrderStatus.SHIPPED, OrderStatus.CANCELLED},
    OrderStatus.SHIPPED: {OrderStatus.DELIVERED, OrderStatus.RETURNED},
    OrderStatus.DELIVERED: {OrderStatus.RETURNED, OrderStatus.REFUNDED},
    OrderStatus.CANCELLED: set(),
    OrderStatus.FAILED: set(),
    OrderStatus.RETURNED: {OrderStatus.REFUNDED},
    OrderStatus.REFUNDED: set(),
}

# Statuses whose reservation should be released (stock never left the building).
_RELEASE_ON = {OrderStatus.CANCELLED, OrderStatus.FAILED, OrderStatus.RETURNED}


class OrderError(Exception):
    pass


def _generate_number(project):
    prefix = (project.slug or "ord")[:6].upper().replace("-", "")
    return f"{prefix}-{get_random_string(7, '0123456789')}"


def order_event_payload(order, **extra):
    name = (order.shipping_address or {}).get("name") or (order.billing_address or {}).get("name") or ""
    data = {
        "order_number": order.number,
        "email": order.email,
        "name": name,
        "currency": order.currency,
        "total": str(order.grand_total),
        "status": order.status,
        "payment_status": order.payment_status,
    }
    data.update(extra)
    return data


def _log_event(order, *, kind, from_value="", to_value="", note="", actor=None):
    return OrderStatusEvent.objects.create(
        order=order, kind=kind, from_value=from_value or "",
        to_value=to_value or "", note=note or "", actor=actor,
    )


def _item_inventory(order, order_item):
    """Inventory row backing this order line at the order's warehouse (or default)."""
    if order_item.product_id is None:
        return None
    warehouse = order.warehouse or inv.default_warehouse(order.project)
    if warehouse is None:
        return None
    return inv.get_or_create_item(
        warehouse=warehouse, product=order_item.product, variant=order_item.variant
    )


@transaction.atomic
def _reserve_stock(order, *, actor=None):
    if order.stock_reserved:
        return
    for oi in order.items.select_related("product", "variant"):
        item = _item_inventory(order, oi)
        if item is not None:
            inv.reserve(item=item, quantity=oi.quantity, reference=f"order#{order.number}", actor=actor)
    order.stock_reserved = True
    order.save(update_fields=["stock_reserved", "updated_at"])


@transaction.atomic
def _release_stock(order, *, actor=None):
    if not order.stock_reserved:
        return
    for oi in order.items.select_related("product", "variant"):
        item = _item_inventory(order, oi)
        if item is not None:
            inv.release(item=item, quantity=oi.remaining_quantity or oi.quantity,
                        reference=f"order#{order.number}", actor=actor)
    order.stock_reserved = False
    order.save(update_fields=["stock_reserved", "updated_at"])


@transaction.atomic
def place_order(
    *, project, cart, email, billing_address, shipping_address,
    phone="", customer_note="", warehouse=None, actor=None, user=None,
):
    """Turn a cart into a PENDING order and reserve stock. Deactivates the cart."""
    items = list(cart.items.select_related("product", "variant"))
    if not items:
        raise OrderError("Cart is empty.")

    order = None
    for _ in range(5):
        try:
            with transaction.atomic():
                order = Order.objects.create(
                    project=project,
                    number=_generate_number(project),
                    user=user if (user is not None and getattr(user, "is_authenticated", False)) else None,
                    email=email,
                    phone=phone or "",
                    currency=project.currency,
                    billing_address=billing_address or {},
                    shipping_address=shipping_address or {},
                    warehouse=warehouse or inv.default_warehouse(project),
                    customer_note=customer_note or "",
                    status=OrderStatus.PENDING,
                    payment_status=PaymentStatus.PENDING,
                )
            break
        except IntegrityError:
            order = None
    if order is None:
        raise OrderError("Could not allocate an order number, try again.")

    from apps.b2b.services import record_b2b_sale

    for ci in items:
        unit = ci.unit_price
        order_item = OrderItem.objects.create(
            order=order,
            product=ci.product,
            variant=ci.variant,
            product_title=ci.product.title,
            variant_name=(ci.variant.name if ci.variant else ""),
            sku=(ci.variant.sku if ci.variant and ci.variant.sku else ci.product.sku),
            unit_price=unit,
            quantity=ci.quantity,
            line_total=unit * ci.quantity,
        )
        record_b2b_sale(order_item)

    order.recalc_totals()
    _reserve_stock(order, actor=actor)

    cart.is_active = False
    cart.converted_order_id = order.pk
    cart.save(update_fields=["is_active", "converted_order_id", "updated_at"])

    _log_event(order, kind=OrderStatusEvent.Kind.STATUS, to_value=OrderStatus.PENDING, actor=actor)
    record_audit(actor=actor, project=project, action=AuditLog.Action.CREATE,
                 target=order, request=None, changes={"number": order.number, "total": str(order.grand_total)})
    transaction.on_commit(
        lambda: emit(Events.ORDER_CREATED, project=project, payload=order_event_payload(order), instance=order)
    )
    return order


@transaction.atomic
def transition_order(*, order, to_status, actor=None, note=""):
    to_status = OrderStatus(to_status)
    current = OrderStatus(order.status)
    if to_status == current:
        return order
    if to_status not in TRANSITIONS.get(current, set()):
        raise OrderError(f"Cannot move order from {current} to {to_status}.")

    if to_status in _RELEASE_ON:
        _release_stock(order, actor=actor)

    order.status = to_status
    order.save(update_fields=["status", "updated_at"])
    _log_event(order, kind=OrderStatusEvent.Kind.STATUS,
               from_value=current, to_value=to_status, note=note, actor=actor)
    record_audit(actor=actor, project=order.project, action=AuditLog.Action.UPDATE,
                 target=order, changes={"status": [current, to_status]})

    evt = Events.ORDER_CANCELLED if to_status == OrderStatus.CANCELLED else Events.ORDER_UPDATED
    payload = order_event_payload(order, from_status=str(current), to_status=str(to_status))
    transaction.on_commit(
        lambda: emit(evt, project=order.project, payload=payload, instance=order)
    )
    return order


@transaction.atomic
def mark_paid(*, order, actor=None, reference="", amount=None):
    prev = order.payment_status
    order.payment_status = PaymentStatus.PAID
    if not order.placed_at:
        order.placed_at = timezone.now()
    updates = ["payment_status", "placed_at", "updated_at"]
    # First payment on a pending order confirms it.
    if order.status == OrderStatus.PENDING:
        order.status = OrderStatus.CONFIRMED
        updates.append("status")
        _log_event(order, kind=OrderStatusEvent.Kind.STATUS,
                   from_value=OrderStatus.PENDING, to_value=OrderStatus.CONFIRMED, actor=actor)
    order.save(update_fields=updates)
    _log_event(order, kind=OrderStatusEvent.Kind.PAYMENT,
               from_value=prev, to_value=PaymentStatus.PAID, note=reference, actor=actor)
    record_audit(actor=actor, project=order.project, action=AuditLog.Action.UPDATE,
                 target=order, changes={"payment_status": [prev, PaymentStatus.PAID]})
    return order


@transaction.atomic
def fulfill_order(*, order, actor=None, note=""):
    """Ship everything still outstanding: consume reserved stock, mark fulfilled."""
    changed = False
    for oi in order.items.select_related("product", "variant"):
        qty = oi.remaining_quantity
        if qty <= 0:
            continue
        item = _item_inventory(order, oi)
        if item is not None:
            inv.consume_sale(item=item, quantity=qty, reference=f"order#{order.number}", actor=actor)
        oi.fulfilled_quantity = oi.quantity
        oi.save(update_fields=["fulfilled_quantity", "updated_at"])
        changed = True

    prev = order.fulfillment_status
    order.fulfillment_status = FulfillmentStatus.FULFILLED
    order.stock_reserved = False
    order.save(update_fields=["fulfillment_status", "stock_reserved", "updated_at"])
    _log_event(order, kind=OrderStatusEvent.Kind.FULFILLMENT,
               from_value=prev, to_value=FulfillmentStatus.FULFILLED, note=note, actor=actor)
    record_audit(actor=actor, project=order.project, action=AuditLog.Action.UPDATE,
                 target=order, changes={"fulfillment_status": [prev, FulfillmentStatus.FULFILLED]})
    return order


@transaction.atomic
def cancel_order(*, order, actor=None, note=""):
    return transition_order(order=order, to_status=OrderStatus.CANCELLED, actor=actor, note=note)


@transaction.atomic
def add_admin_note(*, order, text, actor=None):
    text = (text or "").strip()
    if not text:
        return None
    stamp = timezone.now().strftime("%Y-%m-%d %H:%M")
    line = f"[{stamp}] {text}"
    order.admin_note = f"{order.admin_note}\n{line}".strip() if order.admin_note else line
    order.save(update_fields=["admin_note", "updated_at"])
    return _log_event(order, kind=OrderStatusEvent.Kind.NOTE, note=text, actor=actor)


def allowed_transitions(order):
    return sorted(TRANSITIONS.get(OrderStatus(order.status), set()))
