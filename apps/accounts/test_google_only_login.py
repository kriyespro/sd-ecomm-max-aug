"""Mission Control login is Google-only for now: the password form on
/accounts/login/ (templates/accounts/login.jinja) is hidden by default and
a direct password POST is rejected when Google sign-in is configured. A
deliberate escape hatch (?password=1) keeps password auth reachable --
team accounts are provisioned with a one-time password
(apps.accounts.team.provision_member), not a Google-linked identity, so
removing it outright risks locking real accounts out with no way back
short of server/DB access. See apps/accounts/views.py
request_wants_password_form and LoginView.

Also covers the TWO_FACTOR_ENFORCED env-controlled middleware toggle
(config/settings/base.py) -- mandatory 2FA is off by default now,
reversible by flipping the env var back without a code change."""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

User = get_user_model()

GOOGLE_ON = dict(
    GOOGLE_OAUTH_CLIENT_ID="cid.apps.googleusercontent.com",
    GOOGLE_OAUTH_CLIENT_SECRET="secret",
    GOOGLE_OAUTH_REDIRECT_URI="https://shopinaday.com/accounts/google/callback/",
)


@override_settings(ALLOWED_HOSTS=["*"], **GOOGLE_ON)
class GoogleOnlyLoginTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("staffer", "s@t.test", "correct-pw", is_staff=True)

    def test_password_form_hidden_by_default(self):
        body = self.client.get("/accounts/login/").content.decode()
        self.assertIn("Continue with Google", body)
        self.assertNotIn('name="password"', body)

    def test_direct_password_post_is_rejected_without_the_escape_hatch(self):
        resp = self.client.post(
            "/accounts/login/", {"username": "staffer", "password": "correct-pw"},
        )
        self.assertEqual(resp.status_code, 200)  # re-renders the form, no login
        self.assertContains(resp, "Password sign-in is off for now")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_password_form_shown_at_the_escape_hatch(self):
        body = self.client.get("/accounts/login/?password=1").content.decode()
        self.assertIn('name="password"', body)

    def test_escape_hatch_password_login_still_works(self):
        resp = self.client.post(
            "/accounts/login/?password=1", {"username": "staffer", "password": "correct-pw"},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIn("_auth_user_id", self.client.session)

    def test_wrong_password_at_the_escape_hatch_still_fails_normally(self):
        resp = self.client.post(
            "/accounts/login/?password=1", {"username": "staffer", "password": "nope"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)


@override_settings(ALLOWED_HOSTS=["*"])
class PasswordLoginStillWorksWithoutGoogleTests(TestCase):
    """Google not configured at all (e.g. local dev with no OAuth creds) --
    password login must keep working unconditionally, same as before."""

    def setUp(self):
        self.user = User.objects.create_user("nogoogle", "ng@t.test", "pw12345", is_staff=True)

    def test_password_form_always_shown(self):
        body = self.client.get("/accounts/login/").content.decode()
        self.assertIn('name="password"', body)
        self.assertNotIn("Continue with Google", body)

    def test_password_login_works(self):
        resp = self.client.post(
            "/accounts/login/", {"username": "nogoogle", "password": "pw12345"},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIn("_auth_user_id", self.client.session)


class TwoFactorEnforcedSettingTests(TestCase):
    def test_middleware_off_by_default(self):
        self.assertFalse(settings.TWO_FACTOR_ENFORCED)
        self.assertNotIn(
            "apps.accounts.middleware.TwoFactorEnforcementMiddleware", settings.MIDDLEWARE,
        )
