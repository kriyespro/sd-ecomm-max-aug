"""Model + service tests for the per-store referral program -- the pieces
below the HTTP layer (apps.control.test_referral_program and
apps.shopfront.test_referrals cover the request/response side)."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.catalog.models import Product
from apps.projects.models import Project

from .models import (
    CommissionStatus,
    CommissionType,
    ReferralAttribution,
    ReferralClick,
    ReferralCommission,
    ReferralProgram,
    Referrer,
    ReferrerStatus,
)
from .services import (
    active_program,
    attribute_order,
    become_referrer,
    create_commission_for_payment,
    get_or_create_program,
    record_click,
    referrer_for,
    resolve_active_referrer,
    set_commission_status,
    set_referrer_status,
)

User = get_user_model()


class ReferralProgramModelTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="ProgCo", status="active")

    def test_percent_commission(self):
        program = ReferralProgram.objects.create(
            project=self.project, commission_type=CommissionType.PERCENT,
            commission_value=Decimal("10"),
        )
        self.assertEqual(program.commission_for(Decimal("500")), Decimal("50.00"))

    def test_fixed_commission_ignores_order_amount(self):
        program = ReferralProgram.objects.create(
            project=self.project, commission_type=CommissionType.FIXED,
            commission_value=Decimal("75"),
        )
        self.assertEqual(program.commission_for(Decimal("10000")), Decimal("75"))

    def test_get_or_create_is_idempotent_per_project(self):
        p1 = get_or_create_program(self.project)
        p2 = get_or_create_program(self.project)
        self.assertEqual(p1.pk, p2.pk)

    def test_active_program_requires_is_active(self):
        get_or_create_program(self.project)
        self.assertIsNone(active_program(self.project))
        ReferralProgram.objects.filter(project=self.project).update(is_active=True)
        self.assertIsNotNone(active_program(self.project))


class ReferrerModelTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="RefCo", status="active")
        self.user = User.objects.create_user("shopper", "shopper@t.test", "pw")

    def test_code_auto_generated_and_unique(self):
        r = become_referrer(self.project, self.user)
        self.assertEqual(len(r.code), 8)
        u2 = User.objects.create_user("shopper2", "shopper2@t.test", "pw")
        r2 = become_referrer(self.project, u2)
        self.assertNotEqual(r.code, r2.code)

    def test_become_referrer_is_idempotent(self):
        r1 = become_referrer(self.project, self.user)
        r2 = become_referrer(self.project, self.user)
        self.assertEqual(r1.pk, r2.pk)

    def test_referrer_for_returns_none_when_not_joined(self):
        self.assertIsNone(referrer_for(self.project, self.user))
        become_referrer(self.project, self.user)
        self.assertIsNotNone(referrer_for(self.project, self.user))

    def test_disabled_referrer_not_active(self):
        r = become_referrer(self.project, self.user)
        set_referrer_status(r, False)
        r.refresh_from_db()
        self.assertEqual(r.status, ReferrerStatus.DISABLED)
        self.assertFalse(r.is_active)


class ResolveAndClickTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="ClickCo", status="active")
        self.user = User.objects.create_user("ref1", "ref1@t.test", "pw")
        self.referrer = become_referrer(self.project, self.user)

    def test_no_program_no_resolve(self):
        self.assertIsNone(resolve_active_referrer(self.project, self.referrer.code))

    def test_inactive_program_no_resolve(self):
        ReferralProgram.objects.create(project=self.project, is_active=False)
        self.assertIsNone(resolve_active_referrer(self.project, self.referrer.code))

    def test_active_program_resolves_valid_code(self):
        ReferralProgram.objects.create(project=self.project, is_active=True)
        self.assertEqual(
            resolve_active_referrer(self.project, self.referrer.code).pk, self.referrer.pk,
        )

    def test_unknown_code_does_not_resolve(self):
        ReferralProgram.objects.create(project=self.project, is_active=True)
        self.assertIsNone(resolve_active_referrer(self.project, "NOPE1234"))

    def test_disabled_referrer_does_not_resolve_even_with_active_program(self):
        ReferralProgram.objects.create(project=self.project, is_active=True)
        set_referrer_status(self.referrer, False)
        self.assertIsNone(resolve_active_referrer(self.project, self.referrer.code))

    def test_record_click_creates_a_row_and_returns_referrer(self):
        ReferralProgram.objects.create(project=self.project, is_active=True)
        out = record_click(self.project, self.referrer.code, landing_path="/p/lamp/")
        self.assertEqual(out.pk, self.referrer.pk)
        self.assertEqual(ReferralClick.objects.filter(referrer=self.referrer).count(), 1)

    def test_record_click_no_row_for_a_bad_code(self):
        ReferralProgram.objects.create(project=self.project, is_active=True)
        out = record_click(self.project, "GARBAGE1", landing_path="/")
        self.assertIsNone(out)
        self.assertEqual(ReferralClick.objects.count(), 0)


class AttributionAndCommissionTests(TestCase):
    """The two-step "attribute at checkout, commission only on payment
    success" flow -- the whole point of splitting ReferralAttribution from
    ReferralCommission."""

    def setUp(self):
        self.project = Project.objects.create(name="AttribCo", status="active")
        ReferralProgram.objects.create(
            project=self.project, is_active=True,
            commission_type=CommissionType.PERCENT, commission_value=Decimal("10"),
        )
        self.user = User.objects.create_user("ref2", "ref2@t.test", "pw")
        self.referrer = become_referrer(self.project, self.user)
        self.product = Product.objects.create(
            project=self.project, title="Lamp", price=Decimal("1000"), status="active",
        )

    def _order(self, amount=Decimal("1000")):
        from apps.orders.models import Order

        return Order.objects.create(
            project=self.project, email="buyer@t.test", grand_total=amount,
        )

    def test_attribute_order_creates_attribution_not_commission(self):
        order = self._order()
        attribute_order(order, self.referrer.code)
        self.assertEqual(ReferralAttribution.objects.filter(order=order).count(), 1)
        self.assertEqual(ReferralCommission.objects.filter(order=order).count(), 0)

    def test_attribute_order_no_op_on_bad_code(self):
        order = self._order()
        result = attribute_order(order, "BADCODE1")
        self.assertIsNone(result)
        self.assertFalse(ReferralAttribution.objects.filter(order=order).exists())

    def test_attribute_order_no_op_with_no_code(self):
        order = self._order()
        self.assertIsNone(attribute_order(order, None))
        self.assertIsNone(attribute_order(order, ""))

    def test_attribute_order_is_idempotent(self):
        order = self._order()
        a1 = attribute_order(order, self.referrer.code)
        a2 = attribute_order(order, self.referrer.code)
        self.assertEqual(a1.pk, a2.pk)

    def test_commission_created_from_payment_success(self):
        from apps.payments.services import record_offline_payment

        order = self._order(Decimal("1000"))
        attribute_order(order, self.referrer.code)
        with self.captureOnCommitCallbacks(execute=True):
            payment = record_offline_payment(order=order, mark_collected=True)

        commission = ReferralCommission.objects.get(order=order)
        self.assertEqual(commission.referrer_id, self.referrer.pk)
        self.assertEqual(commission.order_amount, Decimal("1000"))
        self.assertEqual(commission.commission_amount, Decimal("100.00"))
        self.assertEqual(commission.status, CommissionStatus.PENDING)
        self.assertEqual(payment.order_id, order.pk)

    def test_cod_order_creates_no_commission_until_cash_is_captured(self):
        """The critical rule: an unattributed-yet COD order (mark_collected=
        False, the COD default) must not create a commission just because
        the order was placed -- only once the store owner captures it."""
        from apps.payments.services import capture_payment, record_offline_payment

        order = self._order(Decimal("1000"))
        attribute_order(order, self.referrer.code)
        with self.captureOnCommitCallbacks(execute=True):
            payment = record_offline_payment(order=order, mark_collected=False)

        self.assertFalse(ReferralCommission.objects.filter(order=order).exists())

        with self.captureOnCommitCallbacks(execute=True):
            capture_payment(payment=payment)
        self.assertTrue(ReferralCommission.objects.filter(order=order).exists())

    def test_no_attribution_no_commission(self):
        from apps.payments.services import record_offline_payment

        order = self._order()
        with self.captureOnCommitCallbacks(execute=True):
            record_offline_payment(order=order, mark_collected=True)
        self.assertFalse(ReferralCommission.objects.filter(order=order).exists())

    def test_duplicate_payment_success_does_not_double_the_commission(self):
        order = self._order(Decimal("1000"))
        attribute_order(order, self.referrer.code)

        from apps.payments.models import Payment

        payment = Payment.objects.create(
            project=self.project, order=order, provider="cod", amount=Decimal("1000"),
        )
        create_commission_for_payment(payment)
        create_commission_for_payment(payment)
        self.assertEqual(ReferralCommission.objects.filter(order=order).count(), 1)


class CommissionStatusTransitionTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="StatusCo", status="active")
        self.user = User.objects.create_user("ref3", "ref3@t.test", "pw")
        self.referrer = become_referrer(self.project, self.user)

    def _commission(self):
        from apps.orders.models import Order

        order = Order.objects.create(
            project=self.project, email="x@t.test", grand_total=Decimal("500"),
        )
        return ReferralCommission.objects.create(
            project=self.project, referrer=self.referrer, order=order,
            order_amount=Decimal("500"), commission_amount=Decimal("50"),
        )

    def test_pending_to_approved(self):
        c = self._commission()
        set_commission_status(c, CommissionStatus.APPROVED)
        c.refresh_from_db()
        self.assertEqual(c.status, CommissionStatus.APPROVED)
        self.assertIsNotNone(c.approved_at)

    def test_approved_to_paid(self):
        c = self._commission()
        set_commission_status(c, CommissionStatus.APPROVED)
        set_commission_status(c, CommissionStatus.PAID)
        c.refresh_from_db()
        self.assertEqual(c.status, CommissionStatus.PAID)
        self.assertIsNotNone(c.paid_at)

    def test_pending_to_paid_directly_is_rejected(self):
        c = self._commission()
        set_commission_status(c, CommissionStatus.PAID)
        c.refresh_from_db()
        self.assertEqual(c.status, CommissionStatus.PENDING)

    def test_pending_to_rejected(self):
        c = self._commission()
        set_commission_status(c, CommissionStatus.REJECTED)
        c.refresh_from_db()
        self.assertEqual(c.status, CommissionStatus.REJECTED)

    def test_paid_is_terminal(self):
        c = self._commission()
        set_commission_status(c, CommissionStatus.APPROVED)
        set_commission_status(c, CommissionStatus.PAID)
        set_commission_status(c, CommissionStatus.REJECTED)
        c.refresh_from_db()
        self.assertEqual(c.status, CommissionStatus.PAID)
