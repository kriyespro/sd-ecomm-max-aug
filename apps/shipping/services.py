"""Shipping orchestration.

Owns: matching a zone to an address, quoting methods, attaching a method to an
order (and re-costing it), creating shipments through a courier, and moving the
order forward as the shipment progresses. Couriers never imported by orders.
"""

from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.core.events import Events, emit
from apps.core.models import AuditLog
from apps.core.services import record_audit
from apps.orders import services as orders

from .couriers import get_courier_class
from .models import (
    Shipment,
    ShipmentEvent,
    ShipmentItem,
    ShipmentStatus,
    ShippingMethod,
    ShippingZone,
)

# Shipment status -> the order status we try to move to when it is reached.
_ORDER_SYNC = {
    ShipmentStatus.DISPATCHED: "shipped",
    ShipmentStatus.IN_TRANSIT: "shipped",
    ShipmentStatus.OUT_FOR_DELIVERY: "shipped",
    ShipmentStatus.DELIVERED: "delivered",
    ShipmentStatus.RETURNED: "returned",
}

_TERMINAL = {ShipmentStatus.DELIVERED, ShipmentStatus.FAILED, ShipmentStatus.RETURNED}


class ShippingError(Exception):
    pass


# --- weight / matching -------------------------------------------

def order_weight(order) -> Decimal:
    total = Decimal("0")
    for oi in order.items.select_related("product"):
        unit = getattr(oi.product, "weight", None) or Decimal("0")
        total += Decimal(unit) * oi.quantity
    return total


def matching_zone(project, address: dict):
    for zone in ShippingZone.objects.filter(project=project, is_active=True).order_by("priority", "name"):
        if zone.matches(address or {}):
            return zone
    return None


def available_methods(*, project, address, subtotal, weight=None, cod=False):
    """Return ``[(method, quote_decimal), ...]`` for the address + basket."""
    zone = matching_zone(project, address)
    if zone is None:
        return []
    if weight is None:
        weight = Decimal("0")
    out = []
    for method in zone.methods.filter(is_active=True).order_by("priority", "name"):
        q = method.quote(subtotal=Decimal(subtotal), weight=Decimal(weight), cod=cod)
        if q is not None:
            out.append((method, q))
    return out


def methods_for_order(order, *, cod=None):
    if cod is None:
        cod = order.payment_status != "paid" and any(
            p.provider == "cod" for p in order.payments.all()
        )
    return available_methods(
        project=order.project,
        address=order.shipping_address,
        subtotal=order.subtotal,
        weight=order_weight(order),
        cod=cod,
    )


# --- attach method to order -------------------------------------

@transaction.atomic
def set_order_shipping(*, order, method: ShippingMethod, cod=False, actor=None):
    if method.project_id != order.project_id:
        raise ShippingError("Method belongs to another store.")
    charge = method.quote(subtotal=order.subtotal, weight=order_weight(order), cod=cod)
    if charge is None:
        raise ShippingError("Method does not apply to this order.")

    order.shipping_total = charge
    order.courier = method.carrier or method.name
    order.shipping_method = {
        "id": method.pk,
        "name": method.name,
        "carrier": method.carrier,
        "rate": str(charge),
        "cod": cod,
        "min_days": method.min_days,
        "max_days": method.max_days,
    }
    order.save(update_fields=["shipping_total", "courier", "shipping_method", "updated_at"])
    order.recalc_totals()
    record_audit(actor=actor, project=order.project, action=AuditLog.Action.UPDATE,
                 target=order, changes={"shipping_total": str(charge), "method": method.name})
    return order


# --- shipments -------------------------------------------------

