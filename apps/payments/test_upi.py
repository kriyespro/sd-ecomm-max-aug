"""Direct UPI: owner's UPI ID + QR, shopper submits a UTR, staff confirm."""

from decimal import Decimal

from django.test import TestCase

from apps.checkout import services as checkout
from apps.orders.models import Order
from apps.payments import services as pay
from apps.payments.models import Payment, PaymentProviderConfig, PaymentStatus, Provider
from apps.payments.providers.upi import UPIProvider
from apps.projects.models import Project


class DirectUpiTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="UpiCo", status="active", currency="INR")
        self.cfg = PaymentProviderConfig.objects.create(
            project=self.project, provider=Provider.UPI, is_enabled=True,
            credentials={"upi_id": "shop@okhdfcbank"}, config={"payee_name": "Upi Co"},
        )
        self.order = Order.objects.create(
            project=self.project, number="UPI-1", email="b@t.test", grand_total=Decimal("499.50"),
        )

    def _pending(self):
        return pay.record_offline_payment(order=self.order, provider_key=Provider.UPI)

    def test_checkout_allows_upi_when_enabled(self):
        checkout._validate_payment_method(self.project, "upi")  # no raise

    def test_checkout_rejects_upi_when_not_enabled(self):
        self.cfg.is_enabled = False
        self.cfg.save()
        with self.assertRaises(checkout.CheckoutError):
            checkout._validate_payment_method(self.project, "upi")

    def test_offline_upi_stays_pending_and_order_unconfirmed(self):
        p = self._pending()
        self.order.refresh_from_db()
        self.assertEqual(p.status, PaymentStatus.PENDING)
        self.assertEqual(self.order.status, "pending")
        self.assertNotEqual(self.order.payment_status, "paid")

    def test_intent_link_has_amount_and_ref(self):
        provider, _ = pay.get_provider(self.project, "upi")
        link = provider.intent_url(amount=Decimal("499.5"), order_number="UPI-1")
        self.assertTrue(link.startswith("upi://pay?"))
        for part in ("pa=shop%40okhdfcbank", "am=499.50", "cu=INR", "tn=Order%20UPI-1"):
            self.assertIn(part, link)

    def test_utr_saved_then_capture_settles(self):
        p = self._pending()
        pay.submit_upi_reference(payment=p, utr="412345678901")
        p.refresh_from_db()
        self.assertEqual(p.provider_payment_id, "412345678901")
        self.assertEqual(p.status, PaymentStatus.PENDING)
        pay.capture_payment(payment=p)
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, "paid")

    def test_bad_or_duplicate_utr_rejected(self):
        p = self._pending()
        with self.assertRaises(pay.PaymentError):
            pay.submit_upi_reference(payment=p, utr="12")
        pay.submit_upi_reference(payment=p, utr="412345678901")
        other = Order.objects.create(project=self.project, number="UPI-2", email="c@t.test",
                                     grand_total=Decimal("10"))
        p2 = pay.record_offline_payment(order=other, provider_key=Provider.UPI)
        with self.assertRaises(pay.PaymentError):
            pay.submit_upi_reference(payment=p2, utr="412345678901")

    def test_public_verify_cannot_settle_or_fail_upi(self):
        p = self._pending()
        with self.assertRaises(pay.PaymentError):
            pay.verify_payment(payment=p, data={"anything": "x"})
        p.refresh_from_db()
        self.assertEqual(p.status, PaymentStatus.PENDING)

    def test_order_page_shows_panel_and_utr_post(self):
        self._pending()
        s = self.client.session
        s["shopfront_orders"] = ["UPI-1"]
        s.save()
        host = {"HTTP_HOST": "testserver"}
        from apps.shopfront import views
        panel = views._upi_panel(self.project, Order.objects.get(pk=self.order.pk))
        self.assertEqual(panel["upi_id"], "shop@okhdfcbank")
        self.assertTrue(panel["qr_svg"].startswith("data:image/svg+xml;base64,"))


class UpiFormTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="UpiForm", status="active")

    def _form(self, **extra):
        from apps.control.forms import PaymentProviderForm

        data = {"provider": "upi", "priority": "100", "is_enabled": "on",
                "upi_id": "shop@okhdfcbank", "payee_name": "Shop"}
        data.update(extra)
        return PaymentProviderForm(data, project=self.project)

    def test_saves_upi_id_and_payee(self):
        form = self._form()
        self.assertTrue(form.is_valid(), form.errors)
        obj = form.save()
        self.assertEqual(obj.credentials["upi_id"], "shop@okhdfcbank")
        self.assertEqual(obj.config["payee_name"], "Shop")

    def test_bad_upi_id_rejected(self):
        self.assertFalse(self._form(upi_id="not a upi id").is_valid())

    def test_enable_requires_upi_id(self):
        self.assertFalse(self._form(upi_id="").is_valid())
