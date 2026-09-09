from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.accounts.models import Membership, PlatformRole, Profile, StoreRole
from apps.billing import services as billing_svc
from apps.cart.models import Cart, CartItem
from apps.catalog.models import Product
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.orders.services import place_order
from apps.projects.models import Project

User = get_user_model()

# Views a store's DGC must be shut out of.
_BLOCKED_PATHS = [
    "/admin/orders/",
    "/admin/orders/export/",
    "/admin/customers/",
    "/admin/analytics/",
    "/admin/reports/",
    "/admin/payments/providers/",
]


@override_settings(ALLOWED_HOSTS=["*"])
class OrderAccessTests(TestCase):
    def setUp(self):
        self.store = Project.objects.create(name="ShopCo", status="active",
                                            feature_flags={"onboarded": True})
        self.sub = billing_svc.ensure_subscription(self.store)

        self.owner = User.objects.create_user("o", "o@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.store, user=self.owner, role=StoreRole.OWNER)
        self.manager = User.objects.create_user("m", "m@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.store, user=self.manager, role=StoreRole.MANAGER)
        self.staff = User.objects.create_user("s", "s@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.store, user=self.staff, role=StoreRole.STAFF)

        self.dgc = User.objects.create_user("d", "d@t.test", "pw", is_staff=True)
        Profile.objects.filter(user=self.dgc).update(platform_role=PlatformRole.MANAGER)
        self.sub.manager = self.dgc
        self.sub.save(update_fields=["manager"])
        self.dgc = User.objects.get(pk=self.dgc.pk)

        self.admin = User.objects.create_superuser("root", "root@t.test", "pw")

        product = Product.objects.create(project=self.store, title="Mug", slug="mug",
                                         price=Decimal("250"), status="active")
        cart = Cart.objects.create(project=self.store, is_active=True)
        CartItem.objects.create(cart=cart, product=product, quantity=2, unit_price=Decimal("250"))
        self.order = place_order(project=self.store, cart=cart, email="buyer@t.test",
                                 billing_address={}, shipping_address={"name": "Buyer"})

    def _login(self, user):
        self.client.force_login(user)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.store.pk
        s.save()

    # --- DGC is locked out ------------------------------------------------
    def test_dgc_cannot_reach_any_order_customer_or_finance_view(self):
        self._login(self.dgc)
        for path in _BLOCKED_PATHS:
            self.assertEqual(self.client.get(path).status_code, 403, path)

    def test_dgc_nav_has_no_orders_or_customers(self):
        self._login(self.dgc)
        resp = self.client.get("/admin/products/")   # a page the DGC CAN see
        self.assertNotContains(resp, 'href="/admin/orders/"')
        self.assertNotContains(resp, 'href="/admin/customers/"')
        self.assertNotContains(resp, 'href="/admin/analytics/"')

    # --- store team keeps access ---------------------------------------
    def test_owner_and_manager_see_orders(self):
        for user in (self.owner, self.manager, self.staff):
            self._login(user)
            self.assertEqual(self.client.get("/admin/orders/").status_code, 200)

    def test_owner_exports_orders_csv(self):
        self._login(self.owner)
        resp = self.client.get("/admin/orders/export/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "text/csv")
        body = resp.content.decode()
        self.assertIn(self.order.number, body)
        self.assertIn("2x Mug", body)

    def test_manager_can_export_staff_cannot(self):
        self._login(self.manager)
        self.assertEqual(self.client.get("/admin/orders/export/").status_code, 200)
        self._login(self.staff)
        self.assertEqual(self.client.get("/admin/orders/export/").status_code, 403)

    def test_export_button_hidden_from_staff(self):
        self._login(self.staff)
        self.assertNotContains(self.client.get("/admin/orders/"), "Export CSV")
        self._login(self.owner)
        self.assertContains(self.client.get("/admin/orders/"), "Export CSV")

    # --- platform admin unaffected ------------------------------------
    def test_platform_admin_still_sees_everything(self):
        self._login(self.admin)
        for path in _BLOCKED_PATHS:
            self.assertIn(self.client.get(path).status_code, (200, 302), path)
