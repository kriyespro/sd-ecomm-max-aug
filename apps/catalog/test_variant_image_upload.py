"""attach_variant_images (the Size/Colour combo gallery uploader) had no size
cap at all. VariantImage.save() runs shrink_image_field synchronously, in the
same request -- so a single huge upload here didn't just slow a background
task, it could make a gunicorn worker grind on one Pillow decode/encode for
the whole request."""

import io
from decimal import Decimal
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from apps.catalog.models import Product, ProductKind, Variant, VariantImage
from apps.catalog.variants import attach_variant_images
from apps.projects.models import Project


def _jpeg(width=200, height=150):
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (width, height), (120, 140, 200)).save(buf, format="JPEG", quality=80)
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
