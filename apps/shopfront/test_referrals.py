"""Storefront side of the per-store referral program: beacon click
tracking + cookie, checkout attribution, the account-page teaser, and the
referrer's own dashboard (join / stats). Service-level logic is covered by
apps.referrals.tests; the Mission Control admin screen by
apps.control.test_referral_program."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.catalog.models import Product
from apps.projects.models import Domain, Project
from apps.referrals.models import ReferralAttribution, ReferralClick, ReferralProgram, Referrer

User = get_user_model()


@override_settings(ALLOWED_HOSTS=["*"])
class BeaconReferralTrackingTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="BeaconRefCo", status="active", feature_flags={"onboarded": True},
        )
        Domain.objects.create(project=self.project, host="beaconref.test", is_verified=True)
        self.user = User.objects.create_user("bref", "bref@t.test", "pw")
        self.referrer = Referrer.objects.create(project=self.project, user=self.user)

    def _post(self, data):
        return self.client.post("/beacon/", data, HTTP_HOST="beaconref.test")

    def test_no_click_or_cookie_when_program_not_active(self):
        resp = self._post({"kind": "view", "event": "page_view", "path": "/", "ref": self.referrer.code})
        self.assertNotIn("sd_ref", resp.cookies)
        self.assertEqual(ReferralClick.objects.count(), 0)

    def test_click_and_cookie_set_when_program_active(self):
        ReferralProgram.objects.create(project=self.project, is_active=True, cookie_days=30)
        resp = self._post({"kind": "view", "event": "page_view", "path": "/", "ref": self.referrer.code})
        self.assertIn("sd_ref", resp.cookies)
        self.assertEqual(resp.cookies["sd_ref"].value, self.referrer.code)
        self.assertEqual(ReferralClick.objects.filter(referrer=self.referrer).count(), 1)

    def test_cookie_expiry_matches_program_cookie_days(self):
        ReferralProgram.objects.create(project=self.project, is_active=True, cookie_days=7)
        resp = self._post({"kind": "view", "event": "page_view", "path": "/", "ref": self.referrer.code})
        self.assertEqual(int(resp.cookies["sd_ref"]["max-age"]), 7 * 86400)

    def test_bad_ref_code_is_a_silent_no_op(self):
        ReferralProgram.objects.create(project=self.project, is_active=True)
        resp = self._post({"kind": "view", "event": "page_view", "path": "/", "ref": "NOPE9999"})
        self.assertEqual(resp.status_code, 204)
        self.assertNotIn("sd_ref", resp.cookies)

    def test_heartbeat_does_not_record_a_click(self):
        ReferralProgram.objects.create(project=self.project, is_active=True)
        self._post({"kind": "heartbeat", "path": "/", "ref": self.referrer.code})
        self.assertEqual(ReferralClick.objects.count(), 0)

    def test_no_ref_param_is_unaffected(self):
        ReferralProgram.objects.create(project=self.project, is_active=True)
        resp = self._post({"kind": "view", "event": "page_view", "path": "/"})
        self.assertEqual(resp.status_code, 204)
        self.assertNotIn("sd_ref", resp.cookies)


@override_settings(ALLOWED_HOSTS=["*"])
class CheckoutAttributionTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="CheckoutRefCo", status="active", feature_flags={"onboarded": True},
        )
        Domain.objects.create(project=self.project, host="checkoutref.test", is_verified=True)
        self.product = Product.objects.create(
            project=self.project, title="Mug", slug="mug", status="active", price=Decimal("299"),
        )
        self.user = User.objects.create_user("cref", "cref@t.test", "pw")
        self.referrer = Referrer.objects.create(project=self.project, user=self.user)
        ReferralProgram.objects.create(project=self.project, is_active=True)

        self.client.post(
            "/cart/add/", {"product": "mug", "quantity": "1"}, HTTP_HOST="checkoutref.test",
        )

    def _checkout(self, cookie=None):
        if cookie is not None:
            self.client.cookies["sd_ref"] = cookie
        return self.client.post("/checkout/", {
            "email": "buyer@t.test", "name": "Buyer", "line1": "1 Main St",
            "city": "Town", "state": "State", "postal_code": "560001",
            "country": "IN", "phone": "9999999999", "payment_method": "cod",
        }, HTTP_HOST="checkoutref.test")

    def test_order_attributed_when_ref_cookie_present(self):
        self._checkout(cookie=self.referrer.code)
        attribution = ReferralAttribution.objects.get(project=self.project)
        self.assertEqual(attribution.referrer_id, self.referrer.pk)

    def test_no_attribution_without_a_cookie(self):
        self._checkout()
        self.assertFalse(ReferralAttribution.objects.exists())

    def test_bad_cookie_value_does_not_break_checkout(self):
        resp = self._checkout(cookie="GARBAGE1")
        self.assertEqual(resp.status_code, 302)  # order still placed, redirected
        self.assertFalse(ReferralAttribution.objects.exists())


@override_settings(ALLOWED_HOSTS=["*"])
class AccountPageTeaserTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="TeaserCo", status="active", feature_flags={"onboarded": True},
        )
        Domain.objects.create(project=self.project, host="teaserco.test", is_verified=True)
        self.user = User.objects.create_user("teaser", "teaser@t.test", "pw")

    def _login(self):
        self.client.force_login(self.user)

    def test_teaser_hidden_when_program_off(self):
        self._login()
        body = self.client.get("/account/", HTTP_HOST="teaserco.test").content.decode()
        self.assertNotIn("Refer &amp; earn", body)

    def test_teaser_shown_when_program_on(self):
        ReferralProgram.objects.create(project=self.project, is_active=True)
        self._login()
        body = self.client.get("/account/", HTTP_HOST="teaserco.test").content.decode()
        self.assertIn("Refer &amp; earn", body)
        self.assertIn("/account/referrals/", body)


@override_settings(ALLOWED_HOSTS=["*"])
class ReferralDashboardPageTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="DashCo", status="active", feature_flags={"onboarded": True},
        )
        Domain.objects.create(project=self.project, host="dashco.test", is_verified=True)
        self.user = User.objects.create_user("dash", "dash@t.test", "pw")

    def test_anonymous_sees_sign_in_prompt(self):
        body = self.client.get("/account/referrals/", HTTP_HOST="dashco.test").content.decode()
        self.assertIn("Sign in", body)

    def test_program_off_shows_notice(self):
        self.client.force_login(self.user)
        body = self.client.get("/account/referrals/", HTTP_HOST="dashco.test").content.decode()
        self.assertIn("hasn't turned on", body)

    def test_program_on_not_yet_a_referrer_shows_join_cta(self):
        ReferralProgram.objects.create(project=self.project, is_active=True)
        self.client.force_login(self.user)
        body = self.client.get("/account/referrals/", HTTP_HOST="dashco.test").content.decode()
        self.assertIn("Become a referrer", body)

    def test_join_creates_a_referrer_and_redirects_to_dashboard(self):
        ReferralProgram.objects.create(project=self.project, is_active=True)
        self.client.force_login(self.user)
        resp = self.client.post("/account/referrals/join/", HTTP_HOST="dashco.test")
        self.assertRedirects(resp, "/account/referrals/")
        self.assertTrue(Referrer.objects.filter(project=self.project, user=self.user).exists())

    def test_join_is_a_no_op_when_program_is_off(self):
        self.client.force_login(self.user)
        self.client.post("/account/referrals/join/", HTTP_HOST="dashco.test")
        self.assertFalse(Referrer.objects.filter(project=self.project, user=self.user).exists())

    def test_existing_referrer_sees_code_and_link(self):
        ReferralProgram.objects.create(project=self.project, is_active=True)
        referrer = Referrer.objects.create(project=self.project, user=self.user)
        self.client.force_login(self.user)
        body = self.client.get("/account/referrals/", HTTP_HOST="dashco.test").content.decode()
        self.assertIn(referrer.code, body)
        self.assertIn(f"?ref={referrer.code}", body)

    def test_join_is_idempotent_reuses_existing_code(self):
        ReferralProgram.objects.create(project=self.project, is_active=True)
        self.client.force_login(self.user)
        self.client.post("/account/referrals/join/", HTTP_HOST="dashco.test")
        first_code = Referrer.objects.get(project=self.project, user=self.user).code
        self.client.post("/account/referrals/join/", HTTP_HOST="dashco.test")
        self.assertEqual(Referrer.objects.filter(project=self.project, user=self.user).count(), 1)
        self.assertEqual(Referrer.objects.get(project=self.project, user=self.user).code, first_code)
