"""Plan-limit usage + upgrade CTA — Products, Domains, Team all show
"used / cap" and a clickable "Upgrade plan" link once a store is actually
at its plan's limit (previously Team had passive text only; Products and
Domains showed nothing at all)."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.accounts.models import Membership, StoreRole
from apps.catalog.models import Product
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.projects.models import Domain, Project

User = get_user_model()


@override_settings(ALLOWED_HOSTS=["*"])
class PlanLimitNudgeTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="LimitCo", status="active", feature_flags={"onboarded": True},
        )
        self.owner = User.objects.create_user("o", "o@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.project, user=self.owner, role=StoreRole.OWNER)
        self.client.force_login(self.owner)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()

    def _plan(self):
        return self.project.subscription.plan

    def test_products_shows_no_nudge_under_cap(self):
        plan = self._plan()
        plan.max_products = 100
        plan.save(update_fields=["max_products"])
        resp = self.client.get("/admin/products/")
        self.assertContains(resp, "Products: 0 / 100 used")
        self.assertNotContains(resp, "Upgrade plan")

    def test_products_shows_upgrade_cta_at_cap(self):
        plan = self._plan()
        plan.max_products = 1
        plan.save(update_fields=["max_products"])
        Product.objects.create(
            project=self.project, title="Only One", slug="only-one",
            price=Decimal("100"), status="active",
        )
        resp = self.client.get("/admin/products/")
        self.assertContains(resp, "Products: 1 / 1 used")
        self.assertContains(resp, "Upgrade plan")
        self.assertContains(resp, f'href="{"/admin/plan/"}"')

    def test_domains_shows_upgrade_cta_at_cap(self):
        plan = self._plan()
        plan.max_custom_domains = 1
        plan.save(update_fields=["max_custom_domains"])
        Domain.objects.create(project=self.project, host="brand.test")
        resp = self.client.get("/admin/domains/")
        self.assertContains(resp, "Domains: 1 / 1 used")
        self.assertContains(resp, "Upgrade plan")

    def test_domains_no_nudge_when_no_cap_set(self):
        plan = self._plan()
        plan.max_custom_domains = None
        plan.save(update_fields=["max_custom_domains"])
        resp = self.client.get("/admin/domains/")
        self.assertNotContains(resp, "Domains:")

    def test_domain_cap_is_actually_enforced_now(self):
        """check_can_add_domain() existed but was never called anywhere —
        the cap had no display AND no enforcement. This closes the second
        half; the display half is covered by test_domains_shows_upgrade_cta_at_cap."""
        plan = self._plan()
        plan.max_custom_domains = 1
        plan.save(update_fields=["max_custom_domains"])
        Domain.objects.create(project=self.project, host="already-have-one.test")

        resp = self.client.post("/admin/domains/add/", {"host": "second.test"}, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(
            Domain.objects.filter(project=self.project, host="second.test").exists()
        )
        self.assertContains(resp, "custom domains")  # the limits._check() message

    def test_team_upgrade_cta_at_cap(self):
        plan = self._plan()
        plan.max_staff = 1  # owner alone already fills it
        plan.save(update_fields=["max_staff"])
        resp = self.client.get("/admin/team/")
        self.assertContains(resp, "Team seats: 1 / 1 used")
        self.assertContains(resp, "Upgrade plan")
