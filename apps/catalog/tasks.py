"""Background product-image optimisation.

Runs on the low-priority ``images`` queue, rate-limited, so crunching a batch of
uploads never starves the app. Uploads already small and in a web format are
recorded as-is — no re-encode, no CPU.
"""

import os
import posixpath

from celery import shared_task
from django.conf import settings
from django.core.files.base import ContentFile
from django.utils import timezone

_RATE_LIMIT = os.environ.get("PRODUCT_IMAGE_RATE_LIMIT", "6/m")


def _stem(name):
    return posixpath.splitext(posixpath.basename(name))[0]


@shared_task(
    name="apps.catalog.tasks.optimize_product_image",
    bind=True,
    queue="images",
    rate_limit=_RATE_LIMIT,
    max_retries=2,
    default_retry_delay=60,
)
def optimize_product_image(self, image_id):
    from apps.media.optimize import process

    from .models import ProductImage

    pi = ProductImage.objects.filter(pk=image_id).first()
    if pi is None or not pi.image or pi.optimized_at is not None:
        return

    source = pi.original if pi.original else pi.image
    source.open("rb")
    try:
        raw = source.read()
    finally:
        source.close()

    try:
        result = process(
            raw,
            target_bytes=settings.PRODUCT_IMAGE_TARGET_KB * 1024,
            max_edge=settings.PRODUCT_IMAGE_MAX_EDGE,
            skip_under_bytes=settings.PRODUCT_IMAGE_SKIP_UNDER_KB * 1024,
        )
    except Exception as exc:  # noqa: BLE001 - transient decode/memory issues retry
        raise self.retry(exc=exc)

    # Already small + web-friendly: keep the file, just record its metrics.
    if result["skipped"]:
        pi.width = result["width"]
        pi.height = result["height"]
        pi.bytes = result["bytes"]
        pi.renditions = {}
        pi.optimized_at = timezone.now()
        pi.save(update_fields=["width", "height", "bytes", "renditions", "optimized_at"])
        return

    storage = pi.image.storage
    stem = _stem(pi.image.name)

    if not pi.original:
        pi.original.save(posixpath.basename(pi.image.name), ContentFile(raw), save=False)

    old_name = pi.image.name
    pi.image.save(f"{stem}.webp", ContentFile(result["main"]), save=False)
    if old_name and old_name != pi.image.name:
        storage.delete(old_name)

    # Drop renditions from a previous run so re-processing doesn't orphan files.
    for width in list(pi.renditions or {}):
        name = f"products/r/{stem}_{width}.webp"
        if storage.exists(name):
            storage.delete(name)

    rend_urls = {}
    for width, data in result["renditions"].items():
        saved = storage.save(f"products/r/{stem}_{width}.webp", ContentFile(data))
        rend_urls[str(width)] = storage.url(saved)

    pi.width = result["width"]
    pi.height = result["height"]
    pi.bytes = result["bytes"]
    pi.renditions = rend_urls
    pi.optimized_at = timezone.now()
    pi.save(
        update_fields=[
            "image", "original", "width", "height", "bytes",
            "renditions", "optimized_at",
        ]
    )
