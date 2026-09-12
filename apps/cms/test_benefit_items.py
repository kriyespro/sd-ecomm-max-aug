"""The home page "Why it works" benefit band is now merchant-editable
(BenefitItem, /admin/cms/benefits/) instead of hardcoded per skin. An empty
list falls back to the skin's built-in defaults so an existing store's home
page never goes blank."""

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.accounts.models import Membership, StoreRole
from apps.cms.models import BenefitItem, Skin, ThemeSettings
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.core.store_resolver import bust_project_chrome
from apps.projects.models import Domain, Project

User = get_user_model()


class BenefitItemAdminTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("bi", "bi@t.test", "pw", is_staff=True)
        self.project = Project.objects.create(
            name="BenefitCo", status="active", feature_flags={"onboarded": True},
        )
        Membership.objects.create(user=self.user, project=self.project,
                                  role=StoreRole.OWNER, is_active=True)
        self.client.force_login(self.user)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()

    def test_list_page_renders(self):
        r = self.client.get("/admin/cms/benefits/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Why it works")

    def test_create_item(self):
        r = self.client.post("/admin/cms/benefits/new/", {
            "icon": "\U0001F69A", "title": "Fast shipping",
            "description": "Dispatched within 24 hours.",
            "order": "1", "is_active": "on",
        })
        self.assertEqual(r.status_code, 302)
        item = BenefitItem.objects.get(project=self.project, title="Fast shipping")
        self.assertEqual(item.description, "Dispatched within 24 hours.")

    def test_edit_and_delete_item(self):
        item = BenefitItem.objects.create(
            project=self.project, title="Old title", description="old", order=1,
        )
        r = self.client.post(f"/admin/cms/benefits/{item.pk}/", {
            "icon": "✦", "title": "New title", "description": "new",
            "order": "1", "is_active": "on",
        })
        self.assertEqual(r.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.title, "New title")

        r = self.client.post(f"/admin/cms/benefits/{item.pk}/delete/")
        self.assertEqual(r.status_code, 302)
        self.assertFalse(BenefitItem.objects.filter(pk=item.pk).exists())

    def test_another_stores_items_are_not_editable_here(self):
        other = Project.objects.create(name="OtherCo", status="active")
        item = BenefitItem.objects.create(project=other, title="Not yours")
        r = self.client.post(f"/admin/cms/benefits/{item.pk}/", {
            "icon": "x", "title": "hacked", "order": "1", "is_active": "on",
        })
        self.assertEqual(r.status_code, 404)


class BenefitItemStorefrontTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="BenefitShop", status="active", feature_flags={"onboarded": True},
        )
        Domain.objects.create(project=self.project, host="benefit.test", is_verified=True)

    def _use_skin(self, slug):
        Skin.objects.filter(is_default=True).update(is_default=False)
        ThemeSettings.objects.update_or_create(
            project=self.project, defaults={"skin": Skin.objects.get(slug=slug)},
        )
        bust_project_chrome(self.project.pk)

    def test_botanica2_falls_back_to_defaults_when_no_items(self):
        self._use_skin("botanica2")
        resp = self.client.get("/", HTTP_HOST="benefit.test")
        self.assertContains(resp, "Whole-plant actives")

    def test_botanica2_shows_owner_items_instead_of_defaults(self):
        self._use_skin("botanica2")
        BenefitItem.objects.create(
            project=self.project, icon="\U0001F69A", title="Fast shipping",
            description="Dispatched within 24 hours.", order=1, is_active=True,
        )
        BenefitItem.objects.create(
            project=self.project, title="Easy returns", order=2, is_active=True,
        )
        bust_project_chrome(self.project.pk)
        resp = self.client.get("/", HTTP_HOST="benefit.test")
        self.assertContains(resp, "Fast shipping")
        self.assertContains(resp, "Dispatched within 24 hours.")
        self.assertContains(resp, "Easy returns")
        self.assertNotContains(resp, "Whole-plant actives")

    def test_inactive_items_are_excluded(self):
        self._use_skin("botanica2")
        BenefitItem.objects.create(project=self.project, title="Hidden", is_active=False)
        bust_project_chrome(self.project.pk)
        resp = self.client.get("/", HTTP_HOST="benefit.test")
        # no active items -> falls back to the skin defaults, not a blank section
        self.assertContains(resp, "Whole-plant actives")
        self.assertNotContains(resp, "Hidden")

    def test_botanica3_shows_owner_items(self):
        self._use_skin("botanica3")
        BenefitItem.objects.create(
            project=self.project, title="Secure checkout", description="256-bit encryption.",
            order=1, is_active=True,
        )
        bust_project_chrome(self.project.pk)
        resp = self.client.get("/", HTTP_HOST="benefit.test")
        self.assertContains(resp, "Secure checkout")
        self.assertNotContains(resp, "Whole-plant actives")
