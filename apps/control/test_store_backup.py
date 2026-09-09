import io
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from apps.accounts.models import PlatformRole, Profile
from apps.catalog.models import Brand, Product, ProductImage, ProductType, Variant
from apps.categories.models import Category
from apps.cms.models import BudgetBand, Menu, MenuItem, Page, StoreProfile, ThemeSettings
from apps.control import store_backup
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.customers.models import Customer
from apps.projects.models import Project

User = get_user_model()


def _png():
    from PIL import Image

    b = io.BytesIO()
    Image.new("RGB", (80, 80), "teal").save(b, format="PNG")
    return b.getvalue()


def _build_source(project):
    clothing = Category.objects.create(project=project, name="Clothing", slug="clothing", order=1)
    shirts = Category.objects.create(project=project, name="Shirts", slug="shirts",
                                     parent=clothing, order=2)
    brand = Brand.objects.create(project=project, name="Acme", slug="acme")
    ptype = ProductType.objects.create(project=project, name="Simple", slug="simple", kind="simple")

    p1 = Product.objects.create(project=project, title="Tee", slug="tee", price=Decimal("499"),
                                category=clothing, type=ptype, status="active")
    ProductImage.objects.create(product=p1,
                                image=SimpleUploadedFile("t.png", _png(), "image/png"),
                                alt="tee", is_primary=True)
    Variant.objects.create(product=p1, name="M", sku="TEE-M",
                           price=Decimal("499"), stock=5)
    Product.objects.create(project=project, title="Formal Shirt", slug="formal-shirt",
                           price=Decimal("1299"), category=shirts, brand=brand, status="active")
    Product.objects.create(project=project, title="Loose Item", slug="loose", price=Decimal("99"),
                           status="active")

    page = Page.objects.create(project=project, title="About Us", slug="about-us", status="published")
    menu = Menu.objects.create(project=project, name="Header", location="main")
    top = MenuItem.objects.create(menu=menu, label="Shop", link_type="category",
                                  category=shirts, order=1)
    MenuItem.objects.create(menu=menu, label="Kids", link_type="category", category=shirts,
                            parent=top, order=1)
    MenuItem.objects.create(menu=menu, label="About", link_type="page", page=page, order=2)

    BudgetBand.objects.create(project=project, label="Under 500", max_price=Decimal("500"), order=1)
    ThemeSettings.objects.update_or_create(project=project,
                                           defaults={"primary_color": "#123456"})
    StoreProfile.objects.update_or_create(project=project,
                                          defaults={"tagline": "Best tees in town"})


@override_settings(ALLOWED_HOSTS=["*"])
class StoreBackupRoundTripTests(TestCase):
    def setUp(self):
        self.src = Project.objects.create(name="SourceCo", status="active")
        _build_source(self.src)

        self.dst = Project.objects.create(name="TargetCo", status="active")
        # pre-existing content on the target that the restore must clear
        Category.objects.create(project=self.dst, name="Old", slug="old")
        Product.objects.create(project=self.dst, title="OldProd", slug="oldprod",
                               price=Decimal("1"), status="active")
        # and an order/customer that the restore must NOT touch
        self.customer = Customer.objects.create(project=self.dst, email="buyer@dst.test")

    def test_dump_then_restore_clones_content_and_remaps_fks(self):
        blob = store_backup.dump_store(self.src)
        with self.captureOnCommitCallbacks(execute=True):
            counts = store_backup.restore_store(self.dst, blob, actor=None)

        self.assertEqual(counts["product"], 3)
        self.assertEqual(counts["category"], 2)

        cats = {c.name: c for c in Category.objects.filter(project=self.dst)}
        self.assertEqual(set(cats), {"Clothing", "Shirts"})          # "Old" wiped
        self.assertEqual(cats["Shirts"].parent_id, cats["Clothing"].pk)  # self-FK remapped

        titles = set(Product.objects.filter(project=self.dst).values_list("title", flat=True))
        self.assertEqual(titles, {"Tee", "Formal Shirt", "Loose Item"})   # "OldProd" wiped
        shirt = Product.objects.get(project=self.dst, title="Formal Shirt")
        self.assertEqual(shirt.category_id, cats["Shirts"].pk)            # cross-model FK remapped
        self.assertEqual(shirt.brand.name, "Acme")

        tee = Product.objects.get(project=self.dst, title="Tee")
        img = ProductImage.objects.get(product=tee)
        self.assertTrue(img.image.name and img.image.storage.exists(img.image.name))
        self.assertEqual(Variant.objects.filter(product=tee).count(), 1)

        menu = Menu.objects.get(project=self.dst, name="Header")
        kids = MenuItem.objects.get(menu=menu, label="Kids")
        self.assertEqual(kids.parent.label, "Shop")                       # menu self-FK remapped
        self.assertEqual(MenuItem.objects.get(menu=menu, label="Shop").category_id, cats["Shirts"].pk)
        self.assertEqual(MenuItem.objects.get(menu=menu, label="About").page.slug, "about-us")

        self.assertEqual(ThemeSettings.objects.get(project=self.dst).primary_color, "#123456")
        self.assertEqual(StoreProfile.objects.get(project=self.dst).tagline, "Best tees in town")

        # untouched
        self.assertTrue(Customer.objects.filter(pk=self.customer.pk).exists())
        # source unchanged
        self.assertEqual(Product.objects.filter(project=self.src).count(), 3)

    def test_restore_rejects_a_non_backup_zip(self):
        bad = io.BytesIO()
        import zipfile
        with zipfile.ZipFile(bad, "w") as zf:
            zf.writestr("hello.txt", "nope")
        with self.assertRaises(store_backup.BackupError):
            store_backup.restore_store(self.dst, bad.getvalue(), actor=None)
        self.assertTrue(Product.objects.filter(project=self.dst, title="OldProd").exists())


