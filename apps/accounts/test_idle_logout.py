"""Idle timeout for Mission Control staff sessions — 35 minutes with no
/admin/ request logs the account out on its next request, not on a fixed
clock. Storefront shoppers (never is_staff) are never touched."""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.middleware import IDLE_TIMEOUT_SECONDS, _LAST_SEEN_KEY

User = get_user_model()


@override_settings(ALLOWED_HOSTS=["*"])
class IdleLogoutTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_superuser("idle1", "idle1@t.test", "pw")
        self.client.force_login(self.staff)

    def test_first_admin_request_just_stamps_last_seen_no_logout(self):
        resp = self.client.get("/admin/users/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(_LAST_SEEN_KEY, self.client.session)

    def test_quick_second_request_stays_logged_in(self):
        self.client.get("/admin/users/")
        resp = self.client.get("/admin/users/")
        self.assertEqual(resp.status_code, 200)

    def test_stale_session_is_logged_out_and_redirected_to_login(self):
        self.client.get("/admin/users/")  # stamps last_seen
        s = self.client.session
        s[_LAST_SEEN_KEY] = timezone.now().timestamp() - IDLE_TIMEOUT_SECONDS - 60
        s.save()
        resp = self.client.get("/admin/users/")
        self.assertRedirects(
            resp, "/accounts/login/?next=/admin/users/", fetch_redirect_response=False,
        )
        self.assertFalse(resp.wsgi_request.user.is_authenticated)

    def test_login_page_explains_the_idle_logout(self):
        self.client.get("/admin/users/")
        s = self.client.session
        s[_LAST_SEEN_KEY] = timezone.now().timestamp() - IDLE_TIMEOUT_SECONDS - 60
        s.save()
        resp = self.client.get("/admin/users/", follow=True)
        self.assertContains(resp, "signed out after 35 minutes")

    def test_storefront_request_never_sets_last_seen(self):
        from apps.projects.models import Project

        Project.objects.create(name="IdleCo", status="active")
        self.client.get("/")
        self.assertNotIn(_LAST_SEEN_KEY, self.client.session)

    def test_non_staff_account_untouched(self):
        plain = User.objects.create_user("idle2", "idle2@t.test", "pw")
        self.client.force_login(plain)
        resp = self.client.get("/admin/")
        self.assertNotIn(_LAST_SEEN_KEY, self.client.session)
