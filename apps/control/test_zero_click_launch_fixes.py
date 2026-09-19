"""Two fixes from aug19.md's "Deep-think: minimal-click store creation"
plan (2026-08-19):

1. A brand-new store had zero ShippingZone/ShippingMethod rows, and
   order_detail.jinja hides the "Set shipping & ship" button entirely
   when none match -- a first-time owner's first order had no button to
   click at all. apps.shipping.services.ensure_default_shipping seeds a
   zero-cost, self-ship ("All India", blank carrier) default alongside
   the existing demo-content seed, so the button renders out of the box.
2. The owner checklist's "Connect a payment method" item read every new
   store as incomplete even though COD (a virtual default -- never a
   real PaymentProviderConfig row until an owner explicitly adds one)
   already lets it take orders with zero setup. quick_launch.owner_steps
   now reads apps.payments.services.enabled_provider_configs() instead
   of a raw PaymentProviderConfig query."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.accounts.models import Membership, StoreRole
from apps.catalog.models import Product
from apps.control import quick_launch
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.control.starter_content import seed_starter_content
from apps.payments.models import PaymentProviderConfig, Provider
from apps.projects.models import Project
from apps.shipping.models import ShippingMethod, ShippingZone
from apps.shipping.services import ensure_default_shipping

User = get_user_model()


class EnsureDefaultShippingTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="ShipCo", status="active")

    def test_seeds_a_zone_and_method_for_a_bare_store(self):
        created = ensure_default_shipping(self.project)
        self.assertTrue(created)
        zone = ShippingZone.objects.get(project=self.project)
        self.assertEqual(zone.name, "All India")
        self.assertEqual(zone.countries, [])  # matches every address
        method = ShippingMethod.objects.get(zone=zone)
        self.assertEqual(method.carrier, "")  # self-ship, no courier account
        self.assertEqual(method.base_rate, Decimal("0"))

    def test_noop_when_a_zone_already_exists(self):
        ShippingZone.objects.create(project=self.project, name="Custom Zone")
        created = ensure_default_shipping(self.project)
        self.assertFalse(created)
        self.assertEqual(ShippingZone.objects.filter(project=self.project).count(), 1)
        self.assertEqual(ShippingZone.objects.get().name, "Custom Zone")

    def test_idempotent_across_repeated_calls(self):
        ensure_default_shipping(self.project)
        ensure_default_shipping(self.project)
        self.assertEqual(ShippingZone.objects.filter(project=self.project).count(), 1)

    def test_seeded_zone_matches_a_real_address(self):
        ensure_default_shipping(self.project)
        zone = ShippingZone.objects.get(project=self.project)
        self.assertTrue(zone.matches({"country": "IN", "state": "Maharashtra", "postal_code": "411001"}))


class SeedStarterContentIncludesShippingTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="DemoShipCo", status="active")

    def test_seeding_demo_content_also_seeds_shipping(self):
        seed_starter_content(self.project)
        self.assertTrue(ShippingZone.objects.filter(project=self.project).exists())
        self.assertTrue(ShippingMethod.objects.filter(project=self.project).exists())

    def test_removing_demo_content_does_not_remove_shipping(self):
        from apps.control.starter_content import remove_starter_content

        seed_starter_content(self.project)
        remove_starter_content(self.project)
        self.assertTrue(ShippingZone.objects.filter(project=self.project).exists())


@override_settings(ALLOWED_HOSTS=["*"])
class FirstOrderShipButtonRendersTests(TestCase):
    """End-to-end confirmation: a fresh store, seeded the same way real
    signup/store-creation seeds it, shows the ship button on a real order
    instead of the old "No method matches this address" dead end."""

    def setUp(self):
        self.project = Project.objects.create(
            name="FreshOrderCo", status="active", feature_flags={"onboarded": True},
        )
        seed_starter_content(self.project)
        self.owner = User.objects.create_user("fo", "fo@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.project, user=self.owner, role=StoreRole.OWNER)

    def test_ship_button_renders_for_a_confirmed_order(self):
        from apps.orders.models import Order

        product = Product.objects.filter(project=self.project).first()
        order = Order.objects.create(
            project=self.project, number="FO-1", email="buyer@t.test",
            status="confirmed", grand_total=Decimal("500"),
            shipping_address={"country": "IN", "state": "Maharashtra", "postal_code": "411001"},
        )
        self.client.force_login(self.owner)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()
        body = self.client.get(f"/admin/orders/{order.pk}/").content.decode()
        self.assertIn("Set shipping &amp; ship", body)
        self.assertNotIn("No method matches this address", body)


class OwnerChecklistPaymentCheckTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="ChecklistCo", status="active")

    def _payment_step(self):
        steps = quick_launch.owner_steps(self.project)
        return next(s for s in steps if s["label"] == "Connect a payment method")

    def test_done_on_a_bare_store_via_cod_default(self):
        self.assertTrue(self._payment_step()["done"])

    def test_done_when_cod_explicitly_enabled(self):
        PaymentProviderConfig.objects.create(
            project=self.project, provider=Provider.COD, is_enabled=True,
        )
        self.assertTrue(self._payment_step()["done"])

    def test_not_done_when_cod_explicitly_disabled_and_nothing_else_enabled(self):
        PaymentProviderConfig.objects.create(
            project=self.project, provider=Provider.COD, is_enabled=False,
        )
        self.assertFalse(self._payment_step()["done"])

    def test_done_when_a_real_gateway_is_enabled(self):
        PaymentProviderConfig.objects.create(
            project=self.project, provider=Provider.RAZORPAY, is_enabled=True,
        )
        self.assertTrue(self._payment_step()["done"])