@override_settings(ALLOWED_HOSTS=["*"])
class StoreBackupViewTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("root", "root@t.test", "pw")
        self.a = Project.objects.create(name="A Co", status="active")
        _build_source(self.a)
        self.b = Project.objects.create(name="B Co", status="active")

    def _login(self, user):
        self.client.force_login(user)

    def test_admin_downloads_and_restores(self):
        self._login(self.admin)
        r = self.client.get(f"/admin/stores/{self.a.pk}/backup/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "application/zip")
        blob = b"".join(r.streaming_content) if r.streaming else r.content

        with self.captureOnCommitCallbacks(execute=True):
            resp = self.client.post(f"/admin/stores/{self.b.pk}/restore/", {
                "confirm_name": "B Co",
                "backup": SimpleUploadedFile("bk.zip", blob, "application/zip"),
            })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Product.objects.filter(project=self.b).count(), 3)

    def test_restore_needs_exact_name(self):
        self._login(self.admin)
        blob = self.client.get(f"/admin/stores/{self.a.pk}/backup/").content
        resp = self.client.post(f"/admin/stores/{self.b.pk}/restore/", {
            "confirm_name": "wrong",
            "backup": SimpleUploadedFile("bk.zip", blob, "application/zip"),
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Product.objects.filter(project=self.b).count(), 0)

    def test_dgc_can_backup_their_managed_store_but_not_others(self):
        dgc = User.objects.create_user("d", "d@t.test", "pw", is_staff=True)
        Profile.objects.filter(user=dgc).update(platform_role=PlatformRole.MANAGER)
        from apps.billing import services as billing_svc
        sub = billing_svc.ensure_subscription(self.a)
        sub.manager = User.objects.get(pk=dgc.pk)
        sub.save(update_fields=["manager"])
        self._login(User.objects.get(pk=dgc.pk))
        self.assertEqual(self.client.get(f"/admin/stores/{self.a.pk}/backup/").status_code, 200)
        # store B is not theirs
        self.assertEqual(self.client.get(f"/admin/stores/{self.b.pk}/backup/").status_code, 404)


@override_settings(ALLOWED_HOSTS=["*"])
class OwnerBackupScreenTests(TestCase):
    def setUp(self):
        from apps.accounts.models import Membership, StoreRole

        self.store = Project.objects.create(name="OwnerCo", status="active",
                                            feature_flags={"onboarded": True})
        _build_source(self.store)
        self.owner = User.objects.create_user("o", "o@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.store, user=self.owner, role=StoreRole.OWNER)
        self.mgr = User.objects.create_user("m", "m@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.store, user=self.mgr, role=StoreRole.MANAGER)

    def _login(self, user):
        self.client.force_login(user)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.store.pk
        s.save()

    def test_owner_downloads_and_restores_own_store(self):
        self._login(self.owner)
        self.assertEqual(self.client.get("/admin/backup/").status_code, 200)
        blob = self.client.get("/admin/backup/download/").content
        self.assertTrue(blob[:2] == b"PK")

        Product.objects.filter(project=self.store).delete()
        with self.captureOnCommitCallbacks(execute=True):
            r = self.client.post("/admin/backup/restore/", {
                "confirm_name": "OwnerCo",
                "backup": SimpleUploadedFile("b.zip", blob, "application/zip"),
            })
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Product.objects.filter(project=self.store).count(), 3)

    def test_manager_cannot_reach_owner_backup(self):
        self._login(self.mgr)
        self.assertEqual(self.client.get("/admin/backup/").status_code, 403)


@override_settings(ALLOWED_HOSTS=["*"])
class PlatformBackupTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("root", "root@t.test", "pw")
        self.s1 = Project.objects.create(name="Store One", slug="store-one", status="active")
        _build_source(self.s1)
        self.s2 = Project.objects.create(name="Store Two", slug="store-two", status="active")

    def test_platform_dump_and_restore_recreates_a_deleted_store(self):
        from apps.control import store_backup

        blob = store_backup.dump_platform()
        self.assertTrue(blob[:2] == b"PK")

        Project.objects.filter(pk=self.s1.pk).delete()   # gone entirely
        self.assertFalse(Project.objects.filter(slug="store-one").exists())

        with self.captureOnCommitCallbacks(execute=True):
            report = store_backup.restore_platform(blob, actor=self.admin)

        recreated = Project.objects.get(slug="store-one")
        self.assertEqual(recreated.name, "Store One")
        self.assertEqual(Product.objects.filter(project=recreated).count(), 3)
        self.assertNotIn("error", report.get("store-one", {}))

    def test_platform_backup_view_and_restore_view(self):
        self.client.force_login(self.admin)
        r = self.client.get("/admin/platform-backup/")
        self.assertEqual(r.status_code, 200)
        blob = b"".join(r.streaming_content) if r.streaming else r.content

        with self.captureOnCommitCallbacks(execute=True):
            resp = self.client.post("/admin/platform-restore/", {
                "confirm": "RESTORE ALL",
                "backup": SimpleUploadedFile("p.zip", blob, "application/zip"),
            }, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Product.objects.filter(project=self.s1).count(), 3)

    def test_platform_restore_needs_passphrase(self):
        self.client.force_login(self.admin)
        blob = self.client.get("/admin/platform-backup/").content
        resp = self.client.post("/admin/platform-restore/", {
            "confirm": "nope",
            "backup": SimpleUploadedFile("p.zip", blob, "application/zip"),
        })
        self.assertEqual(resp.status_code, 302)

    def test_store_backup_rejects_a_platform_archive(self):
        from apps.control import store_backup

        blob = store_backup.dump_platform()
        with self.assertRaises(store_backup.BackupError):
            store_backup.restore_store(self.s2, blob, actor=self.admin)

    def test_backup_centre_lists_stores_and_does_per_store_restore(self):
        self.client.force_login(self.admin)
        r = self.client.get("/admin/platform-backups/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Store One")
        self.assertContains(r, "Store Two")
        self.assertContains(r, 'action="/admin/stores/%d/restore/"' % self.s1.pk)

        blob = self.client.get(f"/admin/stores/{self.s1.pk}/backup/").content
        Product.objects.filter(project=self.s1).delete()
        with self.captureOnCommitCallbacks(execute=True):
            resp = self.client.post(f"/admin/stores/{self.s1.pk}/restore/", {
                "confirm_name": "Store One",
                "backup": SimpleUploadedFile("s1.zip", blob, "application/zip"),
            })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Product.objects.filter(project=self.s1).count(), 3)

    def test_backup_centre_is_admin_only(self):
        from apps.accounts.models import PlatformRole, Profile
        dgc = User.objects.create_user("d", "d@t.test", "pw", is_staff=True)
        Profile.objects.filter(user=dgc).update(platform_role=PlatformRole.MANAGER)
        self.client.force_login(User.objects.get(pk=dgc.pk))
        self.assertEqual(self.client.get("/admin/platform-backups/").status_code, 403)
        self.assertEqual(self.client.get("/admin/platform-backups/server/").status_code, 403)

    def test_server_stats_partial_renders_for_admin(self):
        self.client.force_login(self.admin)
        r = self.client.get("/admin/platform-backups/server/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "CPU load")
        self.assertContains(r, "Disk")
        # the main screen embeds the same partial
        self.assertContains(self.client.get("/admin/platform-backups/"), "auto-refresh 10s")
