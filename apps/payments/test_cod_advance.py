"""Advance/partial payment on COD orders: mark_paid() now sums an order's
settled payments instead of assuming one full payment, _settle() spawns the
COD remainder once the advance clears, and the full round trip ends PAID."""

from decimal import Decimal

from django.test import TestCase

from apps.orders.models import Order, OrderStatus, PaymentStatus as OrderPaymentStatus
from apps.orders.services import mark_paid
from apps.payments.models import Payment, PaymentProviderConfig, PaymentStatus, Provider
from apps.payments.services import capture_payment, cod_advance_plan, record_offline_payment
from apps.projects.models import Project


class MarkPaidSettlementTests(TestCase):
    """Regression guard: every pre-existing caller settles the full amount in
    one payment, so this must still resolve to PAID, not PARTIALLY_PAID."""

    def setUp(self):
        self.project = Project.objects.create(name="MarkPaidCo", status="active", currency="INR")
        self.order = Order.objects.create(
            project=self.project, number="MP-1", email="buyer@t.test",
            subtotal=Decimal("1000"), grand_total=Decimal("1000"), status=OrderStatus.PENDING,
        )

    def test_single_full_payment_still_yields_paid(self):
        Payment.objects.create(
            project=self.project, order=self.order, provider=Provider.COD,
            amount=Decimal("1000"), status=PaymentStatus.PAID,
        )
        mark_paid(order=self.order)
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, OrderPaymentStatus.PAID)
        self.assertEqual(self.order.status, OrderStatus.CONFIRMED)

    def test_single_partial_payment_yields_partially_paid(self):
        Payment.objects.create(
            project=self.project, order=self.order, provider=Provider.UPI,
            amount=Decimal("100"), status=PaymentStatus.PAID,
        )
        mark_paid(order=self.order)
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, OrderPaymentStatus.PARTIALLY_PAID)
        # Still confirms the order -- the store proceeds to pack on the advance.
        self.assertEqual(self.order.status, OrderStatus.CONFIRMED)

    def test_advance_then_remainder_sums_to_paid(self):
        Payment.objects.create(
            project=self.project, order=self.order, provider=Provider.UPI,
            amount=Decimal("100"), status=PaymentStatus.PAID,
        )
        mark_paid(order=self.order)
        Payment.objects.create(
            project=self.project, order=self.order, provider=Provider.COD,
            amount=Decimal("900"), status=PaymentStatus.PAID,
        )
        mark_paid(order=self.order)
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, OrderPaymentStatus.PAID)


class CodAdvancePlanTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="PlanCo", status="active", currency="INR")

    def test_no_cod_config_means_no_plan(self):
        provider, amount = cod_advance_plan(self.project)
        self.assertIsNone(provider)
        self.assertEqual(amount, Decimal("0"))

    def test_advance_configured_but_no_gateway_enabled_means_no_plan(self):
        PaymentProviderConfig.objects.create(
            project=self.project, provider=Provider.COD, is_enabled=True,
            cod_advance_amount=Decimal("99"),
        )
        provider, amount = cod_advance_plan(self.project)
        self.assertIsNone(provider)
        self.assertEqual(amount, Decimal("0"))

    def test_prefers_a_self_verifying_gateway_over_upi(self):
        # Direct UPI needs a staff member to manually confirm it arrived --
        # picking it over Razorpay would defeat the point of a fast advance.
        PaymentProviderConfig.objects.create(
            project=self.project, provider=Provider.COD, is_enabled=True,
            cod_advance_amount=Decimal("99"),
        )
        PaymentProviderConfig.objects.create(
            project=self.project, provider=Provider.RAZORPAY, is_enabled=True, priority=10,
            credentials={"key_id": "x", "key_secret": "y"},
        )
        PaymentProviderConfig.objects.create(
            project=self.project, provider=Provider.UPI, is_enabled=True, priority=50,
            credentials={"upi_id": "shop@bank"},
        )
        provider, amount = cod_advance_plan(self.project)
        self.assertEqual(provider, Provider.RAZORPAY)
        self.assertEqual(amount, Decimal("99"))

    def test_falls_back_to_upi_when_nothing_else_is_enabled(self):
        PaymentProviderConfig.objects.create(
            project=self.project, provider=Provider.COD, is_enabled=True,
            cod_advance_amount=Decimal("99"),
        )
        PaymentProviderConfig.objects.create(
            project=self.project, provider=Provider.UPI, is_enabled=True,
            credentials={"upi_id": "shop@bank"},
        )
        provider, amount = cod_advance_plan(self.project)
        self.assertEqual(provider, Provider.UPI)
        self.assertEqual(amount, Decimal("99"))


class CodRemainderSpawnTests(TestCase):
    """_settle() on the advance payment must spawn exactly one PENDING COD
    Payment for the leftover amount, and never a second one."""

    def setUp(self):
        self.project = Project.objects.create(name="RemainderCo", status="active", currency="INR")
        self.order = Order.objects.create(
            project=self.project, number="RM-1", email="buyer@t.test",
            subtotal=Decimal("1000"), grand_total=Decimal("1000"), status=OrderStatus.PENDING,
            cod_balance_due=Decimal("900"),
        )
        self.advance = Payment.objects.create(
            project=self.project, order=self.order, provider=Provider.UPI,
            amount=Decimal("100"), status=PaymentStatus.PENDING,
        )

    def test_settling_advance_spawns_cod_remainder(self):
        capture_payment(payment=self.advance)
        self.order.refresh_from_db()
        remainder = Payment.objects.get(order=self.order, provider=Provider.COD)
        self.assertEqual(remainder.amount, Decimal("900"))
        self.assertEqual(remainder.status, PaymentStatus.PENDING)
        self.assertEqual(self.order.payment_status, OrderPaymentStatus.PARTIALLY_PAID)

    def test_settling_advance_twice_does_not_duplicate_remainder(self):
        capture_payment(payment=self.advance)
        self.advance.status = PaymentStatus.PENDING
        self.advance.save(update_fields=["status"])
        capture_payment(payment=self.advance)
        self.assertEqual(Payment.objects.filter(order=self.order, provider=Provider.COD).count(), 1)

    def test_capturing_remainder_completes_payment(self):
        capture_payment(payment=self.advance)
        remainder = Payment.objects.get(order=self.order, provider=Provider.COD)
        capture_payment(payment=remainder)
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, OrderPaymentStatus.PAID)


class InitiatePaymentAmountOverrideTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="InitCo", status="active", currency="INR")
        self.order = Order.objects.create(
            project=self.project, number="IN-1", email="buyer@t.test",
            subtotal=Decimal("1000"), grand_total=Decimal("1000"), status=OrderStatus.PENDING,
        )
        PaymentProviderConfig.objects.create(
            project=self.project, provider=Provider.UPI, is_enabled=True,
            credentials={"upi_id": "shop@bank"},
        )

    def test_record_offline_payment_amount_override(self):
        payment = record_offline_payment(
            order=self.order, provider_key=Provider.COD, amount=Decimal("250"),
        )
        self.assertEqual(payment.amount, Decimal("250"))

    def test_record_offline_payment_defaults_to_grand_total(self):
        payment = record_offline_payment(order=self.order, provider_key=Provider.COD)
        self.assertEqual(payment.amount, self.order.grand_total)
