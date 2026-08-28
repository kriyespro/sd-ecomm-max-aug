"""Inventory business logic. All stock changes go through here so every change
lands in the movement ledger and counters stay consistent (project.md section 9).
"""

from django.db import transaction

from apps.core.events import Events, emit

from .models import InventoryItem, InventoryTransfer, StockMovement, Warehouse


def default_warehouse(project):
    return (
        Warehouse.objects.filter(project=project, is_active=True)
        .order_by("-is_default", "name")
        .first()
    )


def get_or_create_item(*, warehouse, product, variant=None):
    item, _ = InventoryItem.objects.get_or_create(
        warehouse=warehouse, product=product, variant=variant
    )
    return item


@transaction.atomic
def record_movement(*, item, reason, quantity_delta=0, reserved_delta=0, reference="", note="", actor=None):
    """Apply a delta to an inventory item and append a ledger row.

    ``quantity_delta`` changes physical on-hand; ``reserved_delta`` changes the
    reserved count. Row is locked for the duration to keep counters correct
    under concurrency.
    """
    locked = InventoryItem.objects.select_for_update().get(pk=item.pk)
    locked.quantity = locked.quantity + quantity_delta
    locked.reserved = locked.reserved + reserved_delta
    locked.save(update_fields=["quantity", "reserved", "updated_at"])

    movement = StockMovement.objects.create(
        item=locked,
        reason=reason,
        quantity_delta=quantity_delta,
        reserved_delta=reserved_delta,
        quantity_after=locked.quantity,
        reserved_after=locked.reserved,
        reference=reference,
        note=note,
        actor=actor,
    )

    # Fire a low-stock event only on the transition into "low" caused by a drop.
    if (quantity_delta < 0 or reserved_delta > 0) and locked.is_low:
        prev_available = (locked.quantity - quantity_delta) - (locked.reserved - reserved_delta)
        if prev_available > locked.low_stock_threshold:
            wh = locked.warehouse
            payload = {
                "product": locked.product.title,
                "warehouse": wh.name,
                "available": locked.available,
                "threshold": locked.low_stock_threshold,
                "email": (getattr(wh.project, "notification_config", None) or {}).get("low_stock_email", ""),
            }
            transaction.on_commit(
                lambda: emit(Events.INVENTORY_LOW, project=wh.project, payload=payload, instance=locked)
            )

    return movement


def adjust_stock(*, item, new_quantity, actor=None, note=""):
    """Set on-hand to an absolute value; records the difference as an adjustment."""
    delta = new_quantity - item.quantity
    if delta == 0:
        return None
    return record_movement(
        item=item,
        reason=StockMovement.Reason.ADJUSTMENT,
        quantity_delta=delta,
        note=note,
        actor=actor,
    )


def receive_stock(*, item, quantity, actor=None, reference="", note=""):
    return record_movement(
        item=item,
        reason=StockMovement.Reason.PURCHASE,
        quantity_delta=abs(quantity),
        reference=reference,
        note=note,
        actor=actor,
    )


def reserve(*, item, quantity, reference="", actor=None):
    return record_movement(
        item=item,
        reason=StockMovement.Reason.RESERVE,
        reserved_delta=abs(quantity),
        reference=reference,
        actor=actor,
    )


def release(*, item, quantity, reference="", actor=None):
    return record_movement(
        item=item,
        reason=StockMovement.Reason.RELEASE,
        reserved_delta=-abs(quantity),
        reference=reference,
        actor=actor,
    )


def consume_sale(*, item, quantity, reference="", actor=None):
    """Fulfilment: ship reserved stock. Drops on-hand and the reservation."""
    return record_movement(
        item=item,
        reason=StockMovement.Reason.SALE,
        quantity_delta=-abs(quantity),
        reserved_delta=-abs(quantity),
        reference=reference,
        actor=actor,
    )


def low_stock_items(project, *, warehouse=None):
    qs = (
        InventoryItem.objects.filter(warehouse__project=project, low_stock_threshold__gt=0)
        .select_related("warehouse", "product", "variant")
    )
    if warehouse is not None:
        qs = qs.filter(warehouse=warehouse)
    # available = quantity - reserved <= threshold
    return [i for i in qs if i.available <= i.low_stock_threshold]


def low_stock_count(project):
    return len(low_stock_items(project))


@transaction.atomic
def complete_transfer(transfer: InventoryTransfer, *, actor=None):
    if transfer.status != InventoryTransfer.Status.PENDING:
        return transfer
    src_item = get_or_create_item(
        warehouse=transfer.source, product=transfer.product, variant=transfer.variant
    )
    dst_item = get_or_create_item(
        warehouse=transfer.destination, product=transfer.product, variant=transfer.variant
    )
    ref = f"transfer#{transfer.pk}"
    record_movement(
        item=src_item, reason=StockMovement.Reason.TRANSFER_OUT,
        quantity_delta=-transfer.quantity, reference=ref, actor=actor,
    )
    record_movement(
        item=dst_item, reason=StockMovement.Reason.TRANSFER_IN,
        quantity_delta=transfer.quantity, reference=ref, actor=actor,
    )
    transfer.status = InventoryTransfer.Status.COMPLETED
    transfer.save(update_fields=["status", "updated_at"])
    return transfer
