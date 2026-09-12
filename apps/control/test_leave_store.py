"""LeaveStoreView — without it, platform staff who pick a store in the
switcher have no way back to the platform-wide dashboard (get_active_project
keeps re-resolving the same session pick), which made the "Top affiliates" /
"Affiliate signups" panel unreachable once any store had ever been selected."""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.accounts.models import PlatformRole, Profile
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.projects.models import Project

User = get_user_model()


@override_settings(ALLOWED_HOSTS=["*"])
class LeaveStoreViewTests(TestCase):
    def setUp(self):
        self.store_a = Project.objects.create(
            name="Store A", status="active", feature_flags={"onboarded": True}
        )
        self.store_b = Project.objects.create(
            name="Store B", status="active", feature_flags={"onboarded": True}
        )
        self.su = User.objects.create_superuser("root", "root@t.test", "pw")

    def _login_with_active_store(self, user, project):
        self.client.force_login(user)
        session = self.client.session
        session[ACTIVE_PROJECT_SESSION_KEY] = project.pk
        session.save()

    def test_leaving_a_store_returns_to_the_platform_dashboard(self):
        self._login_with_active_store(self.su, self.store_a)
        resp = self.client.get("/admin/")
        self.assertContains(resp, "Store A")  # confirms we start store-scoped

        resp = self.client.post("/admin/leave-store/", follow=True)
        self.assertRedirects(resp, "/admin/")
        self.assertNotIn(ACTIVE_PROJECT_SESSION_KEY, self.client.session)

    def test_platform_dashboard_shows_affiliate_panel_after_leaving(self):
        dgc = User.objects.create_user("dgc", "dgc@t.test", "pw", is_staff=True)
        profile = Profile.objects.get(user=dgc)
        profile.platform_role = PlatformRole.MANAGER
        profile.save(update_fields=["platform_role"])
        code = profile.ensure_affiliate_code()
        from apps.accounts.signup import self_signup

        self_signup(
            name="", email="leaveref@gmail.com", store_name="Leave Ref Shop",
            phone="9", oauth=True, ref_code=code,
        )

        self._login_with_active_store(self.su, self.store_a)
        # Before leaving: store-scoped Today dashboard, no affiliate panel.
        resp = self.client.get("/admin/")
        self.assertNotContains(resp, "Top affiliates")

        self.client.post("/admin/leave-store/")
        resp = self.client.get("/admin/")
        self.assertContains(resp, "Top affiliates")
        self.assertContains(resp, "Affiliate signups")

    def test_leave_store_button_shown_to_platform_staff_with_active_store(self):
        self._login_with_active_store(self.su, self.store_a)
        resp = self.client.get("/admin/")
        self.assertContains(resp, "Platform overview")

    def test_leave_store_button_hidden_for_plain_store_owner(self):
        from apps.accounts.models import Membership

        owner = User.objects.create_user("o", "o@a.test", "pw", is_staff=True)
        Membership.objects.create(user=owner, project=self.store_a, role="owner")
        self._login_with_active_store(owner, self.store_a)
        resp = self.client.get("/admin/")
        self.assertNotContains(resp, "Platform overview")
