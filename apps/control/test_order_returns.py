"""Orders -> order detail: approve/reject a customer's return/exchange
request. Service-level refund-method logic is covered by apps.returns.tests."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import Membership, StoreRole
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.credits.services import get_or_create_account
from apps.customers.models import Customer
from apps.orders.models import Order, OrderStatusEvent
from apps.projects.models import Project
from apps.returns.models import RequestStatus, ReturnRequest

User = get_user_model()


@override_settings(ALLOWED_HOSTS=["*"])
class ReturnRequestAdminTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="ReturnAdminCo", status="active", feature_flags={"onboarded": True},
        )
        self.owner = User.objects.create_user("rao", "rao@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.project, user=self.owner, role=StoreRole.OWNER)
        self.client.force_login(self.owner)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()

        self.customer = Customer.objects.create(project=self.project, email="buyer@t.test")
        self.order = Order.objects.create(
            project=self.project, number="ORD-RET-1", email="buyer@t.test", customer=self.customer,
            status="delivered", subtotal=Decimal("1000"), grand_total=Decimal("1000"),
        )
        event = OrderStatusEvent.objects.create(
            order=self.order, kind=OrderStatusEvent.Kind.STATUS, to_value="delivered",
        )
        OrderStatusEvent.objects.filter(pk=event.pk).update(created_at=timezone.now() - timezone.timedelta(days=1))
        self.req = ReturnRequest.objects.create(
            project=self.project, order=self.order, customer=self.customer, reason="wrong size",
        )

    def test_pending_request_renders_with_approve_reject(self):
        resp = self.client.get(f"/admin/orders/{self.order.pk}/")
        body = resp.content.decode()
        self.assertIn(f"/admin/orders/{self.order.pk}/return/approve/", body)
        self.assertIn(f"/admin/orders/{self.order.pk}/return/reject/", body)
        self.assertIn("wrong size", body)

    def test_approve_as_store_credit(self):
        resp = self.client.post(f"/admin/orders/{self.order.pk}/return/approve/", {
            "refund_method": "store_credit", "amount": "500",
        })
        self.assertRedirects(resp, f"/admin/orders/{self.order.pk}/")
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, RequestStatus.APPROVED)
        account = get_or_create_account(self.project, self.customer)
        self.assertEqual(account.balance, Decimal("500"))

    def test_approve_requires_a_refund_method(self):
        resp = self.client.post(f"/admin/orders/{self.order.pk}/return/approve/", {})
        self.assertRedirects(resp, f"/admin/orders/{self.order.pk}/")
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, RequestStatus.REQUESTED)

    def test_reject(self):
        resp = self.client.post(f"/admin/orders/{self.order.pk}/return/reject/", {"note": "used item"})
        self.assertRedirects(resp, f"/admin/orders/{self.order.pk}/")
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, RequestStatus.REJECTED)
        self.assertEqual(self.req.resolved_note, "used item")

    def test_staff_can_also_resolve_returns(self):
        staff = User.objects.create_user("ras", "ras@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.project, user=staff, role=StoreRole.STAFF)
        self.client.force_login(staff)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()
        resp = self.client.post(f"/admin/orders/{self.order.pk}/return/reject/", {"note": "no"})
        self.assertRedirects(resp, f"/admin/orders/{self.order.pk}/")
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, RequestStatus.REJECTED)
