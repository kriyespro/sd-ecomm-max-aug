"""Release reserved coupon usage when an order is cancelled / failed."""

from django.db.models.signals import post_save
from django.dispatch import receiver

_RELEASE_STATUSES = {"cancelled", "failed"}


@receiver(post_save, sender="orders.Order")
def _release_coupon_on_cancel(sender, instance, **kwargs):
    if instance.status in _RELEASE_STATUSES and instance.coupon_redemptions.filter(released=False).exists():
        from .services import release_for_cancelled_order
        release_for_cancelled_order(instance)
