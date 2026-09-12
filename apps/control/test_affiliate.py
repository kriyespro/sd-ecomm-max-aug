from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.accounts.models import Membership, PlatformRole, Profile
from apps.accounts.signup import self_signup
from apps.billing import services as billing_svc
from apps.projects.models import Project
from apps.projects.services import projects_for_user

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
class AffiliateReferralGrantsNoAccessTests(TestCase):
    """The whole point of splitting referred_by from manager: an affiliate
    link earns a DGC commission, never store access. manager stays the only
    field that grants Mission Control access — set only by a DGC creating
    the store themselves, or a platform admin hand-assigning one."""

    def setUp(self):
        self.dgc = User.objects.create_user("dgc", "dgc@t.test", "pw", is_staff=True)
        profile = Profile.objects.get(user=self.dgc)
        profile.platform_role = PlatformRole.MANAGER
        profile.save(update_fields=["platform_role"])
        self.code = profile.ensure_affiliate_code()
        self.project, _, _ = self_signup(
            name="", email="noaccess@gmail.com", store_name="No Access Shop",
            phone="9", oauth=True, ref_code=self.code,
        )

    def test_referred_project_absent_from_projects_for_user(self):
        self.assertNotIn(self.project, projects_for_user(self.dgc))

    def test_referred_store_not_in_dgc_store_list(self):
        self.client.force_login(self.dgc)
        resp = self.client.get("/admin/stores/")
        self.assertNotContains(resp, "No Access Shop")

    def test_dgc_lands_on_platform_overview_not_the_referred_stores_dashboard(self):
        """get_active_project() must never resolve to a store the DGC only
        earns a referral commission on — otherwise /admin/ would silently
        drop them onto that store's own Today dashboard."""
        self.client.force_login(self.dgc)
        resp = self.client.get("/admin/")
        self.assertContains(resp, "Choose a store")
        self.assertNotContains(resp, "No Access Shop")

    def test_dgc_cannot_administer_the_referred_store(self):
        from django.core.exceptions import PermissionDenied

        from apps.accounts.permissions import OWNER_MANAGER, assert_store_role

        with self.assertRaises(PermissionDenied):
            assert_store_role(self.dgc, self.project, OWNER_MANAGER)

    def test_owner_of_referred_store_still_has_full_normal_access(self):
        owner = User.objects.get(email="noaccess@gmail.com")
        self.assertIn(self.project, projects_for_user(owner))
        self.assertTrue(
            Membership.objects.filter(
                user=owner, project=self.project, role="owner", is_active=True
            ).exists()
        )


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

    def test_manager_commission_falls_back_to_referrer_when_no_manager(self):
        """The money side of the split: an affiliate-referred store's paid
        invoices still accrue commission to the referring DGC even though
        they're never set as `manager`."""
        from apps.billing.models import ManagerCommission

        project = Project.objects.get(name="Dash Ref Shop")
        sub = project.subscription
        self.assertIsNone(sub.manager_id)
        self.assertEqual(sub.referred_by_id, self.dgc.pk)

        invoice = billing_svc.issue_invoice(sub)
        billing_svc.mark_invoice_paid(invoice)
        commission = ManagerCommission.objects.get(subscription=sub)
        self.assertEqual(commission.manager_id, self.dgc.pk)
