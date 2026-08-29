import io

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings

from apps.catalog.models import Product, ProductImage, Variant
from apps.media.optimize import optimize, renditions
from apps.projects.models import Project


def _gradient(width, height):
    from PIL import Image

    r = Image.linear_gradient("L")
    g = Image.linear_gradient("L").transpose(Image.Transpose.ROTATE_90)
    b = Image.linear_gradient("L").transpose(Image.Transpose.ROTATE_180)
    return Image.merge("RGB", (r, g, b)).resize((width, height))


def _png(width=2600, height=1740):
    """A smooth gradient scaled up — cheap to build, realistic to compress."""
    buf = io.BytesIO()
    _gradient(width, height).save(buf, format="PNG")
    return buf.getvalue()


def _jpeg(width=600, height=400):
    """A small JPEG that the pipeline should keep untouched."""
    buf = io.BytesIO()
    _gradient(width, height).save(buf, format="JPEG", quality=80)
    return buf.getvalue()


class OptimizeTests(SimpleTestCase):
    def test_optimize_reencodes_webp_under_budget_and_clamps_dimensions(self):
        out = optimize(_png(), target_bytes=200 * 1024, max_edge=2048)

        self.assertEqual(out["format"], "webp")
        self.assertTrue(out["data"].startswith(b"RIFF"))
        self.assertLessEqual(out["bytes"], 200 * 1024)
        self.assertLessEqual(max(out["width"], out["height"]), 2048)

    def test_renditions_only_smaller_than_source(self):
        rends = renditions(_png(width=1200, height=800))

        self.assertIn(512, rends)
        self.assertIn(1024, rends)
        self.assertNotIn(2048, rends)  # source is 1200 wide
        for data in rends.values():
            self.assertTrue(data.startswith(b"RIFF"))


class AutoSlugSkuTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="Store")

    def test_product_slug_and_sku_autogenerate(self):
        p = Product.objects.create(project=self.project, title="Blue Running Shoes")
        self.assertEqual(p.slug, "blue-running-shoes")
        self.assertTrue(p.sku.startswith("BRS-"))

    def test_explicit_values_are_kept(self):
        p = Product.objects.create(
            project=self.project, title="Widget", slug="my-slug", sku="MINE-1"
        )
        self.assertEqual(p.slug, "my-slug")
        self.assertEqual(p.sku, "MINE-1")

    def test_slug_stays_unique_per_project(self):
        a = Product.objects.create(project=self.project, title="Same Name")
        b = Product.objects.create(project=self.project, title="Same Name")
        self.assertNotEqual(a.slug, b.slug)
        self.assertNotEqual(a.sku, b.sku)

    def test_variant_sku_derives_from_product(self):
        p = Product.objects.create(project=self.project, title="Tee")
        v1 = Variant.objects.create(product=p, name="S")
        v2 = Variant.objects.create(product=p, name="M")
        self.assertEqual(v1.sku, f"{p.sku}-1")
        self.assertEqual(v2.sku, f"{p.sku}-2")


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class ProductImageOptimizeTaskTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="Test Store")
        self.product = Product.objects.create(project=self.project, title="Widget")

    def test_upload_is_optimized_on_commit(self):
        upload = SimpleUploadedFile("photo.png", _png(), content_type="image/png")
        with self.captureOnCommitCallbacks(execute=True):
            img = ProductImage.objects.create(product=self.product, image=upload)

        img.refresh_from_db()
        self.assertIsNotNone(img.optimized_at)
        self.assertTrue(img.image.name.endswith(".webp"))
        self.assertTrue(img.original.name)  # master kept
        self.assertGreater(img.bytes, 0)
        self.assertTrue(img.renditions)
        self.assertIn(f"{img.width}w", img.srcset)

    def test_small_web_image_is_kept_untouched(self):
        raw = _jpeg()
        upload = SimpleUploadedFile("small.jpg", raw, content_type="image/jpeg")
        with self.captureOnCommitCallbacks(execute=True):
            img = ProductImage.objects.create(product=self.product, image=upload)

        img.refresh_from_db()
        self.assertIsNotNone(img.optimized_at)
        self.assertTrue(img.image.name.endswith(".jpg"))   # not re-encoded
        self.assertFalse(img.original.name)                # no master copy needed
        self.assertTrue(img.kept_original)
        self.assertEqual(img.renditions, {})
        self.assertEqual(img.bytes, len(raw))

    @override_settings(PRODUCT_IMAGE_OPTIMIZE=False)
    def test_optimization_can_be_disabled(self):
        upload = SimpleUploadedFile("photo.png", _png(), content_type="image/png")
        with self.captureOnCommitCallbacks(execute=True):
            img = ProductImage.objects.create(product=self.product, image=upload)

        img.refresh_from_db()
        self.assertIsNone(img.optimized_at)
        self.assertTrue(img.image.name.endswith(".png"))
