"""Periodic maintenance for Mission Control — right now, emptying the Trash."""

import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from .trash import TRASH_RETENTION_DAYS, purge_product

logger = logging.getLogger(__name__)


@shared_task(name="apps.control.tasks.purge_trashed_task")
def purge_trashed_task():
    """Hard-delete products and media assets trashed more than
    ``TRASH_RETENTION_DAYS`` ago."""
    from apps.catalog.models import Product
    from apps.media import services as media_svc
    from apps.media.models import MediaAsset

    cutoff = timezone.now() - timedelta(days=TRASH_RETENTION_DAYS)
    products = kept = 0
    for p in Product.objects.filter(trashed_at__isnull=False, trashed_at__lt=cutoff):
        if purge_product(p):
            products += 1
        else:
            kept += 1

    assets = 0
    for a in MediaAsset.objects.filter(trashed_at__isnull=False, trashed_at__lt=cutoff):
        try:
            media_svc.delete_asset(a)
            assets += 1
        except Exception:  # noqa: BLE001
            logger.exception("purge: media asset %s failed", a.pk)

    logger.info("trash purge: %s products, %s assets removed (%s products still cart-locked)",
                products, assets, kept)
    return {"products": products, "assets": assets, "kept": kept}
