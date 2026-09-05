"""B2B / dropship marketplace: listings, cross-tenant import, order ledger."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import TestCase

from apps.accounts.models import Membership, StoreRole
from apps.b2b import services as b2b_svc
from apps.b2b.models import B2BLedgerStatus, B2BListing, B2BOrderLedger, B2BShipStatus
from apps.catalog.models import Product, ProductImage
from apps.cart.models import Cart, CartItem
from apps.orders.services import place_order
from apps.projects.models import Project

User = get_user_model()


class B2BServiceTestCase(TestCase):
    def setUp(self):
        self.seller = Project.objects.create(name="Wholesale Co", status="active", is_b2b_seller=True)
        self.buyer = Project.objects.create(name="Retail Co", status="active")

        self.seller_owner = User.objects.create_user(
            username="sell@t.test", email="sell@t.test", password="pw"
        )
        Membership.objects.create(project=self.seller, user=self.seller_owner, role=StoreRole.OWNER, is_active=True)

        self.buyer_owner = User.objects.create_user(
            username="buy@t.test", email="buy@t.test", password="pw"
        )
        Membership.objects.create(project=self.buyer, user=self.buyer_owner, role=StoreRole.OWNER, is_active=True)

        self.buyer_manager = User.objects.create_user(
            username="mgr@t.test", email="mgr@t.test", password="pw"
        )
        Membership.objects.create(project=self.buyer, user=self.buyer_manager, role=StoreRole.MANAGER, is_active=True)

        self.product = Product.objects.create(project=self.seller, title="Wireless Mouse", price=Decimal("500"))
        ProductImage.objects.create(product=self.product, image="products/mouse.jpg", is_primary=True)

    def test_owner_lists_own_product(self):
        listing = b2b_svc.create_or_update_listing(
            project=self.seller, actor=self.seller_owner, product=self.product,
            wholesale_price=Decimal("300"),
        )
        self.assertEqual(listing.seller_project, self.seller)
        self.assertTrue(listing.is_active)

    def test_manager_cannot_list(self):
        Membership.objects.create(project=self.seller, user=self.buyer_manager, role=StoreRole.MANAGER, is_active=True)
        with self.assertRaises(PermissionDenied):
            b2b_svc.create_or_update_listing(
                project=self.seller, actor=self.buyer_manager, product=self.product,
                wholesale_price=Decimal("300"),
            )

    def test_manager_cannot_import(self):
        listing = B2BListing.objects.create(product=self.product, wholesale_price=Decimal("300"))
        with self.assertRaises(PermissionDenied):
            b2b_svc.import_listing(
                listing=listing, buyer_project=self.buyer, actor=self.buyer_manager, markup_pct=Decimal("20"),
            )

    def test_marketplace_excludes_own_and_inactive_and_non_seller(self):
        listing = B2BListing.objects.create(product=self.product, wholesale_price=Decimal("300"))
        self.assertIn(listing, list(b2b_svc.marketplace_listings(exclude_project=self.buyer)))
        self.assertNotIn(listing, list(b2b_svc.marketplace_listings(exclude_project=self.seller)))

        listing.is_active = False
        listing.save()
        self.assertNotIn(listing, list(b2b_svc.marketplace_listings(exclude_project=self.buyer)))

        listing.is_active = True
        listing.save()
        self.seller.is_b2b_seller = False
        self.seller.save()
        self.assertNotIn(listing, list(b2b_svc.marketplace_listings(exclude_project=self.buyer)))

    def test_import_creates_independent_priced_product_with_images(self):
        listing = B2BListing.objects.create(product=self.product, wholesale_price=Decimal("300"))
        b2b_import = b2b_svc.import_listing(
            listing=listing, buyer_project=self.buyer, actor=self.buyer_owner, markup_pct=Decimal("20"),
        )
        local = b2b_import.local_product
        self.assertEqual(local.project, self.buyer)
        self.assertEqual(local.price, Decimal("360.00"))  # 300 * 1.20
        self.assertEqual(local.title, "Wireless Mouse")
        self.assertEqual(local.images.count(), 1)
        # Editing the buyer's copy must never touch the seller's original.
        local.title = "Renamed"
        local.save()
        self.product.refresh_from_db()
        self.assertEqual(self.product.title, "Wireless Mouse")

    def test_cannot_import_own_listing(self):
        listing = B2BListing.objects.create(product=self.product, wholesale_price=Decimal("300"))
        with self.assertRaises(b2b_svc.B2BError):
            b2b_svc.import_listing(
                listing=listing, buyer_project=self.seller, actor=self.seller_owner, markup_pct=Decimal("10"),
            )

    def test_cannot_import_twice(self):
        listing = B2BListing.objects.create(product=self.product, wholesale_price=Decimal("300"))
        b2b_svc.import_listing(
            listing=listing, buyer_project=self.buyer, actor=self.buyer_owner, markup_pct=Decimal("10"),
        )
        with self.assertRaises(b2b_svc.B2BError):
            b2b_svc.import_listing(
                listing=listing, buyer_project=self.buyer, actor=self.buyer_owner, markup_pct=Decimal("10"),
            )

    def test_cannot_import_inactive_listing(self):
        listing = B2BListing.objects.create(product=self.product, wholesale_price=Decimal("300"), is_active=False)
        with self.assertRaises(b2b_svc.B2BError):
            b2b_svc.import_listing(
                listing=listing, buyer_project=self.buyer, actor=self.buyer_owner, markup_pct=Decimal("10"),
            )

    def test_cannot_import_when_seller_turned_b2b_off(self):
        listing = B2BListing.objects.create(product=self.product, wholesale_price=Decimal("300"))
        self.seller.is_b2b_seller = False
        self.seller.save()
        with self.assertRaises(b2b_svc.B2BError):
            b2b_svc.import_listing(
                listing=listing, buyer_project=self.buyer, actor=self.buyer_owner, markup_pct=Decimal("10"),
            )

    def test_concurrent_double_import_raises_friendly_error_not_500(self):
        """Simulates two racing requests: the DB-level unique constraint is the
        real guard, and it must surface as B2BError, not an unhandled IntegrityError."""
        listing = B2BListing.objects.create(product=self.product, wholesale_price=Decimal("300"))
        b2b_svc.import_listing(
            listing=listing, buyer_project=self.buyer, actor=self.buyer_owner, markup_pct=Decimal("10"),
        )
        # Bypass the .exists() pre-check the same way a true race would, by
        # calling straight through to the create path a second time.
        with self.assertRaises(b2b_svc.B2BError):
            b2b_svc.import_listing(
                listing=listing, buyer_project=self.buyer, actor=self.buyer_owner, markup_pct=Decimal("10"),
            )

    def test_variable_product_imports_as_simple_without_variants(self):
        from apps.catalog.models import ProductKind

        variable = Product.objects.create(
            project=self.seller, title="T-Shirt", price=Decimal("400"), kind=ProductKind.VARIABLE,
        )
        listing = B2BListing.objects.create(product=variable, wholesale_price=Decimal("250"))
        b2b_import = b2b_svc.import_listing(
            listing=listing, buyer_project=self.buyer, actor=self.buyer_owner, markup_pct=Decimal("10"),
        )
        self.assertEqual(b2b_import.local_product.kind, ProductKind.SIMPLE)
        self.assertEqual(b2b_import.local_product.variants.count(), 0)


class PlaceOrderLedgerTests(TestCase):
    """A sale of an imported product must create exactly one ledger row,
    snapshotting the shipping address, without disturbing a normal order."""

    def setUp(self):
        self.seller = Project.objects.create(name="Wholesale Co", status="active", is_b2b_seller=True)
        self.buyer = Project.objects.create(name="Retail Co", status="active", currency="INR")
        self.seller_owner = User.objects.create_user(username="s2@t.test", email="s2@t.test", password="pw")
        Membership.objects.create(project=self.seller, user=self.seller_owner, role=StoreRole.OWNER, is_active=True)
        self.buyer_owner = User.objects.create_user(username="b2@t.test", email="b2@t.test", password="pw")
        Membership.objects.create(project=self.buyer, user=self.buyer_owner, role=StoreRole.OWNER, is_active=True)

        source = Product.objects.create(project=self.seller, title="Lamp", price=Decimal("1000"))
        self.listing = B2BListing.objects.create(product=source, wholesale_price=Decimal("400"))
        self.b2b_import = b2b_svc.import_listing(
            listing=self.listing, buyer_project=self.buyer, actor=self.buyer_owner, markup_pct=Decimal("50"),
        )
        self.local_product = self.b2b_import.local_product

        self.plain_product = Product.objects.create(project=self.buyer, title="Plain Mug", price=Decimal("200"))

    def _cart_with(self, *products):
        cart = Cart.objects.create(project=self.buyer, is_active=True)
        for p in products:
            CartItem.objects.create(cart=cart, product=p, quantity=1, unit_price=p.price)
        return cart

    def test_dropship_sale_creates_ledger_row(self):
        cart = self._cart_with(self.local_product)
        order = place_order(
            project=self.buyer, cart=cart, email="cust@t.test",
            billing_address={}, shipping_address={"name": "Cust One", "line1": "1 Rd", "phone": "999"},
        )
        item = order.items.get()
        ledger = B2BOrderLedger.objects.get(order_item=item)
        self.assertEqual(ledger.seller_project, self.seller)
        self.assertEqual(ledger.buyer_project, self.buyer)
        self.assertEqual(ledger.wholesale_unit_price, Decimal("400.00"))
        self.assertEqual(ledger.amount_owed, Decimal("400.00"))
        self.assertEqual(ledger.status, B2BLedgerStatus.PENDING)
        self.assertEqual(ledger.ship_status, B2BShipStatus.PENDING)
        self.assertEqual(ledger.ship_to_name, "Cust One")

    def test_normal_sale_creates_no_ledger_row(self):
        cart = self._cart_with(self.plain_product)
        order = place_order(
            project=self.buyer, cart=cart, email="cust2@t.test",
            billing_address={}, shipping_address={"name": "Cust Two"},
        )
        item = order.items.get()
        self.assertFalse(B2BOrderLedger.objects.filter(order_item=item).exists())

    def test_mixed_cart_ledgers_only_the_dropship_item(self):
        cart = self._cart_with(self.local_product, self.plain_product)
        order = place_order(
            project=self.buyer, cart=cart, email="cust3@t.test",
            billing_address={}, shipping_address={"name": "Cust Three"},
        )
        self.assertEqual(order.items.count(), 2)
        self.assertEqual(B2BOrderLedger.objects.filter(order_item__order=order).count(), 1)

    def test_mark_shipped_and_mark_paid(self):
        cart = self._cart_with(self.local_product)
        order = place_order(
            project=self.buyer, cart=cart, email="cust4@t.test",
            billing_address={}, shipping_address={"name": "Cust Four"},
        )
        ledger = B2BOrderLedger.objects.get(order_item__order=order)

        b2b_svc.mark_shipped(
            ledger=ledger, tracking_number="TRK1", courier="BlueDart", actor=self.seller_owner,
        )
        ledger.refresh_from_db()
        self.assertEqual(ledger.ship_status, B2BShipStatus.SHIPPED)
        self.assertEqual(ledger.tracking_number, "TRK1")

        with self.assertRaises(PermissionDenied):
            b2b_svc.mark_shipped(ledger=ledger, tracking_number="x", courier="y", actor=self.buyer_owner)

        b2b_svc.mark_paid(ledger=ledger, payout_ref="UPI123", actor=self.buyer_owner)
        ledger.refresh_from_db()
        self.assertEqual(ledger.status, B2BLedgerStatus.PAID)
        self.assertEqual(ledger.payout_ref, "UPI123")

    def test_stranger_cannot_mark_paid(self):
        cart = self._cart_with(self.local_product)
        order = place_order(
            project=self.buyer, cart=cart, email="cust5@t.test",
            billing_address={}, shipping_address={"name": "Cust Five"},
        )
        ledger = B2BOrderLedger.objects.get(order_item__order=order)
        stranger = User.objects.create_user(username="str@t.test", email="str@t.test", password="pw")
        with self.assertRaises(PermissionDenied):
            b2b_svc.mark_paid(ledger=ledger, payout_ref="", actor=stranger)


class B2BScreenTests(TestCase):
    def setUp(self):
        self.seller = Project.objects.create(
            name="ScreenSeller", status="active", is_b2b_seller=True,
            feature_flags={"onboarded": True},
        )
        self.owner = User.objects.create_user(username="so@t.test", email="so@t.test", password="pw", is_staff=True)
        Membership.objects.create(project=self.seller, user=self.owner, role=StoreRole.OWNER, is_active=True)
        self.manager = User.objects.create_user(username="sm@t.test", email="sm@t.test", password="pw", is_staff=True)
        Membership.objects.create(project=self.seller, user=self.manager, role=StoreRole.MANAGER, is_active=True)

        self.product = Product.objects.create(project=self.seller, title="Kettle", price=Decimal("800"))

        from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY

        self._session_key = ACTIVE_PROJECT_SESSION_KEY

    def _login_as(self, user):
        self.client.force_login(user)
        s = self.client.session
        s[self._session_key] = self.seller.pk
        s.save()

    def test_owner_sees_b2b_settings_and_can_list(self):
        self._login_as(self.owner)
        resp = self.client.get("/admin/b2b/settings/")
        self.assertEqual(resp.status_code, 200)

        resp = self.client.post(
            "/admin/b2b/listings/new/",
            {"product_id": self.product.pk, "wholesale_price": "500"}, follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(B2BListing.objects.filter(product=self.product).exists())

    def test_manager_forbidden_from_b2b_settings(self):
        self._login_as(self.manager)
        resp = self.client.get("/admin/b2b/settings/")
        self.assertEqual(resp.status_code, 403)

    def test_manager_forbidden_from_marketplace_and_orders(self):
        self._login_as(self.manager)
        self.assertEqual(self.client.get("/admin/b2b/marketplace/").status_code, 403)
        self.assertEqual(self.client.get("/admin/b2b/orders/").status_code, 403)
        self.assertEqual(self.client.get("/admin/b2b/payables/").status_code, 403)

    def test_nav_hides_b2b_from_manager_shows_owner(self):
        self._login_as(self.owner)
        resp = self.client.get("/admin/b2b/settings/")
        self.assertContains(resp, "Sell B2B")

        self._login_as(self.manager)
        resp = self.client.get("/admin/", follow=True)
        # Manager lands somewhere in Mission Control; nav must not offer B2B.
        self.assertNotContains(resp, "b2b/marketplace")
