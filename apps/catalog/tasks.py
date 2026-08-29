"""Background product-image optimisation.

Keeps image crunching out of the request path: the upload is saved as-is, then
``optimize_product_image`` re-encodes it to a compact WebP, stashes the master
in ``ProductImage.original`` and generates responsive renditions.
"""

import posixpath

from celery import shared_task
from django.conf import settings
from django.core.files.base import ContentFile
from django.utils import timezone


def _stem(name):
    return posixpath.splitext(posixpath.basename(name))[0]


@shared_task(
    name="apps.catalog.tasks.optimize_product_image",
    bind=True,
    max_retries=2,
    default_retry_delay=30,
)
def optimize_product_image(self, image_id):
    from apps.media.optimize import optimize, renditions

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

    target = settings.PRODUCT_IMAGE_TARGET_KB * 1024
    max_edge = settings.PRODUCT_IMAGE_MAX_EDGE
    try:
        main = optimize(raw, target_bytes=target, max_edge=max_edge)
        variant_bytes = renditions(raw, target_bytes=target)
    except Exception as exc:  # noqa: BLE001 - transient decode/memory issues retry
        raise self.retry(exc=exc)

    storage = pi.image.storage
    stem = _stem(pi.image.name)

    # Preserve the untouched upload once, before we overwrite ``image``.
    if not pi.original:
        pi.original.save(posixpath.basename(pi.image.name), ContentFile(raw), save=False)

    old_name = pi.image.name
    pi.image.save(f"{stem}.webp", ContentFile(main["data"]), save=False)
    if old_name and old_name != pi.image.name:
        storage.delete(old_name)

    # Drop renditions from a previous run so re-processing doesn't orphan files.
    # Names are reconstructed by convention (they all live under products/r/).
    for width in list(pi.renditions or {}):
        name = f"products/r/{stem}_{width}.webp"
        if storage.exists(name):
            storage.delete(name)

    rend_urls = {}
    for width, data in variant_bytes.items():
        saved = storage.save(f"products/r/{stem}_{width}.webp", ContentFile(data))
        rend_urls[str(width)] = storage.url(saved)

    pi.width = main["width"]
    pi.height = main["height"]
    pi.bytes = main["bytes"]
    pi.renditions = rend_urls
    pi.optimized_at = timezone.now()
    pi.save(
        update_fields=[
            "image", "original", "width", "height", "bytes",
            "renditions", "optimized_at",
        ]
    )
