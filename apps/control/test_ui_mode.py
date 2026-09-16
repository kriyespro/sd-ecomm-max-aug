"""Easy/Expert Mission Control mode — a per-user preference (Profile.ui_mode)
that trims the sidebar, never access. Direct URLs to an Expert-only screen
must keep working in Easy mode; only the nav link disappears."""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.accounts.models import Membership, Profile, StoreRole, UiMode
from apps.billing import services as billing_svc
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.control.navigation import build_nav
from apps.projects.models import Project

User = get_user_model()

_EXPERT_ONLY_URLS = [
    "/admin/coupons/",
    "/admin/inventory/",
    "/admin/team/",
    "/admin/domains/",
]


class ProfileDefaultsTests(TestCase):
    def test_new_profile_defaults_expert(self):
        """Expert is the model default for everyone — team members, partner/
        DGC-provisioned stores, test fixtures. Only self_signup() opts a
        brand-new owner into Easy explicitly (see apps.accounts.signup)."""
        u = User.objects.create_user("new", "new@t.test", "pw")
        self.assertEqual(u.profile.ui_mode, UiMode.EXPERT)

    def test_self_signup_opts_into_easy_mode(self):
        from apps.accounts.signup import self_signup

        project, user, created = self_signup(
            name="Jane Owner", email="jane@t.test", store_name="Jane's Shop",
            phone="+919876543210", password="pw12345",
        )
        self.assertTrue(created)
        self.assertEqual(user.profile.ui_mode, UiMode.EASY)


class _FakeProject:
    name = "FakeCo"


class BuildNavEasyModeTests(TestCase):
    def _nav(self, **kw):
        base = dict(
            platform_staff=False, platform_admin=False, active_project=_FakeProject(),
            can_manage=True, can_upload_skin=False, can_manage_owner=True,
        )
        base.update(kw)
        return build_nav(**base)

    def test_easy_mode_trims_store_sections(self):
        nav = self._nav(easy_mode=True)
        item_names = {it["name"] for s in nav for it in s["items"]}
        self.assertIn("product_list", item_names)
        self.assertIn("payment_providers", item_names)
        self.assertNotIn("coupon_list", item_names)
        self.assertNotIn("inventory_list", item_names)
        self.assertNotIn("team", item_names)

    def test_easy_mode_includes_expanded_set(self):
        nav = self._nav(easy_mode=True, store_data_ok=True, can_manage_billing=True)
        item_names = {it["name"] for s in nav for it in s["items"]}
        for name in ("order_list", "cms_banners", "category_list", "cms_store_profile",
                     "analytics", "reports", "domains", "store_plan"):
            self.assertIn(name, item_names, name)

    def test_expert_mode_unchanged(self):
        easy_names = {it["name"] for s in self._nav(easy_mode=True) for it in s["items"]}
        expert_names = {it["name"] for s in self._nav(easy_mode=False) for it in s["items"]}
        self.assertTrue(easy_names < expert_names)

    def test_platform_section_untouched_by_easy_mode(self):
        nav = self._nav(easy_mode=True, platform_staff=True, platform_admin=True)
        platform = next(s for s in nav if s["key"] == "platform")
        self.assertTrue(len(platform["items"]) > 5)


@override_settings(ALLOWED_HOSTS=["*"])
class UiModeViewTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="EasyCo", status="active", feature_flags={"onboarded": True},
        )
        billing_svc.ensure_subscription(self.project)
        self.owner = User.objects.create_user("o", "o@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.project, user=self.owner, role=StoreRole.OWNER)
        # simulates a self-signed-up owner (see self_signup()) — everyone else
        # (team members, test fixtures) stays Expert by model default.
        Profile.objects.filter(user=self.owner).update(ui_mode=UiMode.EASY)
        self.client.force_login(self.owner)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()

    def test_easy_mode_owner_sees_trimmed_nav(self):
        resp = self.client.get("/admin/")
        self.assertContains(resp, "Switch to Expert mode")
        # coupons isn't in the sidebar whitelist — but the checklist card can
        # still link there directly ("Create a launch coupon"), so scope the
        # check to the sidebar <nav> itself, not the whole page.
        html = resp.content.decode()
        nav = html[html.index("<nav x-data"):html.index("</nav>")]
        self.assertNotIn('href="/admin/coupons/"', nav)

    def test_easy_mode_shows_expanded_items(self):
        resp = self.client.get("/admin/")
        for href in ("/admin/orders/", "/admin/cms/banners/", "/admin/categories/",
                    "/admin/cms/store-profile/", "/admin/analytics/", "/admin/reports/",
                    "/admin/domains/", "/admin/plan/"):
            self.assertContains(resp, f'href="{href}"', msg_prefix=href)

    def test_easy_mode_sidebar_is_green_not_role_emerald(self):
        resp = self.client.get("/admin/")
        self.assertContains(resp, "bg-green-600")
        self.assertNotContains(resp, "bg-emerald-950")

    def test_expert_mode_keeps_role_colour(self):
        Profile.objects.filter(user=self.owner).update(ui_mode=UiMode.EXPERT)
        resp = self.client.get("/admin/")
        self.assertContains(resp, "bg-emerald-950")
        self.assertNotContains(resp, "bg-green-600")

    def test_expert_only_url_still_reachable_in_easy_mode(self):
        for url in _EXPERT_ONLY_URLS:
            resp = self.client.get(url)
            self.assertNotIn(resp.status_code, (403, 404), f"{url} -> {resp.status_code}")

    def test_toggle_flips_mode_and_persists(self):
        resp = self.client.post("/admin/ui-mode/toggle/", {"next": "/admin/"}, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.owner.profile.refresh_from_db()
        self.assertEqual(self.owner.profile.ui_mode, UiMode.EXPERT)
        self.assertContains(resp, 'href="/admin/coupons/"')
        self.assertContains(resp, "Switch to Easy mode")

        # toggling again flips back
        self.client.post("/admin/ui-mode/toggle/", {"next": "/admin/"})
        self.owner.profile.refresh_from_db()
        self.assertEqual(self.owner.profile.ui_mode, UiMode.EASY)

    def test_quick_launch_checklist_on_dashboard(self):
        resp = self.client.get("/admin/")
        self.assertContains(resp, "Quick launch checklist")
        self.assertContains(resp, "Add your first product")

    def test_checklist_absent_in_expert_mode(self):
        Profile.objects.filter(user=self.owner).update(ui_mode=UiMode.EXPERT)
        resp = self.client.get("/admin/")
        self.assertNotContains(resp, "Quick launch checklist")

    def test_checklist_step_marks_done_once_product_added(self):
        from decimal import Decimal

        from apps.catalog.models import Product

        resp = self.client.get("/admin/")
        # not yet added
        self.assertContains(resp, '<span>○</span>')

        Product.objects.create(
            project=self.project, title="Mug", slug="mug", price=Decimal("100"),
            status="active",
        )
        resp = self.client.get("/admin/")
        html = resp.content.decode()
        # the "Add your first product" step now shows a check, not a circle
        idx = html.index("Add your first product")
        self.assertIn("✔", html[idx - 80:idx])
