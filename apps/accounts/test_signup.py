from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.accounts.models import Membership
from apps.accounts.signup import self_signup
from apps.billing.models import BillingSettings, Plan, Subscription
from apps.projects.models import Project

User = get_user_model()

GOOGLE_ON = dict(
    GOOGLE_OAUTH_CLIENT_ID="cid.apps.googleusercontent.com",
    GOOGLE_OAUTH_CLIENT_SECRET="secret",
    GOOGLE_OAUTH_REDIRECT_URI="https://shopinaday.com/accounts/google/callback/",
)


@override_settings(ALLOWED_HOSTS=["*"])
class SelfSignupServiceTests(TestCase):
    def test_creates_owner_store_short_trial_and_phone(self):
        with self.captureOnCommitCallbacks(execute=True):
            project, user, created = self_signup(
                name="Ada Owner", email="Ada@Shop.test", store_name="Ada's Shop",
                phone="+91 98765 43210", oauth=True,
            )
        self.assertTrue(created)
        self.assertTrue(user.is_staff)
        self.assertEqual(user.email, "ada@shop.test")
        self.assertFalse(user.has_usable_password())  # Google account
        self.assertEqual(user.profile.phone, "+91 98765 43210")
        self.assertEqual(
            Membership.objects.get(user=user, project=project).role, "owner"
        )

        sub = project.subscription
        self.assertEqual(sub.status, "trialing")
        self.assertIsNone(sub.manager_id)
        days = (sub.trial_end - sub.current_period_start).days
        self.assertEqual(days, BillingSettings.load().self_signup_trial_days)
        self.assertEqual(days, 7)

        project.refresh_from_db()
        self.assertTrue(project.feature_flags.get("demo_seeded"))
        self.assertFalse(project.feature_flags.get("onboarded"))

        # the real phone wins over the demo placeholder
        from apps.cms.models import StoreProfile

        self.assertEqual(
            StoreProfile.objects.get(project=project).support_phone, "+91 98765 43210"
        )

    @override_settings(PLATFORM_BASE_DOMAIN="shopinaday.test")
    def test_assigns_verified_primary_subdomain_from_email(self):
        from apps.projects.models import Domain

        project, _, _ = self_signup(
            name="", email="ada@gmail.test", store_name="Ada Co", phone="9", oauth=True,
        )
        d = Domain.objects.get(project=project)
        self.assertEqual(d.host, "ada.shopinaday.test")
        self.assertTrue(d.is_verified and d.is_primary)
        project.refresh_from_db()
        self.assertEqual(project.primary_domain, "ada.shopinaday.test")

    def test_phone_is_required(self):
        from django.core.exceptions import ValidationError

        with self.assertRaises(ValidationError):
            self_signup(name="", email="b@shop.test", store_name="B", phone="", oauth=True)

    def test_pins_chosen_plan(self):
        plan = Plan.objects.filter(is_active=True, is_public=True).order_by("-sort_order").first()
        self_signup(
            name="", email="c@shop.test", store_name="C Shop", phone="999", plan=plan,
            oauth=True,
        )
        self.assertEqual(Subscription.objects.get(project__name="C Shop").plan_id, plan.pk)

    def test_gets_a_short_trial_even_if_the_post_save_signal_failed(self):
        """The post_save signal swallows its own errors (e.g. no active/public
        plan at that instant) and leaves no Subscription at all. self_signup
        must not just skip billing setup in that case, nor fall back to the
        longer default trial — it must still create one at the documented
        7-day self-signup length."""
        plan = Plan.objects.filter(is_active=True, is_public=True).order_by("sort_order").first()
        Plan.objects.exclude(pk=plan.pk).update(is_active=False)
        plan.is_active = False
        plan.save(update_fields=["is_active"])  # signal's own lookup now finds nothing

        project, _, _ = self_signup(
            name="", email="e@shop.test", store_name="E Shop", phone="9",
            plan=plan, oauth=True,
        )
        sub = project.subscription
        self.assertEqual(sub.plan_id, plan.pk)
        days = (sub.trial_end - sub.current_period_start).days
        self.assertEqual(days, BillingSettings.load().self_signup_trial_days)

    def test_existing_usable_account_rejected(self):
        User.objects.create_user("dup", email="dup@shop.test", password="x")
        from django.core.exceptions import ValidationError

        with self.assertRaises(ValidationError):
            self_signup(name="", email="dup@shop.test", store_name="D", phone="9", oauth=True)


