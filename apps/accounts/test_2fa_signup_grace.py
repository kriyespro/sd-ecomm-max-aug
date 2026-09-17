"""A brand-new signup (Google-only) isn't force-walled into 2FA setup mid-
signup — the very first thing they'd see would be a security screen before
they've looked at the product. Grace applies to that one session only; the
next real login (a fresh session) is gated normally, same as any other
Mission Control account.

Mirrors apps.accounts.test_two_factor's pattern: the enforcement middleware
is stripped for `manage.py test` (see config/settings/development.py), so
it's re-added here via override_settings to prove the gate itself."""

from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings

from apps.accounts import twofactor

User = get_user_model()

GOOGLE_ON = dict(
    GOOGLE_OAUTH_CLIENT_ID="cid.apps.googleusercontent.com",
    GOOGLE_OAUTH_CLIENT_SECRET="secret",
    GOOGLE_OAUTH_REDIRECT_URI="https://shopinaday.com/accounts/google/callback/",
)
_GATE = "apps.accounts.middleware.TwoFactorEnforcementMiddleware"


def _with_gate():
    return override_settings(MIDDLEWARE=[*settings.MIDDLEWARE, _GATE])


def _gated_client_for(user):
    """Django's test Client caches its middleware chain on first dispatch —
    reusing a client that already made requests before entering
    _with_gate() silently keeps the old (gate-less) chain. A fresh Client
    picks up the override on its first request."""
    c = Client()
    c.force_login(user)
    return c


@override_settings(ALLOWED_HOSTS=["*"], **GOOGLE_ON)
class SignupGraceTests(TestCase):
    def _signup(self, email="new@gmail.test"):
        self.client.get("/accounts/google/start/?plan=")
        state = self.client.session["google_oauth_flow"]["state"]
        with patch("apps.accounts.views.google_oauth.exchange_code") as ex:
            ex.return_value = {"email": email, "email_verified": True, "name": "New User", "sub": "123"}
            self.client.get(f"/accounts/google/callback/?code=abc&state={state}")
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(
                "/accounts/signup/complete/",
                {"store_name": "Newco", "phone": "+91 90000 11111",
                 "city": "Mumbai", "state": "Maharashtra", "postal_code": "400001"},
            )

    def test_no_2fa_wall_right_after_signup(self):
        self._signup()
        user = User.objects.get(email="new@gmail.test")
        gated = Client()
        gated.cookies = self.client.cookies  # same session -> carries the grace
        with _with_gate():
            resp = gated.get("/admin/products/")
        self.assertNotEqual(resp.get("Location", ""), "/accounts/2fa/setup/")

    def test_next_login_is_gated_normally(self):
        self._signup()
        user = User.objects.get(email="new@gmail.test")
        self.client.logout()
        with _with_gate():
            resp = _gated_client_for(user).get("/admin/products/")
        self.assertRedirects(resp, "/accounts/2fa/setup/", fetch_redirect_response=False)

    def test_dashboard_shows_a_soft_nudge_during_grace(self):
        self._signup()
        resp = self.client.get("/admin/start/")
        self.assertContains(resp, "two-factor authentication")

    def test_nudge_disappears_once_2fa_is_enabled(self):
        import pyotp

        self._signup()
        user = User.objects.get(email="new@gmail.test")
        secret = twofactor.generate_secret()
        twofactor.enable(user.profile, secret=secret, code=pyotp.TOTP(secret).now())
        resp = self.client.get("/admin/start/")
        self.assertNotContains(resp, "two-factor authentication")

    def test_existing_account_google_signin_gets_no_grace(self):
        # First signup — consumes the grace on its own session.
        self._signup(email="existing@gmail.test")
        self.client.logout()

        # A later Google sign-IN to the same already-existing account (not a
        # fresh signup) must not grant a new grace session.
        self.client.get("/accounts/google/start/?plan=")
        state = self.client.session["google_oauth_flow"]["state"]
        with patch("apps.accounts.views.google_oauth.exchange_code") as ex:
            ex.return_value = {"email": "existing@gmail.test", "email_verified": True,
                               "name": "New User", "sub": "123"}
            self.client.get(f"/accounts/google/callback/?code=abc&state={state}")
        gated = Client()
        gated.cookies = self.client.cookies  # same (just-signed-in) session
        with _with_gate():
            resp = gated.get("/admin/products/")
        self.assertRedirects(resp, "/accounts/2fa/setup/", fetch_redirect_response=False)
