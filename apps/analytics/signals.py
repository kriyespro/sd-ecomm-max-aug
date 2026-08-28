"""Recompute today's roll-up when order/payment events fire."""

from django.dispatch import receiver

from apps.core.events import domain_event

_ORDER_EVENTS = {
    "order.created", "order.updated", "order.cancelled",
    "payment.success", "payment.refunded",
}


@receiver(domain_event)
def _on_domain_event(sender, event, project, payload, instance=None, **kwargs):
    if project is None or event not in _ORDER_EVENTS:
        return
    from .services import rebuild_daily

    rebuild_daily(project)
