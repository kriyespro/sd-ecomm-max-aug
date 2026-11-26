"""Background product-image optimisation.

Runs on the low-priority ``images`` queue, rate-limited, so crunching a batch of
uploads never starves the app. Uploads already small and in a web format are
recorded as-is — no re-encode, no CPU.
"""

import os
import posixpath
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.core.files.base import ContentFile
from django.utils import timezone

_RATE_LIMIT = os.environ.get("PRODUCT_IMAGE_RATE_LIMIT", "12/m")


def _stem(name):
    return posixpath.splitext(posixpath.basename(name))[0]


@shared_task(
    name="apps.catalog.tasks.optimize_product_image",
    bind=True,
    queue="images",
    rate_limit=_RATE_LIMIT,
    max_retries=2,
    default_retry_delay=60,
    # Nothing used to bound how long one image could occupy a worker slot.
    # With concurrency=2 and prefetch-multiplier=1 (docker-compose.yml), one
    # pathological upload (huge resolution, slow disk) stalled the whole
    # `images` queue behind it. SoftTimeLimitExceeded during `process()` is
    # just another Exception to the retry below, so this still gets the
    # normal 2 retries before giving up -- it only caps each attempt.
    soft_time_limit=180,
    time_limit=240,
    # By default Celery acks a task before it runs. A worker killed or
    # restarted mid-task (every deploy does this) silently loses it forever --
    # optimized_at stays null and nothing ever retries, so the image is stuck
    # "optimizing" in the admin panel indefinitely. The task re-checks
    # optimized_at up front, so redelivery is a safe no-op, not double work.
    acks_late=True,
    reject_on_worker_lost=True,
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


@shared_task(
    name="apps.catalog.tasks.optimize_variant_image",
    bind=True,
    queue="images",
    rate_limit=_RATE_LIMIT,
    soft_time_limit=180,
    time_limit=240,
    acks_late=True,
    reject_on_worker_lost=True,
)
def optimize_variant_image(self, image_id):
    from apps.catalog.variants import sync_variant_cover
    from apps.media.services import shrink_image_field

    from .models import VariantImage

    vi = VariantImage.objects.filter(pk=image_id).select_related("variant").first()
    if vi is None or not vi.image or vi.optimized_at is not None:
        return

    # shrink_image_field already treats a bad/unreadable image as a no-op
    # (logs and leaves the file alone) rather than raising -- nothing here
    # needs its own retry.
    shrink_image_field(vi.image, target_kb=60, max_edge=800)
    vi.optimized_at = timezone.now()
    vi.save(update_fields=["image", "optimized_at"])
    # The renamed .webp file may be the gallery's primary shot -- Variant.image
    # is a denormalised copy of that filename, read by the storefront swap
    # picker and prefill thumbnail; keep it in sync.
    sync_variant_cover(vi.variant)


@shared_task(name="apps.catalog.tasks.sweep_unoptimized_variant_images", queue="images")
def sweep_unoptimized_variant_images():
    """Self-heal counterpart to ``sweep_unoptimized_product_images`` -- same
    lost-broker-message / worker-killed-mid-task gap, for the Size/Colour
    gallery instead of the main product gallery."""
    from .models import VariantImage

    cutoff = timezone.now() - timedelta(minutes=30)
    stale_ids = list(
        VariantImage.objects.filter(optimized_at__isnull=True, created_at__lt=cutoff)
        .exclude(image="")
        .order_by("created_at")
        .values_list("pk", flat=True)[:200]
    )
    for pk in stale_ids:
        optimize_variant_image.delay(pk)
    return len(stale_ids)


@shared_task(name="apps.catalog.tasks.sweep_unoptimized_product_images", queue="images")
def sweep_unoptimized_product_images():
    """Self-heal for images that fell through: a lost broker publish (now
    guarded, see apps.catalog.signals), a worker killed mid-task before
    ``acks_late`` was added here, or any other way optimized_at never got
    set. 30 min old gives any just-uploaded, still-queued image plenty of
    room before being swept up again. Capped per run -- this re-enters the
    same rate-limited queue, it isn't meant to drain a big backlog instantly."""
    from .models import ProductImage

    cutoff = timezone.now() - timedelta(minutes=30)
    stale_ids = list(
        ProductImage.objects.filter(optimized_at__isnull=True, created_at__lt=cutoff)
        .exclude(image="")
        .order_by("created_at")
        .values_list("pk", flat=True)[:200]
    )
    for pk in stale_ids:
        optimize_product_image.delay(pk)
    return len(stale_ids)
