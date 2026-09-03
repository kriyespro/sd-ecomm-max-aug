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

        project, _, _ = store_services.create_store(
            name="Partner Store", owner_email="po@store.test", plan=plan,
            actor=admin, manager=dgc,
        )
        sub = project.subscription
        self.assertEqual(sub.manager_id, dgc.pk)
        days = (sub.trial_end - sub.current_period_start).days
        self.assertEqual(days, BillingSettings.load().trial_days)
        self.assertEqual(days, 14)
