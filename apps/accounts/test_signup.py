from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.accounts.models import Membership
from apps.accounts.signup import self_signup
from apps.billing.models import BillingSettings, Plan, Subscription
from apps.projects.models import Project

User = get_user_model()


@override_settings(ALLOWED_HOSTS=["*"])
class SelfSignupServiceTests(TestCase):
    def test_creates_owner_store_and_short_trial(self):
        with self.captureOnCommitCallbacks(execute=True):
            project, user, created = self_signup(
                name="Ada Owner", email="Ada@Shop.test", password="s3cret-pw-9x",
                store_name="Ada's Shop",
            )
        self.assertTrue(created)
        self.assertTrue(user.is_staff)
        self.assertEqual(user.email, "ada@shop.test")
        self.assertTrue(user.check_password("s3cret-pw-9x"))
        self.assertEqual(
            Membership.objects.get(user=user, project=project).role, "owner"
        )
        self.assertEqual(project.status, Project.Status.ACTIVE)

        sub = project.subscription
        self.assertEqual(sub.status, "trialing")
        self.assertIsNone(sub.manager_id)
        days = (sub.trial_end - sub.current_period_start).days
        self.assertEqual(days, BillingSettings.load().self_signup_trial_days)
        self.assertEqual(days, 7)

        # demo content seeded, store not yet onboarded
        project.refresh_from_db()
        self.assertTrue(project.feature_flags.get("demo_seeded"))
        self.assertFalse(project.feature_flags.get("onboarded"))

    def test_pins_chosen_plan(self):
        plan = Plan.objects.filter(is_active=True, is_public=True).order_by("-sort_order").first()
        _, _, _ = self_signup(
            name="", email="b@shop.test", password="s3cret-pw-9x",
            store_name="B Shop", plan=plan,
        )
        self.assertEqual(Subscription.objects.get(project__name="B Shop").plan_id, plan.pk)

    def test_existing_usable_account_is_rejected(self):
        User.objects.create_user("dup", email="dup@shop.test", password="x")
        from django.core.exceptions import ValidationError

        with self.assertRaises(ValidationError):
            self_signup(
                name="", email="dup@shop.test", password="s3cret-pw-9x",
                store_name="Dup Shop",
            )


@override_settings(ALLOWED_HOSTS=["*"])
class SignupViewTests(TestCase):
    def test_get_renders(self):
        resp = self.client.get("/accounts/signup/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Start your store")

    def test_post_signs_up_logs_in_and_lands_in_wizard(self):
        with self.captureOnCommitCallbacks(execute=True):
            resp = self.client.post(
                "/accounts/signup/",
                {
                    "store_name": "Nova", "full_name": "Nova Owner",
                    "email": "nova@shop.test", "password": "s3cret-pw-9x",
                },
            )
        self.assertRedirects(resp, "/admin/start/", fetch_redirect_response=False)
        # logged in + gated into onboarding
        self.assertRedirects(
            self.client.get("/admin/products/"), "/admin/start/",
            fetch_redirect_response=False,
        )

    def test_plan_query_param_prefills(self):
        plan = Plan.objects.filter(is_active=True, is_public=True).first()
        resp = self.client.get(f"/accounts/signup/?plan={plan.code}")
        self.assertContains(resp, f'value="{plan.code}"')

    def test_weak_password_rejected(self):
        resp = self.client.post(
            "/accounts/signup/",
            {"store_name": "X", "email": "x@shop.test", "password": "123"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(User.objects.filter(email="x@shop.test").exists())


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
