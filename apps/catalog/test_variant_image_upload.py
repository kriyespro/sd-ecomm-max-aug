"""attach_variant_images (the Size/Colour combo gallery uploader) had no size
cap at all -- a single huge upload could make a gunicorn worker grind on one
Pillow decode/encode in the request itself. The re-encode now runs in a
background task (apps.catalog.tasks.optimize_variant_image); see
OptimizeVariantImageTaskTests below for that part."""

import io
from decimal import Decimal
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from apps.catalog.models import Product, ProductKind, Variant, VariantImage
from apps.catalog.variants import attach_variant_images
from apps.projects.models import Project


def _jpeg(width=200, height=150):
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (width, height), (120, 140, 200)).save(buf, format="JPEG", quality=80)
    return buf.getvalue()


def _gradient_png(width=1000, height=700):
    """Big enough (and noisy enough) to clear the 60KB / 800px target this
    gallery compresses to, so the pipeline actually re-encodes instead of
    taking the skip path."""
    from PIL import Image

    img = Image.new("RGB", (width, height))
    px = img.load()
    for x in range(width):
        for y in range(0, height, 4):
            px[x, y] = (x % 256, y % 256, (x + y) % 256)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class AttachVariantImagesTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="Combo Co", status="active")
        self.product = Product.objects.create(
            project=self.project, title="Scarf", price=Decimal("499"),
            status="active", kind=ProductKind.SIMPLE,
        )
        self.variant = Variant.objects.create(product=self.product, name="Red")

    def test_normal_upload_is_attached(self):
        upload = SimpleUploadedFile("red.jpg", _jpeg(), content_type="image/jpeg")
        created = attach_variant_images(self.variant, [upload])
        self.assertEqual(len(created), 1)
        self.assertEqual(VariantImage.objects.filter(variant=self.variant).count(), 1)
        self.variant.refresh_from_db()
        self.assertTrue(self.variant.image)

    def test_oversized_upload_is_skipped(self):
        upload = SimpleUploadedFile("huge.jpg", _jpeg(), content_type="image/jpeg")
        with patch("apps.media.services.MAX_SIZE", 10):
            created = attach_variant_images(self.variant, [upload])
        self.assertEqual(created, [])
        self.assertEqual(VariantImage.objects.filter(variant=self.variant).count(), 0)


class OptimizeVariantImageTaskTests(TestCase):
    """VariantImage.save() used to run shrink_image_field synchronously, in
    the upload request. It's now apps.catalog.tasks.optimize_variant_image,
    enqueued from apps.catalog.signals on commit -- same pattern as
    ProductImage (see ProductImageOptimizeTaskTests in apps/catalog/tests.py)."""

    def setUp(self):
        self.project = Project.objects.create(name="Combo Co 2", status="active")
        self.product = Product.objects.create(
            project=self.project, title="Scarf", price=Decimal("499"),
            status="active", kind=ProductKind.SIMPLE,
        )
        self.variant = Variant.objects.create(product=self.product, name="Blue")

    def test_upload_is_optimized_on_commit_and_cover_resynced(self):
        upload = SimpleUploadedFile("photo.png", _gradient_png(), content_type="image/png")
        with self.captureOnCommitCallbacks(execute=True):
            vi = VariantImage.objects.create(variant=self.variant, image=upload)

        vi.refresh_from_db()
        self.assertIsNotNone(vi.optimized_at)
        self.assertTrue(vi.image.name.endswith(".webp"))
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.image.name, vi.image.name)

    def test_small_image_is_kept_untouched(self):
        upload = SimpleUploadedFile("small.jpg", _jpeg(), content_type="image/jpeg")
        with self.captureOnCommitCallbacks(execute=True):
            vi = VariantImage.objects.create(variant=self.variant, image=upload)

        vi.refresh_from_db()
        self.assertIsNotNone(vi.optimized_at)
        self.assertTrue(vi.image.name.endswith(".jpg"))

    @override_settings(PRODUCT_IMAGE_OPTIMIZE=False)
    def test_optimization_can_be_disabled(self):
        upload = SimpleUploadedFile("photo.png", _gradient_png(), content_type="image/png")
        with self.captureOnCommitCallbacks(execute=True):
            vi = VariantImage.objects.create(variant=self.variant, image=upload)

        vi.refresh_from_db()
        self.assertIsNone(vi.optimized_at)
        self.assertTrue(vi.image.name.endswith(".png"))

    def test_sweep_reenqueues_images_the_worker_dropped(self):
        from datetime import timedelta

        from django.utils import timezone

        from apps.catalog.tasks import sweep_unoptimized_variant_images

        stuck = VariantImage.objects.create(
            variant=self.variant,
            image=SimpleUploadedFile("stuck.png", _gradient_png(), content_type="image/png"),
        )
        VariantImage.objects.filter(pk=stuck.pk).update(
            created_at=timezone.now() - timedelta(hours=2)
        )
        fresh = VariantImage.objects.create(
            variant=self.variant,
            image=SimpleUploadedFile("fresh.png", _gradient_png(), content_type="image/png"),
        )
        self.assertIsNone(stuck.optimized_at)
        self.assertIsNone(fresh.optimized_at)

        swept = sweep_unoptimized_variant_images()

        self.assertEqual(swept, 1)
        stuck.refresh_from_db()
        fresh.refresh_from_db()
        self.assertIsNotNone(stuck.optimized_at)
        self.assertIsNone(fresh.optimized_at)
