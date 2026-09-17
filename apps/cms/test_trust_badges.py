"""The home page trust strip (Free shipping / COD / 7-day returns / Clean
formulas — the compact badge row right under the hero) used to be hardcoded
per skin with a comment telling a developer to hand-edit the template. Now
owner-editable via the same BenefitItem model as "Why it works", split by
BenefitItem.kind ("why" / "trust") since they're different bands on the
page with different default copy."""

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.accounts.models import Membership, StoreRole
from apps.cms.models import BenefitItem, BenefitItemKind, Skin, ThemeSettings
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.core.store_resolver import bust_project_chrome
from apps.projects.models import Domain, Project

User = get_user_model()


class TrustBadgeAdminTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("tb", "tb@t.test", "pw", is_staff=True)
        self.project = Project.objects.create(
            name="TrustCo", status="active", feature_flags={"onboarded": True},
        )
        Membership.objects.create(user=self.user, project=self.project,
                                  role=StoreRole.OWNER, is_active=True)
        self.client.force_login(self.user)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()

    def test_why_tab_is_the_default(self):
        resp = self.client.get("/admin/cms/benefits/")
        self.assertContains(resp, "Section heading")  # only shown on the why tab

    def test_trust_tab_hides_the_why_heading_field(self):
        resp = self.client.get("/admin/cms/benefits/?kind=trust")
        self.assertNotContains(resp, "Section heading")

    def test_create_trust_item_defaults_to_trust_kind_from_the_tab(self):
        resp = self.client.get("/admin/cms/benefits/new/?kind=trust")
        self.assertContains(resp, 'value="trust" selected')

    def test_create_trust_item(self):
        self.client.post("/admin/cms/benefits/new/", {
            "kind": "trust", "icon": "\U0001F69A", "title": "Free shipping",
            "description": "Orders over ₹999", "order": "1", "is_active": "on",
        })
        item = BenefitItem.objects.get(project=self.project, title="Free shipping")
        self.assertEqual(item.kind, BenefitItemKind.TRUST)

    def test_why_and_trust_items_are_listed_separately(self):
        BenefitItem.objects.create(project=self.project, title="Whole-plant actives", kind=BenefitItemKind.WHY)
        BenefitItem.objects.create(project=self.project, title="Free shipping", kind=BenefitItemKind.TRUST)

        why_resp = self.client.get("/admin/cms/benefits/")
        self.assertContains(why_resp, "Whole-plant actives")
        self.assertNotContains(why_resp, "Free shipping")

        trust_resp = self.client.get("/admin/cms/benefits/?kind=trust")
        self.assertContains(trust_resp, "Free shipping")
        self.assertNotContains(trust_resp, "Whole-plant actives")

    def test_another_stores_items_are_not_editable_here(self):
        other = Project.objects.create(name="OtherTrustCo", status="active")
        item = BenefitItem.objects.create(project=other, title="Not yours", kind=BenefitItemKind.TRUST)
        resp = self.client.post(f"/admin/cms/benefits/{item.pk}/", {
            "kind": "trust", "icon": "x", "title": "hacked", "order": "1", "is_active": "on",
        })
        self.assertEqual(resp.status_code, 404)


class TrustBadgeStorefrontTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="TrustShop", status="active", feature_flags={"onboarded": True},
        )
        Domain.objects.create(project=self.project, host="trustshop.test", is_verified=True)

    def _use_skin(self, slug):
        Skin.objects.filter(is_default=True).update(is_default=False)
        ThemeSettings.objects.update_or_create(
            project=self.project, defaults={"skin": Skin.objects.get(slug=slug)},
        )
        bust_project_chrome(self.project.pk)

    def test_botanica3_falls_back_to_defaults_when_no_trust_items(self):
        self._use_skin("botanica3")
        resp = self.client.get("/", HTTP_HOST="trustshop.test")
        self.assertContains(resp, "Free shipping")
        self.assertContains(resp, "Orders over")

    def test_botanica3_shows_owner_trust_items_instead_of_defaults(self):
        self._use_skin("botanica3")
        BenefitItem.objects.create(
            project=self.project, icon="\U0001F69A", title="Same-day dispatch",
            description="Before 2pm IST", order=1, is_active=True, kind=BenefitItemKind.TRUST,
        )
        bust_project_chrome(self.project.pk)
        resp = self.client.get("/", HTTP_HOST="trustshop.test")
        self.assertContains(resp, "Same-day dispatch")
        self.assertContains(resp, "Before 2pm IST")
        self.assertNotContains(resp, "Orders over")

    def test_trust_items_never_leak_into_the_why_it_works_band(self):
        self._use_skin("botanica3")
        BenefitItem.objects.create(
            project=self.project, title="Free shipping", kind=BenefitItemKind.TRUST, is_active=True,
        )
        bust_project_chrome(self.project.pk)
        resp = self.client.get("/", HTTP_HOST="trustshop.test")
        # "Why it works" band still falls back to its own defaults, unaffected
        self.assertContains(resp, "Whole-plant actives")

    def test_botanica2_also_reads_trust_badges(self):
        self._use_skin("botanica2")
        BenefitItem.objects.create(
            project=self.project, title="Ships in 24h", kind=BenefitItemKind.TRUST, is_active=True,
        )
        bust_project_chrome(self.project.pk)
        resp = self.client.get("/", HTTP_HOST="trustshop.test")
        self.assertContains(resp, "Ships in 24h")
