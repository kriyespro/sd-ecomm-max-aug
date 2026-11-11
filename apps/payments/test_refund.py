"""refund_payment previously updated Payment.status but left Order.payment_status
stale (still "paid" after a real refund) -- the storefront order page read that
field directly. Covers the fix in isolation, independent of any RMA flow."""

from decimal import Decimal

from django.test import TestCase

from apps.orders.models import Order, PaymentStatus as OrderPaymentStatus
from apps.payments.models import Payment, PaymentStatus, Provider
from apps.payments.services import refund_payment
from apps.projects.models import Project


class RefundSyncsOrderPaymentStatusTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="RefundCo", status="active", currency="INR")
        self.order = Order.objects.create(
            project=self.project, number="RF-1", email="buyer@t.test",
            subtotal=Decimal("1000"), grand_total=Decimal("1000"),
            status="delivered", payment_status=OrderPaymentStatus.PAID,
        )
        self.payment = Payment.objects.create(
            project=self.project, order=self.order, provider=Provider.COD,
            amount=Decimal("1000"), status=PaymentStatus.PAID,
        )

    def test_full_refund_flips_order_payment_status_to_refunded(self):
        refund_payment(payment=self.payment, amount=Decimal("1000"))
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, OrderPaymentStatus.REFUNDED)

    def test_partial_refund_flips_order_payment_status_to_partially_refunded(self):
        refund_payment(payment=self.payment, amount=Decimal("400"))
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, OrderPaymentStatus.PARTIALLY_REFUNDED)
