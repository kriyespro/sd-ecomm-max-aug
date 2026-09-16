"""Mandatory TOTP 2FA for platform admins.

The enforcement middleware is stripped out of MIDDLEWARE for the test
settings module (see config/settings/development.py — it's exercised
directly here via override_settings instead) so the other ~1000 tests in
the suite don't all need a confirmed TOTP secret on every superuser/
platform-admin fixture just to reach an /admin/ page."""

import pyotp
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.accounts.models import Membership, PlatformRole, StoreRole
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.projects.models import Project

from . import twofactor

User = get_user_model()

_GATE = "apps.accounts.middleware.TwoFactorEnforcementMiddleware"


def _with_gate():
    return override_settings(MIDDLEWARE=[*settings.MIDDLEWARE, _GATE])


class TwoFactorServiceTests(TestCase):
    def test_generated_secret_verifies_its_own_code(self):
        secret = twofactor.generate_secret()
        code = pyotp.TOTP(secret).now()
        self.assertTrue(twofactor.verify_totp(secret, code))

    def test_wrong_code_rejected(self):
        secret = twofactor.generate_secret()
        self.assertFalse(twofactor.verify_totp(secret, "000000"))

    def test_backup_codes_hashed_not_plaintext(self):
        codes = twofactor.generate_backup_codes()
        hashed = twofactor.hash_backup_codes(codes)
        self.assertEqual(len(hashed), 10)
        for plain, h in zip(codes, hashed):
            self.assertNotEqual(plain, h)

    def test_backup_code_single_use(self):
        user = User.objects.create_user("u1", "u1@t.test", "pw")
        profile = user.profile
        codes = twofactor.generate_backup_codes(n=2)
        profile.backup_codes = twofactor.hash_backup_codes(codes)
        profile.save(update_fields=["backup_codes"])

        self.assertTrue(twofactor.consume_backup_code(profile, codes[0]))
        profile.refresh_from_db()
        self.assertEqual(len(profile.backup_codes), 1)
        # same code again -> no longer valid
        self.assertFalse(twofactor.consume_backup_code(profile, codes[0]))

    def test_enable_requires_a_real_code(self):
        user = User.objects.create_user("u2", "u2@t.test", "pw")
        profile = user.profile
        secret = twofactor.generate_secret()
        self.assertIsNone(twofactor.enable(profile, secret=secret, code="000000"))
        profile.refresh_from_db()
        self.assertFalse(profile.totp_enabled)

        codes = twofactor.enable(profile, secret=secret, code=pyotp.TOTP(secret).now())
        self.assertEqual(len(codes), 10)
        profile.refresh_from_db()
        self.assertTrue(profile.totp_enabled)
        self.assertTrue(profile.totp_secret)

    def test_reset_clears_everything(self):
        user = User.objects.create_user("u3", "u3@t.test", "pw")
        profile = user.profile
        secret = twofactor.generate_secret()
        twofactor.enable(profile, secret=secret, code=pyotp.TOTP(secret).now())
        profile.refresh_from_db()

        twofactor.reset(profile)
        profile.refresh_from_db()
        self.assertFalse(profile.totp_enabled)
        self.assertEqual(profile.totp_secret, "")
        self.assertEqual(profile.backup_codes, [])


