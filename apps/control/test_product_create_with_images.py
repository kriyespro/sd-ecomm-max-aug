"""Product create form: images used to be strictly a second-screen-only
step ("save the product first — then you can upload images here"). The
create form is already multipart — a file field there lets a merchant
attach photos in the same Save, one screen instead of two."""

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
    Image.new("RGB", (width, height), (120, 140, 200)).save(buf, format="JPEG", quality=80)
    return buf.getvalue()


@override_settings(ALLOWED_HOSTS=["*"])
class ProductCreateWithImagesTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="ImgCo", status="active", feature_flags={"onboarded": True},
        )
        billing_svc.ensure_subscription(self.project)
        owner = User.objects.create_user("owner", "owner@t.test", "pw", is_staff=True)
        Membership.objects.create(user=owner, project=self.project, role="owner")
        self.client.force_login(owner)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()

    def test_create_form_has_a_file_field(self):
        resp = self.client.get("/admin/products/new/")
        self.assertContains(resp, 'name="images"')
        self.assertContains(resp, "multiple")

    def test_edit_form_does_not_duplicate_the_create_file_field(self):
        product = Product.objects.create(
            project=self.project, title="Existing", slug="existing", status="draft", price="10",
        )
        resp = self.client.get(f"/admin/products/{product.pk}/")
        self.assertNotContains(resp, 'id="id_images"')

    def test_creating_with_an_image_attaches_it_in_the_same_submit(self):
        upload = SimpleUploadedFile("photo.jpg", _jpeg(), content_type="image/jpeg")
        resp = self.client.post("/admin/products/new/", {
            "title": "New Thing", "price": "499", "status": "draft", "kind": "simple",
            "images": [upload],
        }, follow=True)
        self.assertEqual(resp.status_code, 200)
        product = Product.objects.get(title="New Thing")
        self.assertEqual(product.images.count(), 1)
        self.assertTrue(product.images.first().is_primary)

    def test_creating_without_an_image_still_works(self):
        resp = self.client.post("/admin/products/new/", {
            "title": "No Photo Yet", "price": "100", "status": "draft", "kind": "simple",
        }, follow=True)
        self.assertEqual(resp.status_code, 200)
        product = Product.objects.get(title="No Photo Yet")
        self.assertEqual(product.images.count(), 0)

    def test_edit_screen_uploader_still_works_after_the_shared_helper_refactor(self):
        product = Product.objects.create(
            project=self.project, title="EditUpload", slug="edit-upload", status="draft", price="10",
        )
        upload = SimpleUploadedFile("second.jpg", _jpeg(), content_type="image/jpeg")
        resp = self.client.post(
            f"/admin/products/{product.pk}/images/upload/", {"images": [upload]},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(ProductImage.objects.filter(product=product).count(), 1)
