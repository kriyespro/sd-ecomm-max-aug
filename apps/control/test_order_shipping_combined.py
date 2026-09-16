"""Order fulfillment: "set shipping method" and "create shipment" were two
separate form submits for what's almost always one sequential decision on
a ready-to-fulfill order. One combined action for the common case (no
shipment yet, order ready) — the standalone two-step path stays available
for a second/partial shipment."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.accounts.models import Membership
from apps.billing import services as billing_svc
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.orders.models import Order, OrderItem
from apps.projects.models import Project
from apps.shipping.models import Shipment, ShippingMethod, ShippingZone

User = get_user_model()


@override_settings(ALLOWED_HOSTS=["*"])
class OrderSetShippingAndShipTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="ShipCo", status="active", feature_flags={"onboarded": True},
        )
        billing_svc.ensure_subscription(self.project)
        self.owner = User.objects.create_user("owner", "owner@t.test", "pw", is_staff=True)
        Membership.objects.create(user=self.owner, project=self.project, role="owner")
        self.client.force_login(self.owner)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()

        self.zone = ShippingZone.objects.create(project=self.project, name="Everywhere")
        self.method = ShippingMethod.objects.create(
            project=self.project, zone=self.zone, name="Standard",
            base_rate=Decimal("50"),
        )
        self.order = Order.objects.create(
            project=self.project, number="SC-1", email="buyer@t.test",
            subtotal=Decimal("500"), grand_total=Decimal("500"),
            status="confirmed",
            shipping_address={"country": "IN", "postal_code": "560001"},
        )
        OrderItem.objects.create(
            order=self.order, product_title="Widget", sku="W-1",
            unit_price=Decimal("500"), quantity=1, line_total=Decimal("500"),
        )

    def test_one_submit_sets_shipping_and_creates_the_shipment(self):
        resp = self.client.post(
            f"/admin/orders/{self.order.pk}/shipping/set-and-ship/",
            {"method": self.method.pk}, follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.shipping_method.get("id"), self.method.pk)
        self.assertTrue(Shipment.objects.filter(order=self.order).exists())

    def test_manual_tracking_number_is_used(self):
        self.client.post(
            f"/admin/orders/{self.order.pk}/shipping/set-and-ship/",
            {"method": self.method.pk, "tracking_number": "TRACK123"},
        )
        shipment = Shipment.objects.get(order=self.order)
        self.assertEqual(shipment.tracking_number, "TRACK123")

    def test_order_detail_shows_combined_form_when_no_shipment_yet(self):
        resp = self.client.get(f"/admin/orders/{self.order.pk}/")
        self.assertContains(resp, "Set shipping &amp; ship")
        self.assertContains(resp, "/shipping/set-and-ship/")

    def test_order_detail_falls_back_to_two_step_once_a_shipment_exists(self):
        Shipment.objects.create(project=self.project, order=self.order, carrier="manual")
        resp = self.client.get(f"/admin/orders/{self.order.pk}/")
        self.assertContains(resp, "Set shipping method")
        self.assertNotContains(resp, "/shipping/set-and-ship/")

    def test_invalid_method_from_another_project_is_rejected(self):
        other = Project.objects.create(name="OtherCo", status="active")
        billing_svc.ensure_subscription(other)
        other_zone = ShippingZone.objects.create(project=other, name="Z")
        other_method = ShippingMethod.objects.create(
            project=other, zone=other_zone, name="M", base_rate=Decimal("10"),
        )
        resp = self.client.post(
            f"/admin/orders/{self.order.pk}/shipping/set-and-ship/",
            {"method": other_method.pk},
        )
        self.assertEqual(resp.status_code, 404)