@transaction.atomic
def create_shipment(*, order, method=None, carrier="", tracking_number="",
                    tracking_url="", items=None, actor=None):
    """Create a Shipment. If a method with an integrated courier is given, book
    it at the courier; otherwise it is a manual shipment.
    """
    if method is None and order.shipping_method.get("id"):
        method = ShippingMethod.objects.filter(pk=order.shipping_method["id"], project=order.project).first()

    carrier = carrier or (method.carrier if method else "") or "manual"
    shipment = Shipment.objects.create(
        project=order.project,
        order=order,
        method=method,
        carrier=carrier,
        tracking_number=tracking_number,
        tracking_url=tracking_url,
        weight=order_weight(order),
        status=ShipmentStatus.PENDING,
    )

    # Lines: explicit {order_item_id: qty} or every remaining unit.
    if items:
        for oi in order.items.all():
            qty = int(items.get(oi.pk, items.get(str(oi.pk), 0)) or 0)
            if qty > 0:
                ShipmentItem.objects.create(shipment=shipment, order_item=oi, quantity=qty)
    else:
        for oi in order.items.all():
            if oi.quantity > 0:
                ShipmentItem.objects.create(shipment=shipment, order_item=oi, quantity=oi.quantity)

    courier = get_courier_class(carrier)()
    if courier.integrated:
        result = courier.create_shipment(shipment)
        shipment.tracking_number = result.get("tracking_number", "") or shipment.tracking_number
        shipment.tracking_url = result.get("tracking_url", "") or shipment.tracking_url
        shipment.label_url = result.get("label_url", "") or shipment.label_url

    if method and (method.min_days or method.max_days):
        shipment.estimated_delivery = (timezone.now() + timedelta(days=method.max_days or method.min_days)).date()
    shipment.status = ShipmentStatus.LABEL_CREATED if shipment.tracking_number else ShipmentStatus.PENDING
    shipment.save()

    _event(shipment, status=shipment.status, description="Shipment created")
    record_audit(actor=actor, project=order.project, action=AuditLog.Action.CREATE,
                 target=shipment, request=None)
    payload = orders.order_event_payload(
        order, carrier=shipment.carrier, tracking=shipment.tracking_number,
    )
    transaction.on_commit(
        lambda: emit(Events.SHIPMENT_CREATED, project=order.project, payload=payload, instance=shipment)
    )
    return shipment


def _event(shipment, *, status="", description="", location="", occurred_at=None, raw=None):
    return ShipmentEvent.objects.create(
        shipment=shipment, status=status or "", description=description or "",
        location=location or "", occurred_at=occurred_at or timezone.now(), raw=raw or {},
    )


@transaction.atomic
def update_shipment_status(*, shipment, status, description="", location="",
                           occurred_at=None, raw=None, actor=None):
    status = ShipmentStatus(status)
    shipment.status = status
    fields = ["status", "updated_at"]
    if status == ShipmentStatus.DISPATCHED and not shipment.shipped_at:
        shipment.shipped_at = occurred_at or timezone.now()
        fields.append("shipped_at")
    if status == ShipmentStatus.DELIVERED and not shipment.delivered_at:
        shipment.delivered_at = occurred_at or timezone.now()
        fields.append("delivered_at")
    shipment.save(update_fields=fields)

    _event(shipment, status=status, description=description, location=location,
           occurred_at=occurred_at, raw=raw)

    # Keep the order's free-text shipping_status readable + advance the order.
    order = shipment.order
    order.shipping_status = shipment.get_status_display()
    if shipment.tracking_number and not order.tracking_number:
        order.tracking_number = shipment.tracking_number
    order.save(update_fields=["shipping_status", "tracking_number", "updated_at"])

    target = _ORDER_SYNC.get(status)
    if target:
        try:
            orders.transition_order(order=order, to_status=target, actor=actor,
                                    note=f"Shipment {shipment.tracking_number or shipment.pk}: {status}")
        except orders.OrderError:
            pass  # order not in a state that allows this edge; status text still updated

    record_audit(actor=actor, project=order.project, action=AuditLog.Action.UPDATE,
                 target=shipment, changes={"status": status})

    if status == ShipmentStatus.DELIVERED:
        payload = orders.order_event_payload(order, carrier=shipment.carrier,
                                             tracking=shipment.tracking_number)
        transaction.on_commit(
            lambda: emit(Events.SHIPMENT_DELIVERED, project=order.project,
                         payload=payload, instance=shipment)
        )
    return shipment


def mark_dispatched(*, shipment, actor=None):
    return update_shipment_status(shipment=shipment, status=ShipmentStatus.DISPATCHED, actor=actor)


def mark_delivered(*, shipment, actor=None):
    return update_shipment_status(shipment=shipment, status=ShipmentStatus.DELIVERED, actor=actor)


def add_tracking_event(*, shipment, status="", description="", location="", actor=None):
    if status:
        return update_shipment_status(shipment=shipment, status=status,
                                      description=description, location=location, actor=actor)
    return _event(shipment, description=description, location=location)


# --- courier webhooks ------------------------------------------

@transaction.atomic
def handle_courier_webhook(*, project, courier_key, headers, body: bytes):
    courier = get_courier_class(courier_key)()
    result = courier.parse_webhook(headers, body)
    if not result.signature_valid:
        raise ShippingError("Invalid courier webhook signature.")
    shipment = Shipment.objects.filter(
        project=project, tracking_number=result.tracking_number
    ).first()
    if shipment is None:
        return None
    for ev in result.events:
        if ev.status:
            update_shipment_status(shipment=shipment, status=ev.status,
                                   description=ev.description, location=ev.location,
                                   occurred_at=ev.occurred_at, raw=ev.raw)
        else:
            _event(shipment, description=ev.description, location=ev.location,
                   occurred_at=ev.occurred_at, raw=ev.raw)
    return shipment
