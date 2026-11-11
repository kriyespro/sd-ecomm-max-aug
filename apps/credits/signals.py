"""Loyalty credit is earned off a real payment success -- never before
(mirrors apps.referrals.signals). Spent credit is released back to the
customer's balance if the order is later cancelled or fails (mirrors
apps.coupons.signals)."""

from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.core.events import Events, domain_event

_RELEASE_STATUSES = {"cancelled", "failed"}


@receiver(domain_event)
def _on_domain_event(sender, event, project, payload, instance=None, **kwargs):
    if event != Events.PAYMENT_SUCCESS or project is None:
        return

    payment = instance
    order = getattr(payment, "order", None)
    if order is None:
        return

    from .services import earn_loyalty_for_payment

    earn_loyalty_for_payment(payment)


@receiver(post_save, sender="orders.Order")
def _release_credit_on_cancel(sender, instance, **kwargs):
    if instance.status in _RELEASE_STATUSES and instance.credit_applied:
        from .services import release_credit_for_cancelled_order

        release_credit_for_cancelled_order(instance)
