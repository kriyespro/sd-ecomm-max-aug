"""Inline-edit a product's title straight from /admin/products/ (pencil icon
next to the title), autosaving on blur — same per-field htmx-POST shape as
the Featured/New/Bestseller flag checkboxes (test_product_flag_toggle.py)."""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.accounts.models import Membership
from apps.billing import services as billing_svc
from apps.catalog.models import Product
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.projects.models import Project

User = get_user_model()


@override_settings(ALLOWED_HOSTS=["*"])
class ProductTitleUpdateTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="TitleCo", status="active", feature_flags={"onboarded": True},
        )
        billing_svc.ensure_subscription(self.project)
        owner = User.objects.create_user("owner", "owner@t.test", "pw", is_staff=True)
        Membership.objects.create(user=owner, project=self.project, role="owner")
        self.client.force_login(owner)
        session = self.client.session
        session[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        session.save()
        self.product = Product.objects.create(
            project=self.project, title="Ring", slug="ring", status="active", price="999",
        )

    def _rename(self, title):
        return self.client.post(
            f"/admin/products/{self.product.pk}/title/", {"title": title},
        )

    def test_list_page_shows_the_inline_edit_markup(self):
        body = self.client.get("/admin/products/").content.decode()
        self.assertIn("title-edit-btn", body)
        self.assertIn(f"/admin/products/{self.product.pk}/title/", body)
        # htmx only serializes a bare (formless) input into the POST body
        # when it has a name attribute — without it the request always
        # arrives with no "title" key, so the server always 400s.
        self.assertIn('name="title"', body)

    def test_posting_a_new_title_updates_the_product(self):
        resp = self._rename("Gold Ring")
        self.assertEqual(resp.status_code, 204)
        self.product.refresh_from_db()
        self.assertEqual(self.product.title, "Gold Ring")

    def test_empty_title_is_rejected(self):
        resp = self._rename("")
        self.assertEqual(resp.status_code, 400)
        self.product.refresh_from_db()
        self.assertEqual(self.product.title, "Ring")

    def test_whitespace_only_title_is_rejected(self):
        resp = self._rename("   ")
        self.assertEqual(resp.status_code, 400)
        self.product.refresh_from_db()
        self.assertEqual(self.product.title, "Ring")

    def test_title_is_stripped(self):
        resp = self._rename("  Gold Ring  ")
        self.assertEqual(resp.status_code, 204)
        self.product.refresh_from_db()
        self.assertEqual(self.product.title, "Gold Ring")

    def test_cannot_rename_another_projects_product(self):
        other = Project.objects.create(name="Other", status="active")
        other_product = Product.objects.create(
            project=other, title="Necklace", slug="necklace", status="active", price="500",
        )
        resp = self.client.post(
            f"/admin/products/{other_product.pk}/title/", {"title": "Hacked"},
        )
        self.assertEqual(resp.status_code, 404)
        other_product.refresh_from_db()
        self.assertEqual(other_product.title, "Necklace")
