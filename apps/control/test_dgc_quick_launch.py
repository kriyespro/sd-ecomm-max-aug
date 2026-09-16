"""DGC dashboard checklist — extends the Easy-mode onboarding checklist
(apps.control.quick_launch) to actually track a DGC's real one-time setup
(managing a store, payout UPI, affiliate link) instead of always showing
three permanently-incomplete items.

Regression: the old third step linked to control:affiliate_overview,
which is PlatformAdminRequiredMixin (superuser / Platform Owner only) — a
plain DGC (Platform Manager) following their own checklist got a 403.
That page is the superadmin's cross-DGC view; a DGC's own link lives on
their own earnings page, control:my_commissions."""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.accounts.models import PlatformRole, Profile, UiMode
from apps.billing import services as billing_svc
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.projects.models import Project

User = get_user_model()


@override_settings(ALLOWED_HOSTS=["*"])
class DgcQuickLaunchTests(TestCase):
    def setUp(self):
        self.dgc = User.objects.create_user("dgc9", "dgc9@t.test", "pw", is_staff=True)
        Profile.objects.filter(user=self.dgc).update(
            platform_role=PlatformRole.MANAGER, ui_mode=UiMode.EASY,
        )
        self.dgc = User.objects.get(pk=self.dgc.pk)
        self.client.force_login(self.dgc)
        # No active-project session -> platform-level dashboard, where the
        # DGC checklist actually renders.
        s = self.client.session
        s.pop(ACTIVE_PROJECT_SESSION_KEY, None)
        s.save()

    def test_old_affiliate_overview_link_would_403_a_plain_dgc(self):
        """Proves the bug the fix removes — a plain DGC hitting the old
        checklist's third-step URL directly gets forbidden."""
        resp = self.client.get("/admin/affiliates/")
        self.assertEqual(resp.status_code, 403)

    def test_checklist_is_tracked_and_does_not_link_the_403_page(self):
        resp = self.client.get("/admin/")
        self.assertContains(resp, "Quick launch checklist")
        self.assertContains(resp, "0 / 3")
        self.assertNotContains(resp, 'href="/admin/affiliates/"')
        self.assertContains(resp, 'href="/admin/earnings/"')

    def test_managing_a_store_checks_off_first_step(self):
        project = Project.objects.create(name="DgcCo", status="active")
        sub = billing_svc.ensure_subscription(project)
        sub.manager = self.dgc
        sub.save(update_fields=["manager"])
        resp = self.client.get("/admin/")
        self.assertContains(resp, "1 / 3")

    def test_payout_upi_checks_off_second_step(self):
        self.dgc.profile.payout_upi = "dgc9@okbank"
        self.dgc.profile.save(update_fields=["payout_upi"])
        resp = self.client.get("/admin/")
        self.assertContains(resp, "1 / 3")

    def test_visiting_earnings_generates_the_link_and_completes_the_list(self):
        project = Project.objects.create(name="DgcCo2", status="active")
        sub = billing_svc.ensure_subscription(project)
        sub.manager = self.dgc
        sub.save(update_fields=["manager"])
        self.dgc.profile.payout_upi = "dgc9@okbank"
        self.dgc.profile.save(update_fields=["payout_upi"])

        # earnings page lazily generates the affiliate code on first visit
        self.client.get("/admin/earnings/")
        self.dgc.profile.refresh_from_db()
        self.assertTrue(self.dgc.profile.affiliate_code)

        resp = self.client.get("/admin/")
        self.assertContains(resp, "3 / 3")
