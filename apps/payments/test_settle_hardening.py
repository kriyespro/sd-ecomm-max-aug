"""Hardening found by a deep security/bug audit of _settle() and reconcile():
double-settle idempotency, never-pay-after a cancelled/failed order, and
reconcile() no longer false-flagging a correctly PARTIALLY_PAID order."""

from decimal import Decimal

from django.test import TestCase

from apps.orders.models import Order, OrderStatus, PaymentStatus as OrderPaymentStatus
from apps.payments.models import Payment, PaymentStatus, Provider
from apps.payments.services import PaymentError, capture_payment, reconcile
from apps.projects.models import Project


class DoubleSettleIdempotencyTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="SettleCo", status="active", currency="INR")
        self.order = Order.objects.create(
            project=self.project, number="ST-1", email="buyer@t.test",
            subtotal=Decimal("1000"), grand_total=Decimal("1000"), status=OrderStatus.PENDING,
        )
        self.payment = Payment.objects.create(
            project=self.project, order=self.order, provider=Provider.COD,
            amount=Decimal("1000"), status=PaymentStatus.PENDING,
        )

    def test_settling_the_same_already_paid_payment_again_is_a_no_op(self):
        capture_payment(payment=self.payment)
        self.payment.refresh_from_db()
        captured_at = self.payment.captured_at
        # Simulate a replayed client callback / retried webhook hitting the
        # same already-PAID payment -- must not re-run settlement side effects.
        result = capture_payment(payment=self.payment)
        self.assertEqual(result.captured_at, captured_at)
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, OrderPaymentStatus.PAID)


class NeverSettleAfterCancelledTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="CancelSettleCo", status="active", currency="INR")
        self.order = Order.objects.create(
            project=self.project, number="CN-1", email="buyer@t.test",
            subtotal=Decimal("1000"), grand_total=Decimal("1000"), status=OrderStatus.CANCELLED,
        )
        self.payment = Payment.objects.create(
            project=self.project, order=self.order, provider=Provider.COD,
            amount=Decimal("1000"), status=PaymentStatus.PENDING,
        )

    def test_capturing_payment_on_a_cancelled_order_is_rejected(self):
        with self.assertRaises(PaymentError):
            capture_payment(payment=self.payment)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, PaymentStatus.PENDING)


class ReconcilePartiallyPaidTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="ReconcileCo", status="active", currency="INR")

    def test_correctly_partially_paid_order_is_not_a_mismatch(self):
        order = Order.objects.create(
            project=self.project, number="RC-1", email="buyer@t.test",
            subtotal=Decimal("1000"), grand_total=Decimal("1000"),
            status=OrderStatus.CONFIRMED, payment_status=OrderPaymentStatus.PARTIALLY_PAID,
        )
        Payment.objects.create(
            project=self.project, order=order, provider=Provider.UPI,
            amount=Decimal("100"), status=PaymentStatus.PAID,
        )
        Payment.objects.create(
            project=self.project, order=order, provider=Provider.COD,
            amount=Decimal("900"), status=PaymentStatus.PENDING,
        )
        mismatches = reconcile(self.project)
        self.assertEqual([o for o, _ in mismatches if o.pk == order.pk], [])

    def test_fully_settled_but_still_marked_partially_paid_is_a_mismatch(self):
        order = Order.objects.create(
            project=self.project, number="RC-2", email="buyer@t.test",
            subtotal=Decimal("1000"), grand_total=Decimal("1000"),
            status=OrderStatus.CONFIRMED, payment_status=OrderPaymentStatus.PARTIALLY_PAID,
        )
        Payment.objects.create(
            project=self.project, order=order, provider=Provider.UPI,
            amount=Decimal("1000"), status=PaymentStatus.PAID,
        )
        mismatches = reconcile(self.project)
        self.assertEqual(len(mismatches), 1)
        self.assertEqual(mismatches[0][0].pk, order.pk)