@override_settings(ALLOWED_HOSTS=["*"])
class TwoFactorSetupViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser("admin1", "admin1@t.test", "pw")
        self.client.force_login(self.user)

    def test_get_shows_a_secret_and_uri(self):
        resp = self.client.get("/accounts/2fa/setup/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Manual entry key")
        self.assertTrue(self.client.session.get("2fa_setup_secret"))

    def test_wrong_code_does_not_enable(self):
        self.client.get("/accounts/2fa/setup/")
        resp = self.client.post("/accounts/2fa/setup/", {"code": "000000"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Incorrect code")
        self.user.profile.refresh_from_db()
        self.assertFalse(self.user.profile.totp_enabled)

    def test_correct_code_enables_and_shows_backup_codes_once(self):
        self.client.get("/accounts/2fa/setup/")
        secret = self.client.session["2fa_setup_secret"]
        resp = self.client.post("/accounts/2fa/setup/", {"code": pyotp.TOTP(secret).now()})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "backup codes")
        self.user.profile.refresh_from_db()
        self.assertTrue(self.user.profile.totp_enabled)
        self.assertEqual(len(self.user.profile.backup_codes), 10)

    def test_already_enabled_short_circuits(self):
        profile = self.user.profile
        secret = twofactor.generate_secret()
        twofactor.enable(profile, secret=secret, code=pyotp.TOTP(secret).now())
        resp = self.client.get("/accounts/2fa/setup/")
        self.assertContains(resp, "already")


@override_settings(ALLOWED_HOSTS=["*"])
class LoginFlowTwoFactorTests(TestCase):
    """The password-then-code login handshake for a platform admin with
    2FA enabled. Doesn't need the /admin/ gate middleware — this is the
    login view's own branch."""

    def setUp(self):
        self.user = User.objects.create_superuser("admin2", "admin2@t.test", "pw")
        self.secret = twofactor.generate_secret()
        twofactor.enable(self.user.profile, secret=self.secret, code=pyotp.TOTP(self.secret).now())

    def test_password_alone_does_not_log_in(self):
        resp = self.client.post("/accounts/login/", {"username": "admin2", "password": "pw"})
        self.assertRedirects(resp, "/accounts/2fa/verify/", fetch_redirect_response=False)
        self.assertFalse(resp.wsgi_request.user.is_authenticated)

    def test_wrong_code_rejected_then_correct_code_logs_in(self):
        self.client.post("/accounts/login/", {"username": "admin2", "password": "pw"})
        bad = self.client.post("/accounts/2fa/verify/", {"code": "000000"})
        self.assertEqual(bad.status_code, 200)
        self.assertContains(bad, "Incorrect code")

        good = self.client.post("/accounts/2fa/verify/", {"code": pyotp.TOTP(self.secret).now()})
        self.assertEqual(good.status_code, 302)
        resp = self.client.get("/accounts/2fa/setup/")
        self.assertTrue(resp.wsgi_request.user.is_authenticated)

    def test_backup_code_also_logs_in(self):
        codes = list(self.user.profile.backup_codes)
        self.assertTrue(codes)  # sanity: hashed codes exist from enable()
        # Re-derive a fresh plaintext set since enable() only returns them
        # once — regenerate for this test's own use.
        plain = twofactor.generate_backup_codes(n=1)
        self.user.profile.backup_codes = twofactor.hash_backup_codes(plain)
        self.user.profile.save(update_fields=["backup_codes"])

        self.client.post("/accounts/login/", {"username": "admin2", "password": "pw"})
        resp = self.client.post("/accounts/2fa/verify/", {"code": plain[0]})
        self.assertEqual(resp.status_code, 302)

    def test_non_platform_admin_skips_2fa_even_if_enabled(self):
        # A store owner is never gated by this — only is_platform_admin.
        plain_user = User.objects.create_user("owner1", "owner1@t.test", "pw")
        secret = twofactor.generate_secret()
        twofactor.enable(plain_user.profile, secret=secret, code=pyotp.TOTP(secret).now())
        resp = self.client.post("/accounts/login/", {"username": "owner1", "password": "pw"})
        self.assertNotEqual(resp.get("Location", ""), "/accounts/2fa/verify/")


@override_settings(ALLOWED_HOSTS=["*"])
class MiddlewareGateTests(TestCase):
    """Direct proof the /admin/ gate itself works — re-added via
    override_settings since it's stripped from MIDDLEWARE under `test`."""

    def setUp(self):
        self.admin = User.objects.create_superuser("admin3", "admin3@t.test", "pw")

    def test_platform_admin_without_2fa_is_redirected_to_setup(self):
        self.client.force_login(self.admin)
        with _with_gate():
            resp = self.client.get("/admin/users/")
        self.assertRedirects(resp, "/accounts/2fa/setup/", fetch_redirect_response=False)

    def test_platform_admin_with_2fa_passes_through(self):
        secret = twofactor.generate_secret()
        twofactor.enable(self.admin.profile, secret=secret, code=pyotp.TOTP(secret).now())
        self.client.force_login(self.admin)
        with _with_gate():
            resp = self.client.get("/admin/users/")
        self.assertEqual(resp.status_code, 200)

    def test_store_owner_never_gated(self):
        project = Project.objects.create(name="GateCo", status="active", feature_flags={"onboarded": True})
        owner = User.objects.create_user("owner2", "owner2@t.test", "pw", is_staff=True)
        Membership.objects.create(project=project, user=owner, role=StoreRole.OWNER)
        self.client.force_login(owner)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = project.pk
        s.save()
        with _with_gate():
            resp = self.client.get("/admin/")
        self.assertNotEqual(resp.get("Location", ""), "/accounts/2fa/setup/")

    def test_setup_page_itself_never_redirect_loops(self):
        self.client.force_login(self.admin)
        with _with_gate():
            resp = self.client.get("/accounts/2fa/setup/")
        self.assertEqual(resp.status_code, 200)


@override_settings(ALLOWED_HOSTS=["*"])
class AdminResetTwoFactorTests(TestCase):
    """Recovery path: another platform admin clears a locked-out admin's
    2FA from Mission Control's Users screen."""

    def setUp(self):
        self.rescuer = User.objects.create_superuser("rescuer", "rescuer@t.test", "pw")
        self.locked = User.objects.create_user(
            "locked", "locked@t.test", "pw", is_staff=True,
        )
        self.locked.profile.platform_role = PlatformRole.OWNER
        self.locked.profile.save(update_fields=["platform_role"])
        secret = twofactor.generate_secret()
        twofactor.enable(self.locked.profile, secret=secret, code=pyotp.TOTP(secret).now())
        self.client.force_login(self.rescuer)

    def test_reset_clears_target_totp(self):
        resp = self.client.post(f"/admin/users/{self.locked.pk}/2fa-reset/", follow=True)
        self.assertEqual(resp.status_code, 200)
        self.locked.profile.refresh_from_db()
        self.assertFalse(self.locked.profile.totp_enabled)
        self.assertEqual(self.locked.profile.totp_secret, "")

    def test_non_admin_cannot_reset(self):
        from apps.accounts.permissions import is_platform_admin
        from apps.control import services

        regular = User.objects.create_user("reg", "reg@t.test", "pw")
        with self.assertRaises(Exception):
            services.reset_two_factor(actor=regular, target=self.locked)
        self.assertFalse(is_platform_admin(regular))
