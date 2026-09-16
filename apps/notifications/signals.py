"""Map domain events to customer notifications."""

from django.dispatch import receiver

from apps.core.events import Events, domain_event

from .models import Event

# domain event -> (notification event, recipient-key, context builder)
_MAP = {
    Events.ORDER_CREATED: Event.ORDER_CONFIRMATION,
    Events.ORDER_CANCELLED: Event.ORDER_CANCELLED,
    Events.PAYMENT_SUCCESS: Event.PAYMENT_CONFIRMATION,
    Events.PAYMENT_REFUNDED: Event.REFUND,
    Events.SHIPMENT_CREATED: Event.SHIPMENT,
    Events.SHIPMENT_DELIVERED: Event.DELIVERY,
    Events.CUSTOMER_CREATED: Event.WELCOME,
    Events.INVENTORY_LOW: Event.LOW_STOCK_ALERT,
}

# Events whose recipient is the store OWNER, not a customer — resolved via
# apps.projects.services.owner_notification_email instead of payload["email"].
_OWNER_FACING = {Events.INVENTORY_LOW}


@receiver(domain_event)
def _on_domain_event(sender, event, project, payload, instance=None, **kwargs):
    notif_event = _MAP.get(event)
    if notif_event is None or project is None:
        return
    from .tasks import send_notification_task

    if event in _OWNER_FACING:
        from apps.projects.services import owner_notification_email

        to = owner_notification_email(project)
    else:
        to = payload.get("email") or payload.get("to") or ""
    context = {
        "name": payload.get("name") or payload.get("customer_name") or "there",
        "store_name": project.name,
        "order_number": str(payload.get("order_number", "")),
        "currency": str(payload.get("currency", project.currency)),
        "total": str(payload.get("total", "")),
        "amount": str(payload.get("amount", "")),
        "carrier": str(payload.get("carrier", "")),
        "tracking": str(payload.get("tracking", "")),
        "product": str(payload.get("product", "")),
        "warehouse": str(payload.get("warehouse", "")),
        "available": str(payload.get("available", "")),
        "threshold": str(payload.get("threshold", "")),
        "event": event,
    }
    label = instance._meta.label if instance is not None else ""
    pk = str(instance.pk) if instance is not None else ""
    send_notification_task.delay(project.id, notif_event, to, context, label, pk)

    _maybe_whatsapp(project, notif_event, payload, context, label, pk)


def _maybe_whatsapp(project, notif_event, payload, context, label, pk):
    """Also send the event over WhatsApp when the store has an active account
    and the customer left a phone number. ``send_transactional`` still no-ops
    cleanly if no WhatsApp template is configured for this event."""
    phone = payload.get("phone") or payload.get("customer_phone") or ""
    if not phone:
        return
    try:
        from apps.whatsapp.models import account_for
        from apps.whatsapp.tasks import send_whatsapp_task
    except Exception:  # noqa: BLE001 - app not installed
        return
    if account_for(project) is None:
        return
    send_whatsapp_task.delay(project.id, notif_event, phone, context, label, pk)
