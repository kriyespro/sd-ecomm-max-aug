import io
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings

from apps.catalog.models import Product, ProductImage, Variant
from apps.media import optimize as optimize_module
from apps.media.optimize import optimize, process, renditions
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

    def test_skip_path_never_decodes_pixels(self):
        """Regression: process() decoded + exif-transposed + converted every
        upload before checking whether to skip, so the documented 'already
        small and web-friendly costs no CPU' path still paid a full Pillow
        decode every time. _load (the real decode) must not run when the
        header check alone says skip."""
        raw = _jpeg(width=200, height=150)
        with patch.object(optimize_module, "_load", side_effect=AssertionError(
            "full decode ran on the skip path"
        )):
            out = process(raw, target_bytes=200 * 1024, max_edge=2048)
        self.assertTrue(out["skipped"])

    def test_renditions_of_a_real_photo_skip_the_wasted_lossless_pass(self):
        """Regression: _fit_budget tried a 5th, lossless encode whenever the
        lossy quality ladder missed budget -- not just for small/icon images.
        Rendition budgets are quadratically tiny (see process()), so a real
        downscaled photo missed budget on nearly every rendition and paid for
        a lossless encode that, for photo content, can never win. That 5th
        encode per rendition was the main cost behind slow post-upload
        optimisation. A large photo's renditions must stick to the 4-probe
        lossy ladder only."""
        # Noise, not the gradient helper -- incompressible, so every quality
        # probe misses the tiny rendition budget and the ladder never breaks
        # early. That's what forces the (otherwise rare) old lossless
        # fallback on every real photo's small renditions. 512x512 is well
        # above _SMALL_PIXELS, so this mirrors a real rendition-sized crop.
        from PIL import Image

        im = Image.effect_noise((512, 512), 60).convert("RGB")
        with patch.object(
            optimize_module, "_encode", wraps=optimize_module._encode
        ) as spy:
            optimize_module._fit_budget(im, 24 * 1024)  # tight rendition budget
        # 4 quality probes, no trailing lossless=True call.
        self.assertEqual(spy.call_count, 4)
        self.assertTrue(all(not c.kwargs.get("lossless") for c in spy.call_args_list))

    def test_skip_path_reports_exif_rotated_dimensions(self):
        """Regression: the skip path now reads width/height from the file
        header instead of the exif-transposed decode, so a portrait phone
        photo stored sideways (EXIF orientation 6) with landscape raw pixel
        dimensions must still report portrait width/height, matching what
        _load's exif_transpose would have produced."""
        from PIL import Image

        buf = io.BytesIO()
        im = _gradient(600, 400)  # raw pixels: landscape
        exif = Image.Exif()
        exif[0x0112] = 6  # "rotate 90 CW to display" -> portrait on screen
        im.save(buf, format="JPEG", quality=80, exif=exif)
        raw = buf.getvalue()

        out = process(raw, target_bytes=200 * 1024, max_edge=2048)
        self.assertTrue(out["skipped"])
        self.assertEqual((out["width"], out["height"]), (400, 600))


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
