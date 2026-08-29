"""Enqueue image optimisation whenever a ProductImage is saved with a fresh
(un-optimised) file. The task itself is idempotent — it bails if ``optimized_at``
is already set — so the re-save it triggers does not loop."""

from django.conf import settings
from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import ProductImage


@receiver(post_save, sender=ProductImage, dispatch_uid="catalog_optimize_product_image")
def _enqueue_optimize(sender, instance, **kwargs):
    if not getattr(settings, "PRODUCT_IMAGE_OPTIMIZE", True):
        return
    if instance.optimized_at is not None or not instance.image:
        return
    pk = instance.pk
    from .tasks import optimize_product_image

    transaction.on_commit(lambda: optimize_product_image.delay(pk))
