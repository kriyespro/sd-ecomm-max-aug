"""Trial-ending-soon banner in Mission Control — only for the owner/manager
who'd actually act on it, only in the last 3 days of a real trial."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import Membership, PlatformRole, Profile, StoreRole
from apps.billing import services as billing_svc
from apps.billing.models import SubscriptionStatus
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.projects.models import Project

User = get_user_model()


@override_settings(ALLOWED_HOSTS=["*"])
class TrialBannerTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="TrialCo", status="active", feature_flags={"onboarded": True},
        )
        self.sub = billing_svc.ensure_subscription(self.project)
        self.owner = User.objects.create_user("o", "o@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.project, user=self.owner, role=StoreRole.OWNER)

    def _login(self, user):
        self.client.force_login(user)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()

    def test_no_banner_when_trial_far_out(self):
        self.sub.trial_end = timezone.now() + timedelta(days=10)
        self.sub.save(update_fields=["trial_end"])
        self._login(self.owner)
        resp = self.client.get("/admin/")
        self.assertNotContains(resp, "trial ends")

    def test_banner_shows_within_three_days(self):
        self.sub.trial_end = timezone.now() + timedelta(days=2, hours=1)
        self.sub.save(update_fields=["trial_end"])
        self._login(self.owner)
        resp = self.client.get("/admin/")
        self.assertContains(resp, "trial ends in 2 days")
        self.assertContains(resp, "Upgrade now")

    def test_banner_says_today_on_last_day(self):
        self.sub.trial_end = timezone.now() + timedelta(hours=1)
        self.sub.save(update_fields=["trial_end"])
        self._login(self.owner)
        resp = self.client.get("/admin/")
        self.assertContains(resp, "trial ends today")

    def test_no_banner_once_trial_expired(self):
        self.sub.trial_end = timezone.now() - timedelta(hours=1)
        self.sub.save(update_fields=["trial_end"])
        self._login(self.owner)
        resp = self.client.get("/admin/")
        self.assertNotContains(resp, "trial ends")

    def test_no_banner_once_active_not_trialing(self):
        self.sub.trial_end = timezone.now() + timedelta(days=1)
        self.sub.status = SubscriptionStatus.ACTIVE
        self.sub.save(update_fields=["trial_end", "status"])
        self._login(self.owner)
        resp = self.client.get("/admin/")
        self.assertNotContains(resp, "trial ends")

    def test_staff_never_sees_it(self):
        staff = User.objects.create_user("s", "s@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.project, user=staff, role=StoreRole.STAFF)
        self.sub.trial_end = timezone.now() + timedelta(hours=1)
        self.sub.save(update_fields=["trial_end"])
        self._login(staff)
        resp = self.client.get("/admin/")
        self.assertNotContains(resp, "trial ends")

    def test_dgc_managed_store_team_never_sees_it(self):
        """The DGC owns the billing relationship — the store's own owner
        shouldn't get a billing nudge that isn't theirs to act on."""
        dgc = User.objects.create_user("d", "d@t.test", "pw", is_staff=True)
        Profile.objects.filter(user=dgc).update(platform_role=PlatformRole.MANAGER)
        self.sub.manager_id = dgc.pk
        self.sub.trial_end = timezone.now() + timedelta(hours=1)
        self.sub.save(update_fields=["manager", "trial_end"])
        self._login(self.owner)
        resp = self.client.get("/admin/")
        self.assertNotContains(resp, "trial ends")

    def test_platform_admin_browsing_does_not_see_it(self):
        admin = User.objects.create_superuser("root", "root@t.test", "pw")
        self.sub.trial_end = timezone.now() + timedelta(hours=1)
        self.sub.save(update_fields=["trial_end"])
        self._login(admin)
        resp = self.client.get("/admin/")
        self.assertNotContains(resp, "trial ends")
