"""verify_payment's failure path — a bad-signature callback must actually
record itself (Payment.FAILED persisted, PaymentEvent logged, the domain
event fires for any configured webhook) instead of the whole thing
silently rolling back along with the raise that reports it to the caller.

Regression for a real bug: verify_payment was @transaction.atomic at the
top level, so on failure `payment.save()` (status=FAILED) + the on_commit-
scheduled emit() both got wiped by the very `raise PaymentError(...)` that
was supposed to report the failure — a failed verification looked
identical, in the database, to one that was simply never attempted."""

from decimal import Decimal

from django.test import TestCase

from apps.orders.models import Order
from apps.payments import services as pay
from apps.payments.models import Payment, PaymentEvent, PaymentProviderConfig, PaymentStatus, Provider
from apps.projects.models import Project


class VerifyPaymentFailurePersistsTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="PayFailCo", status="active", currency="INR")
        PaymentProviderConfig.objects.create(
            project=self.project, provider=Provider.MANUAL, is_enabled=True,
        )
        self.order = Order.objects.create(
            project=self.project, number="PFC-1", email="buyer@t.test",
            grand_total=Decimal("500"),
        )
        self.payment = Payment.objects.create(
            project=self.project, order=self.order, provider=Provider.MANUAL,
            amount=Decimal("500"), currency="INR", status=PaymentStatus.PENDING,
        )

    def test_bad_signature_leaves_a_failed_record_not_a_rollback(self):
        # ManualProvider.verify() always returns False by design (manual
        # payments only settle via an authenticated staff capture) — a
        # deterministic way to hit the failure branch with no mocking.
        with self.captureOnCommitCallbacks(execute=True):
            with self.assertRaises(pay.PaymentError):
                pay.verify_payment(payment=self.payment, data={})

        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, PaymentStatus.FAILED)
        self.assertTrue(self.payment.error_message)
        self.assertIsNotNone(self.payment.failed_at)

        event = PaymentEvent.objects.filter(
            payment=self.payment, kind=PaymentEvent.Kind.VERIFY,
        ).first()
        self.assertIsNotNone(event)
        self.assertFalse(event.signature_valid)
