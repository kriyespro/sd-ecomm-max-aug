"""Courier integration — Shiprocket/Delhivery adapters + the plumbing that
wires a store's own CourierConfig into shipment creation.

Covers two bugs fixed alongside the new adapters (create_shipment() used to
call get_courier_class(carrier)() with zero args, so a real courier's
self.config was always {}; and a courier API failure raised uncaught inside
the shipment-creation @transaction.atomic block, which would have rolled
back the just-created Shipment/ShipmentItem rows). Both are proven here by
forcing a real (test-mode-off) courier call to fail and asserting the
shipment still exists afterwards.
"""

from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

from apps.orders.models import Order, OrderItem
from apps.projects.models import Project
from apps.shipping import services as ship
from apps.shipping.couriers import get_courier_class
from apps.shipping.couriers.base import CourierError
from apps.shipping.couriers.delhivery import DelhiveryCourier
from apps.shipping.couriers.shiprocket import ShiprocketCourier
from apps.shipping.models import Courier, CourierConfig, Shipment, ShipmentStatus


class RegistryTests(TestCase):
    def test_shiprocket_and_delhivery_registered(self):
        self.assertIs(get_courier_class("shiprocket"), ShiprocketCourier)
        self.assertIs(get_courier_class("delhivery"), DelhiveryCourier)

    def test_unknown_key_falls_back_to_manual(self):
        from apps.shipping.couriers.manual import ManualCourier
        self.assertIs(get_courier_class("nope"), ManualCourier)


class CourierConfigModelTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="CourierCo", status="active", currency="INR")

    def test_defaults_to_test_mode(self):
        cfg = CourierConfig.objects.create(project=self.project, courier=Courier.SHIPROCKET)
        self.assertTrue(cfg.is_test_mode)
        self.assertFalse(cfg.is_enabled)
        self.assertEqual(cfg.credentials, {})

    def test_unique_per_project_and_courier(self):
        CourierConfig.objects.create(project=self.project, courier=Courier.SHIPROCKET)
        with self.assertRaises(Exception):
            CourierConfig.objects.create(project=self.project, courier=Courier.SHIPROCKET)


class _TestModeBehaviorTests(TestCase):
    """No CourierConfig row and no credentials must both mean test mode —
    the adapters have to be safe to exercise before the site owner has
    entered any real key."""

    def setUp(self):
        self.project = Project.objects.create(name="CourierCo2", status="active", currency="INR")

    def test_shiprocket_no_config_returns_synthetic_awb(self):
        courier = ShiprocketCourier(None)
        result = courier.create_shipment(shipment=None)
        self.assertTrue(result["tracking_number"].startswith("SRTEST"))

    def test_shiprocket_config_without_creds_is_test_mode(self):
        cfg = CourierConfig.objects.create(
            project=self.project, courier=Courier.SHIPROCKET, is_test_mode=False,
        )
        courier = ShiprocketCourier(cfg)
        self.assertTrue(courier._test_mode())  # no email/password -> forced test mode

    def test_delhivery_no_config_returns_synthetic_waybill(self):
        courier = DelhiveryCourier(None)
        result = courier.create_shipment(shipment=None)
        self.assertTrue(result["tracking_number"].startswith("DLTEST"))

    def test_delhivery_config_without_token_is_test_mode(self):
        cfg = CourierConfig.objects.create(
            project=self.project, courier=Courier.DELHIVERY, is_test_mode=False,
        )
        courier = DelhiveryCourier(cfg)
        self.assertTrue(courier._test_mode())


class CreateShipmentWiringTests(TestCase):
    """apps.shipping.services.create_shipment() must (1) pass the store's
    CourierConfig into the courier, and (2) survive a courier API failure
    without losing the Shipment it already created."""

    def setUp(self):
        self.project = Project.objects.create(name="CourierCo3", status="active", currency="INR")
        self.order = Order.objects.create(
            project=self.project, number="CC3-1", email="buyer@t.test",
            subtotal=Decimal("500"), grand_total=Decimal("500"),
        )
        OrderItem.objects.create(
            order=self.order, product_title="Widget", sku="W-1",
            unit_price=Decimal("500"), quantity=1, line_total=Decimal("500"),
        )

    def test_manual_carrier_unaffected(self):
        shipment = ship.create_shipment(order=self.order, carrier="manual")
        self.assertEqual(shipment.status, ShipmentStatus.PENDING)

    def test_shiprocket_test_mode_books_synthetic_awb_without_config_row(self):
        # No CourierConfig row exists at all yet (owner hasn't added keys) —
        # must not crash, and must fall back to test-mode behavior.
        shipment = ship.create_shipment(order=self.order, carrier="shiprocket")
        self.assertTrue(shipment.tracking_number.startswith("SRTEST"))
        self.assertEqual(shipment.status, ShipmentStatus.LABEL_CREATED)

    def test_courier_config_is_actually_passed_to_the_courier(self):
        """Regression: create_shipment() used to instantiate the courier with
        zero args (get_courier_class(carrier)()), so self.config was always
        {} even with a real CourierConfig row saved. Prove the row's own
        credentials reach the live-mode request by capturing the outbound
        login call's body."""
        CourierConfig.objects.create(
            project=self.project, courier=Courier.SHIPROCKET, is_test_mode=False,
            credentials={"email": "owner@shop.test", "password": "secret-pw"},
        )
        seen = {}

        def fake_post(path, body, *, token=None, timeout=15):
            seen.setdefault(path, body)
            if path == "/auth/login":
                return {"token": "tok"}
            if path == "/orders/create/adhoc":
                return {"shipment_id": 999}
            if path == "/courier/assign/awb":
                return {"response": {"data": {"awb_code": "SR999"}}}
            raise AssertionError(f"unexpected path {path}")

        with patch("apps.shipping.couriers.shiprocket._post", side_effect=fake_post):
            shipment = ship.create_shipment(order=self.order, carrier="shiprocket")

        self.assertEqual(seen["/auth/login"], {"email": "owner@shop.test", "password": "secret-pw"})
        self.assertEqual(shipment.tracking_number, "SR999")

    def test_live_courier_failure_does_not_roll_back_the_shipment(self):
        """Regression: create_shipment() used to run the live API call with
        no try/except inside @transaction.atomic — a CourierError would
        have wiped the Shipment/ShipmentItem rows it had just created."""
        CourierConfig.objects.create(
            project=self.project, courier=Courier.SHIPROCKET, is_test_mode=False,
            credentials={"email": "x@t.test", "password": "pw"},
        )
        with patch.object(ShiprocketCourier, "create_shipment", side_effect=CourierError("boom")):
            shipment = ship.create_shipment(order=self.order, carrier="shiprocket")

        self.assertTrue(Shipment.objects.filter(pk=shipment.pk).exists())
        shipment.refresh_from_db()
        self.assertIn("boom", shipment.notes)
        self.assertEqual(shipment.status, ShipmentStatus.PENDING)
