from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.accounts.models import Membership, PlatformRole, Profile
from apps.accounts.signup import self_signup
from apps.billing import services as billing_svc
from apps.projects.models import Project

User = get_user_model()


@override_settings(ALLOWED_HOSTS=["*"])
class AffiliateEarningsScreenTests(TestCase):
    def setUp(self):
        self.dgc = User.objects.create_user("dgc", "dgc@t.test", "pw", is_staff=True)
        profile = Profile.objects.get(user=self.dgc)
        profile.platform_role = PlatformRole.MANAGER
        profile.save(update_fields=["platform_role"])
        self.code = profile.ensure_affiliate_code()

    def test_earnings_screen_shows_own_link_and_code(self):
        self.client.force_login(self.dgc)
        resp = self.client.get("/admin/earnings/")
        self.assertContains(resp, f"?ref={self.code}")
        self.assertContains(resp, self.code)

    def test_referred_signup_appears_with_masked_email(self):
        self_signup(
            name="", email="referredowner@gmail.com", store_name="Referred Shop",
            phone="9", oauth=True, ref_code=self.code,
        )
        self.client.force_login(self.dgc)
        resp = self.client.get("/admin/earnings/")
        self.assertContains(resp, "Referred Shop")
        self.assertContains(resp, "r***********r@gmail.com")
        self.assertNotContains(resp, "referredowner@gmail.com")

    def test_manually_assigned_manager_without_ref_is_not_listed_as_referral(self):
        """Being hand-set as a store's manager on the Mission Control store
        screen is a different relationship from an affiliate-link signup —
        the affiliate table must only show genuine link conversions."""
        project = Project.objects.create(name="Manual Co", status="active")
        sub = billing_svc.ensure_subscription(project)
        sub.manager = self.dgc
        sub.save(update_fields=["manager"])

        self.client.force_login(self.dgc)
        resp = self.client.get("/admin/earnings/")
        # "Manual Co" legitimately shows up in the store-switcher (the DGC
        # does manage it) — what must NOT happen is it landing in the
        # affiliate-signups table, which stays empty.
        self.assertContains(resp, "No signups through your link yet")

    def test_referral_from_another_dgc_is_not_shown(self):
        other = User.objects.create_user("dgc2", "dgc2@t.test", "pw", is_staff=True)
        other_profile = Profile.objects.get(user=other)
        other_profile.platform_role = PlatformRole.MANAGER
        other_profile.save(update_fields=["platform_role"])
        other_code = other_profile.ensure_affiliate_code()

        self_signup(
            name="", email="other-owner@gmail.com", store_name="Other Shop",
            phone="9", oauth=True, ref_code=other_code,
        )
        self.client.force_login(self.dgc)
        resp = self.client.get("/admin/earnings/")
        self.assertNotContains(resp, "Other Shop")


@override_settings(ALLOWED_HOSTS=["*"])
class SuperadminAffiliateDashboardTests(TestCase):
    def setUp(self):
        self.dgc = User.objects.create_user("dgc", "dgc@t.test", "pw", is_staff=True)
        profile = Profile.objects.get(user=self.dgc)
        profile.platform_role = PlatformRole.MANAGER
        profile.save(update_fields=["platform_role"])
        self.code = profile.ensure_affiliate_code()
        self_signup(
            name="", email="dashref@gmail.com", store_name="Dash Ref Shop",
            phone="9", oauth=True, ref_code=self.code,
        )

    def test_admin_dashboard_shows_affiliate_count_and_top_affiliate(self):
        # A second, unrelated store — otherwise a platform admin with only
        # one project in the whole DB gets auto-dropped onto that store's own
        # "Today" dashboard (get_active_project's "sole accessible project"
        # rule) instead of the platform-wide overview this test checks.
        Project.objects.create(name="Other Co", status="active")
        su = User.objects.create_superuser("root", "root@t.test", "pw")
        self.client.force_login(su)
        resp = self.client.get("/admin/")
        self.assertContains(resp, "Affiliate signups")
        self.assertContains(resp, "Top affiliates")
        self.assertContains(resp, "dgc")

    def test_plain_dgc_does_not_see_platform_wide_affiliate_panel(self):
        self.client.force_login(self.dgc)
        resp = self.client.get("/admin/")
        self.assertNotContains(resp, "Top affiliates")