@override_settings(ALLOWED_HOSTS=["*"])
class SignupPageTests(TestCase):
    @override_settings(**GOOGLE_ON)
    def test_shows_google_button_when_configured(self):
        resp = self.client.get("/accounts/signup/")
        self.assertContains(resp, "Continue with Google")
        self.assertContains(resp, "/accounts/google/start/")

    def test_notice_when_not_configured(self):
        resp = self.client.get("/accounts/signup/")
        self.assertContains(resp, "isn't configured")

    @override_settings(**GOOGLE_ON)
    def test_start_redirects_to_google(self):
        resp = self.client.get("/accounts/google/start/?plan=growth")
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp["Location"].startswith("https://accounts.google.com/o/oauth2/v2/auth"))
        self.assertIn("client_id=cid", resp["Location"])


@override_settings(ALLOWED_HOSTS=["*"], **GOOGLE_ON)
class GoogleCallbackTests(TestCase):
    def _start(self, plan=""):
        self.client.get(f"/accounts/google/start/?plan={plan}")
        return self.client.session["google_oauth_flow"]["state"]

    def test_new_user_goes_to_complete_then_creates_store(self):
        state = self._start(plan="growth")
        with patch("apps.accounts.views.google_oauth.exchange_code") as ex:
            ex.return_value = {
                "email": "new@gmail.test", "email_verified": True,
                "name": "New User", "sub": "123",
            }
            resp = self.client.get(f"/accounts/google/callback/?code=abc&state={state}")
        self.assertRedirects(resp, "/accounts/signup/complete/", fetch_redirect_response=False)

        with self.captureOnCommitCallbacks(execute=True):
            resp = self.client.post(
                "/accounts/signup/complete/",
                {"store_name": "Newco", "phone": "+91 90000 11111"},
            )
        self.assertRedirects(resp, "/admin/start/", fetch_redirect_response=False)
        user = User.objects.get(email="new@gmail.test")
        self.assertFalse(user.has_usable_password())
        self.assertEqual(user.profile.phone, "+91 90000 11111")
        self.assertEqual(
            Subscription.objects.get(project__name="Newco").plan.code, "growth"
        )
        # logged in + gated into the wizard
        self.assertRedirects(
            self.client.get("/admin/products/"), "/admin/start/",
            fetch_redirect_response=False,
        )

    def test_complete_requires_phone(self):
        state = self._start()
        with patch("apps.accounts.views.google_oauth.exchange_code") as ex:
            ex.return_value = {"email": "p@gmail.test", "email_verified": True,
                               "name": "", "sub": "1"}
            self.client.get(f"/accounts/google/callback/?code=abc&state={state}")
        resp = self.client.post("/accounts/signup/complete/", {"store_name": "X"})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(User.objects.filter(email="p@gmail.test").exists())

    def test_bad_state_is_rejected(self):
        self._start()
        resp = self.client.get("/accounts/google/callback/?code=abc&state=wrong")
        self.assertRedirects(resp, "/accounts/signup/", fetch_redirect_response=False)

    def test_existing_account_is_signed_in(self):
        u = User.objects.create_user("ex", email="ex@gmail.test", password="pw", is_staff=True)
        Project.objects.create(name="ExStore", status="active",
                               feature_flags={"onboarded": True})
        Membership.objects.create(
            user=u, project=Project.objects.get(name="ExStore"), role="owner"
        )
        state = self._start()
        with patch("apps.accounts.views.google_oauth.exchange_code") as ex:
            ex.return_value = {"email": "ex@gmail.test", "email_verified": True,
                               "name": "Ex", "sub": "9"}
            resp = self.client.get(f"/accounts/google/callback/?code=abc&state={state}")
        self.assertEqual(resp.status_code, 302)
        self.assertNotIn("/accounts/signup", resp["Location"])
        self.assertEqual(int(self.client.session["_auth_user_id"]), u.pk)

    def test_completion_fires_platform_capi_when_configured(self):
        from apps.marketing.models import PlatformTrackingSettings

        s = PlatformTrackingSettings.load()
        s.is_enabled = True
        s.meta_pixel_id = "123456789012"
        s.meta_capi_token = "tok"
        s.save()

        state = self._start(plan="growth")
        with patch("apps.accounts.views.google_oauth.exchange_code") as ex:
            ex.return_value = {"email": "cap@gmail.test", "email_verified": True,
                               "name": "Cap", "sub": "77"}
            self.client.get(f"/accounts/google/callback/?code=abc&state={state}")

        self.client.cookies["_fbp"] = "fb.1.111.222"
        self.client.cookies["_fbc"] = "fb.1.111.333"
        with self.captureOnCommitCallbacks(execute=True), \
             patch("apps.marketing.tasks.send_platform_capi_event.delay") as delay:
            self.client.post(
                "/accounts/signup/complete/",
                {"store_name": "CapCo", "phone": "9876543210"},
                HTTP_USER_AGENT="test-agent/1.0", REMOTE_ADDR="203.0.113.9",
            )
        delay.assert_called_once()
        kw = delay.call_args.kwargs
        self.assertEqual(kw["event_name"], "CompleteRegistration")
        project = Project.objects.get(name="CapCo")
        self.assertEqual(kw["event_id"], f"signup-{project.pk}")
        # match-quality fields — this is the whole point of a server-only
        # event with no browser pixel behind it to fall back on.
        ud = kw["user_data"]
        self.assertIn("ph", ud)
        self.assertIn("external_id", ud)
        self.assertEqual(ud["client_ip_address"], "203.0.113.9")
        self.assertEqual(ud["client_user_agent"], "test-agent/1.0")
        self.assertEqual(ud["fbp"], "fb.1.111.222")
        self.assertEqual(ud["fbc"], "fb.1.111.333")

    def test_completion_skips_capi_when_not_configured(self):
        state = self._start()
        with patch("apps.accounts.views.google_oauth.exchange_code") as ex:
            ex.return_value = {"email": "nocap@gmail.test", "email_verified": True,
                               "name": "N", "sub": "78"}
            self.client.get(f"/accounts/google/callback/?code=abc&state={state}")

        with self.captureOnCommitCallbacks(execute=True), \
             patch("apps.marketing.tasks.send_platform_capi_event.delay") as delay:
            self.client.post("/accounts/signup/complete/",
                             {"store_name": "NoCapCo", "phone": "9"})
        delay.assert_not_called()


@override_settings(ALLOWED_HOSTS=["*"])
class DgcCreatedStoreGetsLongerTrialTests(TestCase):
    def test_partner_provisioned_store_uses_trial_days(self):
        from apps.accounts.models import PlatformRole, Profile
        from apps.control import store_services

        admin = User.objects.create_superuser("root", "root@t.test", "pw")
        dgc = User.objects.create_user("dgc", "dgc@t.test", "pw", is_staff=True)
        Profile.objects.update_or_create(
            user=dgc, defaults={"platform_role": PlatformRole.MANAGER}
        )
        plan = Plan.objects.filter(is_active=True).order_by("sort_order").first()

        project, _, _, _ = store_services.create_store(
            name="Partner Store", owner_email="po@store.test", plan=plan,
            actor=admin, manager=dgc,
        )
        sub = project.subscription
        self.assertEqual(sub.manager_id, dgc.pk)
        days = (sub.trial_end - sub.current_period_start).days
        self.assertEqual(days, BillingSettings.load().trial_days)
        self.assertEqual(days, 14)
