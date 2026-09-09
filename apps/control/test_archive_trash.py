from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import Membership, StoreRole
from apps.cart.models import Cart, CartItem
from apps.catalog.models import Product
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.control.tasks import purge_trashed_task
from apps.control.trash import TRASH_RETENTION_DAYS
from apps.media.models import MediaAsset
from apps.orders.services import place_order
from apps.projects.models import Project

User = get_user_model()


@override_settings(ALLOWED_HOSTS=["*"])
class ArchiveTrashTests(TestCase):
    def setUp(self):
        self.store = Project.objects.create(name="ShopCo", status="active",
                                            feature_flags={"onboarded": True})
        self.owner = User.objects.create_user("o", "o@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.store, user=self.owner, role=StoreRole.OWNER)
        self.manager = User.objects.create_user("m", "m@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.store, user=self.manager, role=StoreRole.MANAGER)
        self.staff = User.objects.create_user("s", "s@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.store, user=self.staff, role=StoreRole.STAFF)

        self.product = Product.objects.create(project=self.store, title="Mug", slug="mug",
                                              price=Decimal("250"), status="active")
        cart = Cart.objects.create(project=self.store, is_active=True)
        CartItem.objects.create(cart=cart, product=self.product, quantity=1, unit_price=Decimal("250"))
        self.order = place_order(project=self.store, cart=cart, email="b@t.test",
                                 billing_address={}, shipping_address={"name": "B"})

    def _login(self, user):
        self.client.force_login(user)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.store.pk
        s.save()

    # --- orders --------------------------------------------------------
    def test_manager_archives_and_unarchives_order(self):
        self._login(self.manager)
        self.client.post(f"/admin/orders/{self.order.pk}/archive/")
        self.order.refresh_from_db()
        self.assertTrue(self.order.is_archived)

        row = f'href="/admin/orders/{self.order.pk}/"'
        self.assertNotContains(self.client.get("/admin/orders/"), row)          # not in Active
        self.assertContains(self.client.get("/admin/orders/?archived=1"), row)  # in Archived

        self.client.post(f"/admin/orders/{self.order.pk}/unarchive/",
                         {"next": f"/admin/orders/{self.order.pk}/"})
        self.order.refresh_from_db()
        self.assertFalse(self.order.is_archived)

    def test_staff_cannot_archive_orders(self):
        self._login(self.staff)
        self.assertEqual(self.client.post(f"/admin/orders/{self.order.pk}/archive/").status_code, 403)

    # --- product trash ----------------------------------------------
    def test_trash_hides_product_and_forces_archived_status(self):
        self._login(self.owner)
        self.client.post(f"/admin/products/{self.product.pk}/delete/")
        self.product.refresh_from_db()
        self.assertIsNotNone(self.product.trashed_at)
        self.assertEqual(self.product.status, "archived")
        self.assertEqual(self.product.trashed_from_status, "active")

        row = f'href="/admin/products/{self.product.pk}/"'
        self.assertNotContains(self.client.get("/admin/products/"), row)
        self.assertContains(self.client.get("/admin/products/?trash=1"), "Mug")

    def test_restore_product_brings_back_previous_status(self):
        self._login(self.owner)
        self.client.post(f"/admin/products/{self.product.pk}/delete/")
        self.client.post(f"/admin/products/{self.product.pk}/restore/")
        self.product.refresh_from_db()
        self.assertIsNone(self.product.trashed_at)
        self.assertEqual(self.product.status, "active")

    def test_purge_blocked_while_in_a_cart_then_allowed(self):
        self._login(self.owner)
        self.client.post(f"/admin/products/{self.product.pk}/delete/")
        # a live cart still references it -> purge clears the cart line then deletes
        self.client.post(f"/admin/products/{self.product.pk}/purge/")
        self.assertFalse(Product.objects.filter(pk=self.product.pk).exists())

    def test_manager_gates_product_trash_actions(self):
        self._login(self.staff)
        r = self.client.post(f"/admin/products/{self.product.pk}/delete/")
        self.assertEqual(r.status_code, 403)
        self.product.refresh_from_db()
        self.assertIsNone(self.product.trashed_at)

    # --- media trash ----------------------------------------------
    def test_media_trash_restore_purge(self):
        asset = MediaAsset.objects.create(project=self.store, original_name="a.png",
                                          kind="image", size=10)
        self._login(self.owner)
        self.client.post(f"/admin/media/{asset.pk}/delete/")
        asset.refresh_from_db()
        self.assertIsNotNone(asset.trashed_at)
        self.assertNotContains(self.client.get("/admin/media/"),
                               f'/admin/media/{asset.pk}/delete/')

        self.client.post(f"/admin/media/{asset.pk}/restore/")
        asset.refresh_from_db()
        self.assertIsNone(asset.trashed_at)

        self.client.post(f"/admin/media/{asset.pk}/delete/")
        self.client.post(f"/admin/media/{asset.pk}/purge/")
        self.assertFalse(MediaAsset.objects.filter(pk=asset.pk).exists())

    # --- purge task ------------------------------------------------
    def test_purge_task_removes_only_expired_trash(self):
        old = Product.objects.create(project=self.store, title="Old", slug="old",
                                     price=Decimal("1"), status="active")
        from apps.control.trash import trash_product
        trash_product(old)
        Product.objects.filter(pk=old.pk).update(
            trashed_at=timezone.now() - timedelta(days=TRASH_RETENTION_DAYS + 1)
        )
        trash_product(self.product)   # trashed just now

        purge_trashed_task()
        self.assertFalse(Product.objects.filter(pk=old.pk).exists())
        self.assertTrue(Product.objects.filter(pk=self.product.pk).exists())
