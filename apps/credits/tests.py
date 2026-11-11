"""Store credit: gift-card redemption, loyalty earn (idempotent per order,
mirroring apps.referrals' commission pattern), spend-at-checkout capping,
and release back to balance on cancel."""

from decimal import Decimal

from django.test import TestCase

from apps.customers.models import Customer
from apps.orders.models import Order, OrderItem
from apps.projects.models import Project

from .models import CreditKind, GiftCard, GiftCardStatus, LoyaltyProgram
from .services import (
    CreditError, apply_available_credit, apply_credit_to_order, earn_loyalty_for_payment,
    get_or_create_account, issue_gift_card, redeem_gift_card, release_credit_for_cancelled_order,
)


class GiftCardRedemptionTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="GiftCo", status="active", currency="INR")
        self.customer = Customer.objects.create(project=self.project, email="shop@t.test")

    def test_redeeming_credits_the_account_once(self):
        card = GiftCard.objects.create(project=self.project, code="ABCD1234", value=Decimal("500"))
        credited = redeem_gift_card(project=self.project, customer=self.customer, code="abcd1234")
        self.assertEqual(credited, Decimal("500"))
        account = get_or_create_account(self.project, self.customer)
        self.assertEqual(account.balance, Decimal("500"))
        card.refresh_from_db()
        self.assertEqual(card.status, GiftCardStatus.REDEEMED)
        self.assertEqual(card.redeemed_account_id, account.pk)

    def test_redeeming_twice_fails_the_second_time(self):
        GiftCard.objects.create(project=self.project, code="ONEUSE1", value=Decimal("200"))
        redeem_gift_card(project=self.project, customer=self.customer, code="ONEUSE1")
        with self.assertRaises(CreditError):
            redeem_gift_card(project=self.project, customer=self.customer, code="ONEUSE1")
        account = get_or_create_account(self.project, self.customer)
        self.assertEqual(account.balance, Decimal("200"))

    def test_unknown_code_is_a_clean_error_not_a_crash(self):
        with self.assertRaises(CreditError):
            redeem_gift_card(project=self.project, customer=self.customer, code="NOSUCHCODE")

    def test_expired_card_cannot_be_redeemed(self):
        from django.utils import timezone

        GiftCard.objects.create(
            project=self.project, code="EXPIRED1", value=Decimal("100"),
            expires_at=timezone.now() - timezone.timedelta(days=1),
        )
        with self.assertRaises(CreditError):
            redeem_gift_card(project=self.project, customer=self.customer, code="EXPIRED1")

    def test_issue_gift_card_generates_a_unique_code(self):
        card = issue_gift_card(project=self.project, value=Decimal("300"))
        self.assertTrue(card.code)
        self.assertEqual(card.status, GiftCardStatus.ACTIVE)

    def test_issue_gift_card_emails_the_recipient_when_given(self):
        from apps.notifications.models import Event, NotificationLog

        issue_gift_card(project=self.project, value=Decimal("300"), issued_to_email="friend@t.test")
        log = NotificationLog.objects.filter(project=self.project, event=Event.GIFT_CARD_ISSUED).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.to_address, "friend@t.test")

    def test_issue_gift_card_with_no_email_sends_nothing(self):
        from apps.notifications.models import Event, NotificationLog

        issue_gift_card(project=self.project, value=Decimal("300"))
        self.assertFalse(
            NotificationLog.objects.filter(project=self.project, event=Event.GIFT_CARD_ISSUED).exists()
        )


