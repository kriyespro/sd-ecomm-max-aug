"""Create a referral commission off a real payment success -- never before.
Mirrors apps.marketing.signals' receiver shape exactly."""

import logging

from django.dispatch import receiver

from apps.core.events import Events, domain_event

logger = logging.getLogger(__name__)


@receiver(domain_event)
def _on_domain_event(sender, event, project, payload, instance=None, **kwargs):
    if event != Events.PAYMENT_SUCCESS or project is None:
        return

    payment = instance
    order = getattr(payment, "order", None)
    if order is None:
        return

    from .services import create_commission_for_payment

    create_commission_for_payment(payment)
