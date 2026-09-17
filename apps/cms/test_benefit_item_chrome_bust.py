"""Regression: BenefitItem (both the "why it works" band and the trust
strip added later — apps.cms.models.BenefitItemKind) was missing from
apps.core.signals' chrome-cache bust list. Every other CMS content type
(Category, Page, Banner, BudgetBand, InstagramItem, ...) busts the
5-minute storefront chrome cache immediately on save; BenefitItem edits
silently sat in the stale cache instead, only ever fixed in tests by
manually calling bust_project_chrome() — which meant no test ever
exercised the real, signal-driven path a merchant's actual edit takes."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.accounts.models import Membership, StoreRole
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.projects.models import Domain, Project

User = get_user_model()


@override_settings(ALLOWED_HOSTS=["*"])
class BenefitItemAutoBustTests(TestCase):
    """No manual bust_project_chrome() call anywhere in this test — that's
    the point. If the signal isn't wired, these fail."""

    def setUp(self):
        from apps.cms.models import Skin, ThemeSettings

        self.project = Project.objects.create(
            name="AutoBustCo", status="active", feature_flags={"onboarded": True},
        )
        Domain.objects.create(project=self.project, host="autobustco.test", is_verified=True)
        self.owner = User.objects.create_user("abowner", "abowner@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.project, user=self.owner, role=StoreRole.OWNER)
        self.client.force_login(self.owner)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()
        # The "why it works" band only exists on botanica2/botanica3's own
        # home.jinja — the "default" skin has no such section at all.
        Skin.objects.filter(is_default=True).update(is_default=False)
        ThemeSettings.objects.update_or_create(
            project=self.project, defaults={"skin": Skin.objects.get(slug="botanica2")},
        )

    def test_creating_a_why_item_via_admin_shows_immediately_on_home(self):
        # Prime the chrome cache with a real request first, so the edit
        # below has to invalidate an existing cache entry, not just fill
        # an empty one.
        first = self.client.get("/", HTTP_HOST="autobustco.test")
        self.assertContains(first, "Whole-plant actives")  # skin default fallback

        self.client.post("/admin/cms/benefits/new/", {
            "kind": "why", "icon": "\U0001F69A", "title": "Same-day dispatch",
            "description": "Before 2pm IST", "order": "1", "is_active": "on",
        })

        second = self.client.get("/", HTTP_HOST="autobustco.test")
        self.assertContains(second, "Same-day dispatch")
        self.assertNotContains(second, "Whole-plant actives")

    def test_creating_a_trust_item_via_admin_shows_immediately_on_home(self):
        from apps.cms.models import Skin, ThemeSettings

        Skin.objects.filter(is_default=True).update(is_default=False)
        ThemeSettings.objects.update_or_create(
            project=self.project, defaults={"skin": Skin.objects.get(slug="botanica3")},
        )
        first = self.client.get("/", HTTP_HOST="autobustco.test")
        self.assertContains(first, "Free shipping")  # trust-strip fallback default

        self.client.post("/admin/cms/benefits/new/", {
            "kind": "trust", "icon": "\U0001F69A", "title": "Ships in 24h",
            "description": "Every business day", "order": "1", "is_active": "on",
        })

        second = self.client.get("/", HTTP_HOST="autobustco.test")
        self.assertContains(second, "Ships in 24h")
        self.assertNotContains(second, "Orders over")  # the old fallback line is gone

    def test_editing_an_existing_item_also_busts(self):
        from apps.cms.models import BenefitItem

        item = BenefitItem.objects.create(
            project=self.project, title="Old copy", description="stale", order=1, is_active=True,
        )
        first = self.client.get("/", HTTP_HOST="autobustco.test")
        self.assertContains(first, "Old copy")

        self.client.post(f"/admin/cms/benefits/{item.pk}/", {
            "kind": "why", "icon": "✦", "title": "Fresh copy", "description": "updated",
            "order": "1", "is_active": "on",
        })

        second = self.client.get("/", HTTP_HOST="autobustco.test")
        self.assertContains(second, "Fresh copy")
        self.assertNotContains(second, "Old copy")

    def test_deleting_an_item_also_busts(self):
        from apps.cms.models import BenefitItem

        item = BenefitItem.objects.create(
            project=self.project, title="Going away", order=1, is_active=True,
        )
        first = self.client.get("/", HTTP_HOST="autobustco.test")
        self.assertContains(first, "Going away")

        self.client.post(f"/admin/cms/benefits/{item.pk}/delete/")

        second = self.client.get("/", HTTP_HOST="autobustco.test")
        self.assertNotContains(second, "Going away")
        self.assertContains(second, "Whole-plant actives")  # falls back cleanly
