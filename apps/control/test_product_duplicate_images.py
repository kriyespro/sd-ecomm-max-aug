"""Duplicating a product used to always copy its images onto the new copy —
but a duplicate is almost always meant to become a *different* product, so
the source's photos got deleted and replaced every single time anyway.
Image copying is now opt-in via a "Copy images too" checkbox on the
Duplicate form, off by default."""

import io

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from apps.accounts.models import Membership
from apps.billing import services as billing_svc
from apps.catalog.models import Product, ProductImage
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.projects.models import Project

User = get_user_model()


def _jpeg(width=200, height=150):
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (width, height), (90, 110, 160)).save(buf, format="JPEG", quality=80)
    return buf.getvalue()


@override_settings(ALLOWED_HOSTS=["*"])
class ProductDuplicateImagesTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="DupCo", status="active", feature_flags={"onboarded": True},
        )
        billing_svc.ensure_subscription(self.project)
        owner = User.objects.create_user("owner", "owner@t.test", "pw", is_staff=True)
        Membership.objects.create(user=owner, project=self.project, role="owner")
        self.client.force_login(owner)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()
        self.product = Product.objects.create(
            project=self.project, title="Ring", slug="ring", status="active", price="999",
        )
        ProductImage.objects.create(
            product=self.product, is_primary=True, order=0,
            image=SimpleUploadedFile("a.jpg", _jpeg(), content_type="image/jpeg"),
        )

    def _duplicate(self, copy_images=False):
        data = {"copy_images": "on"} if copy_images else {}
        return self.client.post(f"/admin/products/{self.product.pk}/duplicate/", data)

    def test_duplicate_without_checkbox_copies_zero_images(self):
        resp = self._duplicate(copy_images=False)
        self.assertEqual(resp.status_code, 302)
        clone = Product.objects.exclude(pk=self.product.pk).get(project=self.project)
        self.assertEqual(clone.images.count(), 0)

    def test_duplicate_with_checkbox_still_copies_images(self):
        resp = self._duplicate(copy_images=True)
        self.assertEqual(resp.status_code, 302)
        clone = Product.objects.exclude(pk=self.product.pk).get(project=self.project)
        self.assertEqual(clone.images.count(), 1)
        self.assertTrue(clone.images.first().is_primary)

    def test_duplicate_always_copies_non_image_fields(self):
        resp = self._duplicate(copy_images=False)
        self.assertEqual(resp.status_code, 302)
        clone = Product.objects.exclude(pk=self.product.pk).get(project=self.project)
        self.assertEqual(clone.title, "Ring (copy)")
        self.assertEqual(str(clone.price), "999.00")

    def test_list_page_shows_the_copy_images_checkbox(self):
        body = self.client.get("/admin/products/").content.decode()
        self.assertIn('name="copy_images"', body)