class LoyaltyEarnTests(TestCase):
    """Mirrors apps.referrals' PAYMENT_SUCCESS-only, idempotent-per-order
    commission pattern exactly, swapped for a credit instead of a payout."""

    def setUp(self):
        self.project = Project.objects.create(name="LoyaltyCo", status="active", currency="INR")
        self.customer = Customer.objects.create(project=self.project, email="buyer@t.test")

    def _order(self, subtotal=Decimal("1000")):
        return Order.objects.create(
            project=self.project, email="buyer@t.test", customer=self.customer,
            subtotal=subtotal, grand_total=subtotal,
        )

    def test_no_program_earns_nothing(self):
        order = self._order()
        from apps.payments.models import Payment

        payment = Payment.objects.create(
            project=self.project, order=order, provider="cod", amount=order.grand_total,
        )
        self.assertIsNone(earn_loyalty_for_payment(payment))
        account = get_or_create_account(self.project, self.customer)
        self.assertEqual(account.balance, Decimal("0"))

    def test_active_program_credits_the_order_subtotal_percentage(self):
        LoyaltyProgram.objects.create(project=self.project, is_active=True, earn_percent=Decimal("5"))
        order = self._order(Decimal("1000"))
        from apps.payments.models import Payment

        payment = Payment.objects.create(
            project=self.project, order=order, provider="cod", amount=order.grand_total,
        )
        earn_loyalty_for_payment(payment)
        account = get_or_create_account(self.project, self.customer)
        self.assertEqual(account.balance, Decimal("50.00"))

    def test_redelivered_payment_success_does_not_double_credit(self):
        LoyaltyProgram.objects.create(project=self.project, is_active=True, earn_percent=Decimal("5"))
        order = self._order(Decimal("1000"))
        from apps.payments.models import Payment

        payment = Payment.objects.create(
            project=self.project, order=order, provider="cod", amount=order.grand_total,
        )
        earn_loyalty_for_payment(payment)
        earn_loyalty_for_payment(payment)
        account = get_or_create_account(self.project, self.customer)
        self.assertEqual(account.balance, Decimal("50.00"))

    def test_inactive_program_earns_nothing(self):
        LoyaltyProgram.objects.create(project=self.project, is_active=False, earn_percent=Decimal("5"))
        order = self._order()
        from apps.payments.models import Payment

        payment = Payment.objects.create(
            project=self.project, order=order, provider="cod", amount=order.grand_total,
        )
        earn_loyalty_for_payment(payment)
        account = get_or_create_account(self.project, self.customer)
        self.assertEqual(account.balance, Decimal("0"))

    def test_via_real_payment_success_event_cod_capture(self):
        """End-to-end through the actual domain-event signal, same as
        referrals' test_commission_created_from_payment_success."""
        from apps.payments.services import record_offline_payment

        LoyaltyProgram.objects.create(project=self.project, is_active=True, earn_percent=Decimal("10"))
        order = self._order(Decimal("1000"))
        with self.captureOnCommitCallbacks(execute=True):
            record_offline_payment(order=order, mark_collected=True)
        account = get_or_create_account(self.project, self.customer)
        self.assertEqual(account.balance, Decimal("100.00"))


class ApplyCreditToOrderTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="SpendCo", status="active", currency="INR")
        self.customer = Customer.objects.create(project=self.project, email="spend@t.test")
        self.account = get_or_create_account(self.project, self.customer)

    def _credit(self, amount):
        GiftCard.objects.create(project=self.project, code=f"CODE{amount}", value=Decimal(amount))
        redeem_gift_card(project=self.project, customer=self.customer, code=f"CODE{amount}")

    def _order(self, subtotal=Decimal("1000")):
        # apply_credit_to_order calls order.recalc_totals(), which sums
        # order.items — give it a real line so that recompute lands back on
        # the same subtotal instead of zeroing it out.
        order = Order.objects.create(
            project=self.project, email="spend@t.test", customer=self.customer,
            subtotal=subtotal, grand_total=subtotal,
        )
        OrderItem.objects.create(
            order=order, product_title="Widget", unit_price=subtotal, quantity=1, line_total=subtotal,
        )
        return order

    def test_applies_up_to_the_requested_amount(self):
        self._credit("300")
        order = self._order(Decimal("1000"))
        applied = apply_credit_to_order(order=order, customer=self.customer, amount=Decimal("200"))
        self.assertEqual(applied, Decimal("200"))
        order.refresh_from_db()
        self.assertEqual(order.credit_applied, Decimal("200"))
        self.assertEqual(order.grand_total, Decimal("800"))
        self.account.refresh_from_db()
        self.assertEqual(self.account.balance, Decimal("100"))

    def test_caps_at_available_balance_not_the_requested_amount(self):
        self._credit("150")
        order = self._order(Decimal("1000"))
        applied = apply_credit_to_order(order=order, customer=self.customer, amount=Decimal("500"))
        self.assertEqual(applied, Decimal("150"))
        self.account.refresh_from_db()
        self.assertEqual(self.account.balance, Decimal("0"))

    def test_caps_at_order_total_never_goes_negative(self):
        self._credit("2000")
        order = self._order(Decimal("300"))
        applied = apply_credit_to_order(order=order, customer=self.customer, amount=Decimal("2000"))
        self.assertEqual(applied, Decimal("300"))
        order.refresh_from_db()
        self.assertEqual(order.grand_total, Decimal("0"))
        self.account.refresh_from_db()
        self.assertEqual(self.account.balance, Decimal("1700"))

    def test_zero_balance_raises_cleanly(self):
        order = self._order(Decimal("1000"))
        with self.assertRaises(CreditError):
            apply_credit_to_order(order=order, customer=self.customer, amount=Decimal("100"))

    def test_apply_available_credit_uses_the_whole_balance(self):
        self._credit("250")
        order = self._order(Decimal("1000"))
        applied = apply_available_credit(order=order, customer=self.customer)
        self.assertEqual(applied, Decimal("250"))

    def test_apply_available_credit_with_nothing_available_raises_cleanly(self):
        order = self._order(Decimal("1000"))
        with self.assertRaises(CreditError):
            apply_available_credit(order=order, customer=self.customer)

    def test_coupon_discount_and_credit_both_survive_on_the_same_order(self):
        from apps.coupons.models import Coupon, DiscountType
        from apps.coupons.services import apply_to_order

        self._credit("100")
        order = self._order(Decimal("1000"))
        Coupon.objects.create(
            project=self.project, code="SAVE10", discount_type=DiscountType.PERCENT, value=Decimal("10"),
        )
        apply_to_order(order=order, code="SAVE10")
        order.refresh_from_db()
        apply_credit_to_order(order=order, customer=self.customer, amount=Decimal("100"))
        order.refresh_from_db()
        self.assertEqual(order.discount_total, Decimal("100"))  # 10% of 1000
        self.assertEqual(order.credit_applied, Decimal("100"))
        self.assertEqual(order.grand_total, Decimal("800"))  # 1000 - 100 - 100


class ReleaseCreditOnCancelTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="CancelCo", status="active", currency="INR")
        self.customer = Customer.objects.create(project=self.project, email="cancel@t.test")

    def test_cancelling_an_order_returns_the_spent_credit(self):
        from apps.orders.services import cancel_order

        GiftCard.objects.create(project=self.project, code="BACK100", value=Decimal("100"))
        redeem_gift_card(project=self.project, customer=self.customer, code="BACK100")
        order = Order.objects.create(
            project=self.project, email="cancel@t.test", customer=self.customer,
            subtotal=Decimal("500"), grand_total=Decimal("500"),
        )
        OrderItem.objects.create(
            order=order, product_title="Widget", unit_price=Decimal("500"), quantity=1,
            line_total=Decimal("500"),
        )
        apply_credit_to_order(order=order, customer=self.customer, amount=Decimal("100"))

        cancel_order(order=order)
        order.refresh_from_db()
        self.assertEqual(order.credit_applied, Decimal("0"))
        account = get_or_create_account(self.project, self.customer)
        self.assertEqual(account.balance, Decimal("100"))
        self.assertTrue(
            account.entries.filter(kind=CreditKind.REFUND_REVERSAL, order=order).exists()
        )

    def test_cancelling_an_order_with_no_credit_applied_is_a_no_op(self):
        from apps.orders.services import cancel_order

        order = Order.objects.create(
            project=self.project, email="cancel@t.test", customer=self.customer, subtotal=Decimal("500"),
        )
        order.recalc_totals()
        cancel_order(order=order)  # must not raise
        release_credit_for_cancelled_order(order)  # must not raise either, direct call
