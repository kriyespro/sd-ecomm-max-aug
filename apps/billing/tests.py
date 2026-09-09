from decimal import Decimal

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.billing import razorpay, services as billing_svc
from apps.billing.models import BillingSettings, Invoice, InvoiceStatus
from apps.projects.models import Project


class EffectiveRazorpayCredsTests(TestCase):
    def setUp(self):
        BillingSettings.objects.all().delete()
        self.cfg = BillingSettings.load()

    @override_settings(RAZORPAY_KEY_ID="rzp_test_env", RAZORPAY_KEY_SECRET="env_secret")
    def test_env_used_when_db_blank(self):
        self.assertEqual(self.cfg.effective_key_id, "rzp_test_env")
        self.assertEqual(self.cfg.effective_key_secret, "env_secret")
        self.assertTrue(self.cfg.razorpay_configured)

    @override_settings(RAZORPAY_KEY_ID="rzp_test_env", RAZORPAY_KEY_SECRET="env_secret")
    def test_db_value_overrides_env(self):
        self.cfg.razorpay_key_id = "rzp_live_db"
        self.cfg.razorpay_key_secret = "db_secret"
        self.assertEqual(self.cfg.effective_key_id, "rzp_live_db")
        self.assertEqual(self.cfg.effective_key_secret, "db_secret")

    def test_not_configured_when_nothing_set(self):
        self.assertFalse(self.cfg.razorpay_configured)

    @override_settings(RAZORPAY_KEY_ID="rzp_live_x", RAZORPAY_KEY_SECRET="s", RAZORPAY_TEST_MODE="")
    def test_test_mode_inferred_from_live_prefix(self):
        self.cfg.is_test_mode = True  # DB says test, but the key is live
        self.assertFalse(self.cfg.effective_test_mode)

    @override_settings(RAZORPAY_KEY_ID="rzp_test_x", RAZORPAY_KEY_SECRET="s", RAZORPAY_TEST_MODE="")
    def test_test_mode_inferred_from_test_prefix(self):
        self.cfg.is_test_mode = False
        self.assertTrue(self.cfg.effective_test_mode)

    @override_settings(RAZORPAY_KEY_ID="rzp_live_x", RAZORPAY_KEY_SECRET="s", RAZORPAY_TEST_MODE="true")
    def test_forced_env_test_mode_wins_over_prefix(self):
        self.assertTrue(self.cfg.effective_test_mode)

    def test_test_mode_falls_back_to_db_flag_without_prefix_or_env(self):
        self.cfg.razorpay_key_id = "somekey"
        self.cfg.is_test_mode = True
        self.assertTrue(self.cfg.effective_test_mode)
        self.cfg.is_test_mode = False
        self.assertFalse(self.cfg.effective_test_mode)


class StartPaymentGuardTests(TestCase):
    def setUp(self):
        BillingSettings.objects.all().delete()
        self.project = Project.objects.create(name="BillCo", status="active")
        self.sub = billing_svc.ensure_subscription(self.project)
        now = timezone.now()
        self.invoice = Invoice.objects.create(
            subscription=self.sub, number="INV-T-1", amount=Decimal("999"),
            status=InvoiceStatus.OPEN, period_start=now, period_end=now, due_at=now,
        )

    @override_settings(RAZORPAY_KEY_ID="", RAZORPAY_KEY_SECRET="", RAZORPAY_TEST_MODE="false")
    def test_refuses_when_unconfigured_and_live(self):
        with self.assertRaises(billing_svc.BillingError):
            billing_svc.start_payment(self.invoice)

    @override_settings(RAZORPAY_KEY_ID="", RAZORPAY_KEY_SECRET="", RAZORPAY_TEST_MODE="true")
    def test_synthetic_order_in_test_mode_without_keys(self):
        res = billing_svc.start_payment(self.invoice)
        self.assertTrue(res["synthetic"])
        self.assertTrue(res["order_id"].startswith("order_test_"))

    @override_settings(RAZORPAY_KEY_ID="rzp_test_abc", RAZORPAY_KEY_SECRET="s")
    def test_key_id_in_checkout_params_comes_from_env(self):
        res = billing_svc.start_payment(self.invoice)
        self.assertEqual(res["key_id"], "rzp_test_abc")


class CreateOrderTests(TestCase):
    @override_settings(RAZORPAY_KEY_ID="rzp_test_x", RAZORPAY_KEY_SECRET="s")
    def test_create_order_synthetic_when_api_unreachable_in_test_mode(self):
        BillingSettings.objects.all().delete()
        cfg = BillingSettings.load()
        res = razorpay.create_order(
            amount=Decimal("100"), receipt="R1", notes={}, settings=cfg,
        )
        # rzp_test_ prefix -> test mode -> network failure falls back to synthetic
        self.assertTrue(res["synthetic"])
        self.assertEqual(res["key_id"], "rzp_test_x")
