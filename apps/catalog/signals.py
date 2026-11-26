"""Enqueue image optimisation whenever a ProductImage/VariantImage is saved
with a fresh (un-optimised) file. Each task is idempotent — it bails if
``optimized_at`` is already set — so the re-save it triggers does not loop."""

import logging

from django.conf import settings
from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import ProductImage, VariantImage

logger = logging.getLogger(__name__)


def _enqueue(pk):
    from .tasks import optimize_product_image

    try:
        optimize_product_image.delay(pk)
    except Exception:  # noqa: BLE001 — broker hiccup must not 500 the upload
        logger.exception("optimize_product_image.delay(%s) failed to enqueue", pk)


def _enqueue_variant(pk):
    from .tasks import optimize_variant_image

    try:
        optimize_variant_image.delay(pk)
    except Exception:  # noqa: BLE001 — broker hiccup must not 500 the upload
        logger.exception("optimize_variant_image.delay(%s) failed to enqueue", pk)


@receiver(post_save, sender=ProductImage, dispatch_uid="catalog_optimize_product_image")
def _enqueue_optimize(sender, instance, **kwargs):
    if not getattr(settings, "PRODUCT_IMAGE_OPTIMIZE", True):
        return
    if instance.optimized_at is not None or not instance.image:
        return
    pk = instance.pk
    transaction.on_commit(lambda: _enqueue(pk))


@receiver(post_save, sender=VariantImage, dispatch_uid="catalog_optimize_variant_image")
def _enqueue_optimize_variant(sender, instance, **kwargs):
    if not getattr(settings, "PRODUCT_IMAGE_OPTIMIZE", True):
        return
    if instance.optimized_at is not None or not instance.image:
        return
    pk = instance.pk
    transaction.on_commit(lambda: _enqueue_variant(pk))
