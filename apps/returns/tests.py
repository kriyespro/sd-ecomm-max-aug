"""Self-serve return/exchange request: window/duplicate guards on request,
and each refund method's behavior on approve (store credit, gateway refund,
manual), plus reject and the EXCHANGE-never-auto-transitions rule."""

from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.credits.models import CreditKind
from apps.credits.services import get_or_create_account
from apps.customers.models import Customer
from apps.orders.models import Order, OrderStatusEvent
from apps.payments.models import Payment, PaymentStatus as PayPaymentStatus, Provider
from apps.projects.models import Project

from .models import RefundMethod, RequestKind, RequestStatus, ReturnRequest
from .services import ReturnError, RETURN_WINDOW_DAYS, approve_return, reject_return, request_return


class ReturnTestBase(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="ReturnsCo", status="active", currency="INR")
        self.customer = Customer.objects.create(project=self.project, email="buyer@t.test")
        self.order = Order.objects.create(
            project=self.project, number="RET-1", email="buyer@t.test", customer=self.customer,
            status="delivered", subtotal=Decimal("1000"), grand_total=Decimal("1000"),
        )

    def _mark_delivered(self, days_ago=1):
        event = OrderStatusEvent.objects.create(
            order=self.order, kind=OrderStatusEvent.Kind.STATUS,
            from_value="shipped", to_value="delivered",
        )
        stamp = timezone.now() - timezone.timedelta(days=days_ago)
        OrderStatusEvent.objects.filter(pk=event.pk).update(created_at=stamp)


class RequestReturnTests(ReturnTestBase):
    def test_no_delivered_event_is_rejected(self):
        with self.assertRaises(ReturnError):
            request_return(order=self.order, customer=self.customer, kind=RequestKind.RETURN, reason="wrong size")

    def test_within_window_succeeds(self):
        self._mark_delivered(days_ago=2)
        req = request_return(order=self.order, customer=self.customer, kind=RequestKind.RETURN, reason="wrong size")
        self.assertEqual(req.status, RequestStatus.REQUESTED)

    def test_outside_window_is_rejected(self):
        self._mark_delivered(days_ago=RETURN_WINDOW_DAYS + 1)
        with self.assertRaises(ReturnError):
            request_return(order=self.order, customer=self.customer, kind=RequestKind.RETURN, reason="wrong size")

    def test_duplicate_request_is_rejected(self):
        self._mark_delivered(days_ago=1)
        request_return(order=self.order, customer=self.customer, kind=RequestKind.RETURN, reason="wrong size")
        with self.assertRaises(ReturnError):
            request_return(order=self.order, customer=self.customer, kind=RequestKind.RETURN, reason="changed mind")
        self.assertEqual(ReturnRequest.objects.filter(order=self.order).count(), 1)

    def test_not_delivered_order_is_rejected(self):
        self.order.status = "shipped"
        self.order.save(update_fields=["status"])
        with self.assertRaises(ReturnError):
            request_return(order=self.order, customer=self.customer, kind=RequestKind.RETURN, reason="wrong size")


class ApproveReturnTests(ReturnTestBase):
    def setUp(self):
        super().setUp()
        self._mark_delivered(days_ago=1)
        self.req = request_return(
            order=self.order, customer=self.customer, kind=RequestKind.RETURN, reason="wrong size",
        )

    def test_store_credit_refund_credits_ledger_and_moves_order_to_returned(self):
        approve_return(request=self.req, refund_method=RefundMethod.STORE_CREDIT, amount=Decimal("500"))
        account = get_or_create_account(self.project, self.customer)
        self.assertEqual(account.balance, Decimal("500"))
        self.assertEqual(account.entries.get().kind, CreditKind.RETURN_REFUND)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "returned")
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, RequestStatus.APPROVED)
        self.assertEqual(self.req.resolved_amount, Decimal("500"))

    def test_store_credit_refund_does_not_touch_any_payment(self):
        approve_return(request=self.req, refund_method=RefundMethod.STORE_CREDIT, amount=Decimal("500"))
        self.assertFalse(Payment.objects.filter(order=self.order).exists())

    def test_gateway_refund_calls_refund_payment_and_order_ends_refunded(self):
        payment = Payment.objects.create(
            project=self.project, order=self.order, provider=Provider.COD,
            amount=Decimal("1000"), status=PayPaymentStatus.PAID,
        )
        approve_return(request=self.req, refund_method=RefundMethod.GATEWAY_REFUND, amount=Decimal("1000"))
        payment.refresh_from_db()
        self.assertEqual(payment.status, PayPaymentStatus.REFUNDED)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "refunded")
        self.assertEqual(self.order.payment_status, "refunded")

    def test_gateway_refund_with_no_settled_payment_raises(self):
        with self.assertRaises(ReturnError):
            approve_return(request=self.req, refund_method=RefundMethod.GATEWAY_REFUND)

    def test_manual_refund_moves_status_with_no_money_movement(self):
        approve_return(request=self.req, refund_method=RefundMethod.MANUAL, amount=Decimal("1000"))
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "returned")
        account = get_or_create_account(self.project, self.customer)
        self.assertEqual(account.balance, Decimal("0"))

    def test_exchange_kind_never_auto_transitions_order_status(self):
        self.req.delete()  # one request per order
        req = ReturnRequest.objects.create(
            project=self.project, order=self.order, customer=self.customer,
            kind=RequestKind.EXCHANGE, reason="wrong color",
        )
        approve_return(request=req, refund_method=RefundMethod.MANUAL)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "delivered")

    def test_already_resolved_request_cannot_be_approved_again(self):
        approve_return(request=self.req, refund_method=RefundMethod.MANUAL)
        with self.assertRaises(ReturnError):
            approve_return(request=self.req, refund_method=RefundMethod.MANUAL)


class RejectReturnTests(ReturnTestBase):
    def setUp(self):
        super().setUp()
        self._mark_delivered(days_ago=1)
        self.req = request_return(
            order=self.order, customer=self.customer, kind=RequestKind.RETURN, reason="wrong size",
        )

    def test_reject_leaves_order_status_untouched(self):
        reject_return(request=self.req, note="Item shows signs of use")
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "delivered")
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, RequestStatus.REJECTED)
        self.assertEqual(self.req.resolved_note, "Item shows signs of use")

    def test_already_resolved_request_cannot_be_rejected_again(self):
        reject_return(request=self.req, note="no")
        with self.assertRaises(ReturnError):
            reject_return(request=self.req, note="no again")
