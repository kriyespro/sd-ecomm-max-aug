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


import hashlib
import hmac
import json


def _sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@override_settings(RAZORPAY_WEBHOOK_SECRET="whsec_test", RAZORPAY_KEY_ID="rzp_live_x",
                   RAZORPAY_KEY_SECRET="s")
class BillingWebhookTests(TestCase):
    def setUp(self):
        BillingSettings.objects.all().delete()
        self.project = Project.objects.create(name="WhCo", status="active")
        self.sub = billing_svc.ensure_subscription(self.project)
        now = timezone.now()
        self.invoice = Invoice.objects.create(
            subscription=self.sub, number="INV-WH-1", amount=Decimal("500"),
            status=InvoiceStatus.OPEN, period_start=now, period_end=now, due_at=now,
            provider_order_id="order_ABC",
        )

    def _event(self, *, event="payment.captured", order_id="order_ABC",
               payment_id="pay_1", amount=50000):
        return json.dumps({
            "event": event,
            "payload": {"payment": {"entity": {
                "id": payment_id, "order_id": order_id, "amount": amount,
            }}},
        }).encode()

    def _post(self, body, secret="whsec_test"):
        return self.client.post(
            "/billing/webhook/razorpay/", data=body, content_type="application/json",
            HTTP_X_RAZORPAY_SIGNATURE=_sign(secret, body),
        )

    def test_valid_capture_settles_the_invoice(self):
        r = self._post(self._event())
        self.assertEqual(r.status_code, 200)
        self.invoice.refresh_from_db()
        self.sub.refresh_from_db()
        self.assertEqual(self.invoice.status, InvoiceStatus.PAID)
        self.assertEqual(self.invoice.provider_payment_id, "pay_1")
        self.assertEqual(self.sub.status, "active")

    def test_bad_signature_is_400(self):
        r = self._post(self._event(), secret="wrong")
        self.assertEqual(r.status_code, 400)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, InvoiceStatus.OPEN)

    def test_short_paid_amount_rejected(self):
        r = self._post(self._event(amount=10000))  # ₹100 < ₹500 due
        self.assertEqual(r.status_code, 400)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, InvoiceStatus.OPEN)

    def test_unknown_order_is_acknowledged_noop(self):
        r = self._post(self._event(order_id="order_NOPE", payment_id="pay_x"))
        self.assertEqual(r.status_code, 200)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, InvoiceStatus.OPEN)

    def test_already_paid_is_noop(self):
        billing_svc.mark_invoice_paid(self.invoice, provider_payment_id="pay_first")
        r = self._post(self._event(payment_id="pay_second"))
        self.assertEqual(r.status_code, 200)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.provider_payment_id, "pay_first")

    def test_non_payment_event_ignored(self):
        r = self._post(self._event(event="payment.failed"))
        self.assertEqual(r.status_code, 200)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, InvoiceStatus.OPEN)

    @override_settings(RAZORPAY_WEBHOOK_SECRET="")
    def test_no_secret_configured_is_noop_not_error(self):
        body = self._event()
        r = self.client.post(
            "/billing/webhook/razorpay/", data=body, content_type="application/json",
            HTTP_X_RAZORPAY_SIGNATURE="anything",
        )
        self.assertEqual(r.status_code, 200)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, InvoiceStatus.OPEN)
