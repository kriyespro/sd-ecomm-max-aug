"""Compact Featured/New arrivals/Bestseller checkboxes on /admin/products/ —
lets a merchant pick which home page rail a product shows in without opening
the full product edit form. These three flags were removed from the
create/edit form itself (apps/control/forms.py ProductForm) once this list
toggle existed, to avoid asking for the same thing twice."""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.accounts.models import Membership
from apps.billing import services as billing_svc
from apps.catalog.models import Product
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.projects.models import Project

User = get_user_model()


@override_settings(ALLOWED_HOSTS=["*"])
class ProductFlagToggleTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="FlagToggleCo", status="active", feature_flags={"onboarded": True},
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

    def _toggle(self, flag, checked):
        return self.client.post(
            f"/admin/products/{self.product.pk}/flags/{flag}/",
            {"state": "1"} if checked else {},
        )

    def test_list_page_shows_a_checkbox_per_flag(self):
        body = self.client.get("/admin/products/").content.decode()
        self.assertIn(f"/admin/products/{self.product.pk}/flags/is_featured/", body)
        self.assertIn(f"/admin/products/{self.product.pk}/flags/is_new_arrival/", body)
        self.assertIn(f"/admin/products/{self.product.pk}/flags/is_bestseller/", body)

    def test_checking_the_box_sets_the_flag(self):
        resp = self._toggle("is_featured", checked=True)
        self.assertEqual(resp.status_code, 204)
        self.product.refresh_from_db()
        self.assertTrue(self.product.is_featured)

    def test_unchecking_the_box_clears_the_flag(self):
        self.product.is_featured = True
        self.product.save(update_fields=["is_featured"])
        resp = self._toggle("is_featured", checked=False)
        self.assertEqual(resp.status_code, 204)
        self.product.refresh_from_db()
        self.assertFalse(self.product.is_featured)

    def test_new_arrival_flag_is_independent_of_featured(self):
        self._toggle("is_featured", checked=True)
        self._toggle("is_new_arrival", checked=True)
        self._toggle("is_featured", checked=False)
        self.product.refresh_from_db()
        self.assertFalse(self.product.is_featured)
        self.assertTrue(self.product.is_new_arrival)

    def test_bestseller_flag_toggles_too(self):
        resp = self._toggle("is_bestseller", checked=True)
        self.assertEqual(resp.status_code, 204)
        self.product.refresh_from_db()
        self.assertTrue(self.product.is_bestseller)

    def test_unknown_flag_is_rejected(self):
        resp = self._toggle("is_archived", checked=True)
        self.assertEqual(resp.status_code, 404)

    def test_cannot_toggle_another_projects_product(self):
        other = Project.objects.create(name="Other", status="active")
        other_product = Product.objects.create(
            project=other, title="Necklace", slug="necklace", status="active", price="500",
        )
        resp = self.client.post(
            f"/admin/products/{other_product.pk}/flags/is_featured/", {"state": "1"},
        )
        self.assertEqual(resp.status_code, 404)
        other_product.refresh_from_db()
        self.assertFalse(other_product.is_featured)
