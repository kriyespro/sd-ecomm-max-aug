from decimal import Decimal

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.billing import razorpay, services as billing_svc
from apps.billing.models import (
    BillingPeriod,
    BillingSettings,
    Invoice,
    InvoiceStatus,
    Plan,
    SubscriptionStatus,
)
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


class ChangePlanBillingTests(TestCase):
    def setUp(self):
        BillingSettings.objects.all().delete()
        self.project = Project.objects.create(name="BuyCo", status="active")
        self.sub = billing_svc.ensure_subscription(self.project)
        self.other_plan = (
            Plan.objects.filter(is_active=True, is_public=True)
            .exclude(pk=self.sub.plan_id).order_by("sort_order").first()
        )

    def test_buying_during_trial_bills_starting_today(self):
        self.assertEqual(self.sub.status, SubscriptionStatus.TRIALING)
        before = timezone.now()
        billing_svc.change_plan(self.sub, plan=self.other_plan, period=BillingPeriod.MONTHLY)
        inv = self.sub.invoices.get(status=InvoiceStatus.OPEN)
        self.assertGreaterEqual(inv.period_start, before)
        self.assertEqual(self.sub.plan_id, self.other_plan.pk)

    def test_paying_the_trial_invoice_activates_the_subscription(self):
        billing_svc.change_plan(self.sub, plan=self.other_plan, period=BillingPeriod.MONTHLY)
        inv = self.sub.invoices.get(status=InvoiceStatus.OPEN)
        billing_svc.mark_invoice_paid(inv, provider_payment_id="test")
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, SubscriptionStatus.ACTIVE)

    def test_switching_an_active_subscription_defers_billing_to_next_cycle(self):
        self.sub.status = SubscriptionStatus.ACTIVE
        self.sub.save(update_fields=["status"])
        original_period_end = self.sub.current_period_end
        billing_svc.change_plan(self.sub, plan=self.other_plan, period=BillingPeriod.MONTHLY)
        inv = self.sub.invoices.get(status=InvoiceStatus.OPEN)
        self.assertEqual(inv.period_start, original_period_end)

    def test_reactivating_a_suspended_subscription_bills_starting_today(self):
        self.sub.status = SubscriptionStatus.SUSPENDED
        self.sub.save(update_fields=["status"])
        before = timezone.now()
        billing_svc.change_plan(self.sub, plan=self.sub.plan, period=self.sub.period)
        inv = self.sub.invoices.get(status=InvoiceStatus.OPEN)
        self.assertGreaterEqual(inv.period_start, before)

    def test_second_change_while_invoice_open_does_not_duplicate_it(self):
        billing_svc.change_plan(self.sub, plan=self.other_plan, period=BillingPeriod.MONTHLY)
        billing_svc.change_plan(self.sub, plan=self.sub.plan, period=BillingPeriod.YEARLY)
        self.assertEqual(self.sub.invoices.filter(status=InvoiceStatus.OPEN).count(), 1)


class SubscriptionGateMiddlewareTests(TestCase):
    """A cancelled subscription must be gated off the storefront exactly like
    a suspended one — otherwise it stays live and sellable forever once
    mark_invoice_paid() flips a cancel_at_period_end subscription straight to
    CANCELLED on its last payment."""

    def setUp(self):
        from apps.billing.middleware import SubscriptionGateMiddleware

        self.project = Project.objects.create(name="GateCo", status="active")
        self.sub = billing_svc.ensure_subscription(self.project)
        self.middleware = SubscriptionGateMiddleware(get_response=lambda r: "PASSED")

    def _request(self, path="/app/"):
        class _Req:
            pass

        req = _Req()
        req.path = path
        req.project = self.project
        return req

    def test_cancelled_subscription_blocks_the_storefront(self):
        self.sub.status = SubscriptionStatus.CANCELLED
        self.sub.save(update_fields=["status"])
        resp = self.middleware(self._request())
        self.assertEqual(resp.status_code, 503)

    def test_active_subscription_passes_through(self):
        self.sub.status = SubscriptionStatus.ACTIVE
        self.sub.save(update_fields=["status"])
        self.assertEqual(self.middleware(self._request()), "PASSED")

    def test_admin_path_is_exempt_even_when_cancelled(self):
        self.sub.status = SubscriptionStatus.CANCELLED
        self.sub.save(update_fields=["status"])
        self.assertEqual(self.middleware(self._request("/admin/plan/")), "PASSED")


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
