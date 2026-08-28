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
}


@receiver(domain_event)
def _on_domain_event(sender, event, project, payload, instance=None, **kwargs):
    notif_event = _MAP.get(event)
    if notif_event is None or project is None:
        return
    from .tasks import send_notification_task

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
        "event": event,
    }
    label = instance._meta.label if instance is not None else ""
    pk = str(instance.pk) if instance is not None else ""
    send_notification_task.delay(project.id, notif_event, to, context, label, pk)
