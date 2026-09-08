import io

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from apps.accounts.models import Membership, StoreRole
from apps.catalog import importer
from apps.catalog.models import Product, ProductImage
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.inventory.models import InventoryItem
from apps.media.models import MediaAsset
from apps.media.services import store_upload
from apps.projects.models import Project

User = get_user_model()


def _png_bytes():
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(buf, format="PNG")
    return buf.getvalue()


def _csv(*rows, headers=None):
    headers = headers or importer.TEMPLATE_HEADERS
    lines = [",".join(headers)]
    for r in rows:
        lines.append(",".join('"' + str(r.get(h, "")).replace('"', '""') + '"' for h in headers))
    return SimpleUploadedFile(
        "import.csv", "\n".join(lines).encode("utf-8"), content_type="text/csv"
    )


class ImporterCoreTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="Imp Co")

    def test_sample_csv_has_all_headers(self):
        text = importer.sample_csv()
        self.assertEqual(text.splitlines()[0], ",".join(importer.TEMPLATE_HEADERS))
        self.assertIn("Classic Cotton Tee", text)

    def test_creates_products_with_taxonomy_and_tags(self):
        f = _csv({
            "title": "Blue Shirt", "sku": "SH-1", "price": "999",
            "status": "active", "category": "Shirts", "brand": "Acme",
            "type": "Apparel", "tags": "cotton, blue", "is_featured": "yes",
        })
        rows = importer.read_table(f, "import.csv")
        result = importer.run_import(self.project, rows, default_status="draft")

        self.assertEqual((result.created, result.updated, len(result.errors)), (1, 0, 0))
        p = Product.objects.get(project=self.project, sku="SH-1")
        self.assertEqual(p.title, "Blue Shirt")
        self.assertEqual(p.status, "active")
        self.assertTrue(p.is_featured)
        self.assertEqual(p.category.name, "Shirts")
        self.assertEqual(p.brand.name, "Acme")
        self.assertEqual(set(p.tags.values_list("name", flat=True)), {"cotton", "blue"})

    def test_reimport_by_sku_updates_in_place(self):
        importer.run_import(self.project, importer.read_table(
            _csv({"title": "Mug", "sku": "M-1", "price": "100"}), "a.csv"))
        importer.run_import(self.project, importer.read_table(
            _csv({"title": "Mug XL", "sku": "M-1", "price": "150"}), "b.csv"))

        self.assertEqual(Product.objects.filter(project=self.project).count(), 1)
        p = Product.objects.get(sku="M-1")
        self.assertEqual(p.title, "Mug XL")
        self.assertEqual(str(p.price), "150.00")

    def test_bad_price_row_is_skipped_others_import(self):
        f = _csv(
            {"title": "Good", "sku": "G-1", "price": "10"},
            {"title": "Bad", "sku": "B-1", "price": "ten"},
        )
        result = importer.run_import(self.project, importer.read_table(f, "x.csv"))
        self.assertEqual(result.created, 1)
        self.assertEqual(len(result.errors), 1)
        self.assertIn("price", result.errors[0][1])
        self.assertTrue(Product.objects.filter(sku="G-1").exists())
        self.assertFalse(Product.objects.filter(sku="B-1").exists())

    def test_stock_sets_inventory_at_default_warehouse(self):
        importer.run_import(self.project, importer.read_table(
            _csv({"title": "Stocked", "sku": "S-1", "price": "5", "stock": "42"}), "s.csv"))
        p = Product.objects.get(sku="S-1")
        item = InventoryItem.objects.get(product=p, variant=None)
        self.assertEqual(item.quantity, 42)
        self.assertTrue(item.warehouse.is_default)

    def test_images_matched_from_media_library_by_filename(self):
        store_upload(
            project=self.project,
            upload=SimpleUploadedFile("hero.png", _png_bytes(), content_type="image/png"),
        )
        f = _csv({"title": "Withpic", "sku": "P-1", "price": "9",
                  "images": "hero.png | missing.jpg"})
        result = importer.run_import(self.project, importer.read_table(f, "p.csv"))

        p = Product.objects.get(sku="P-1")
        self.assertEqual(p.images.count(), 1)
        self.assertTrue(p.images.first().is_primary)
        self.assertEqual(result.images_attached, 1)
        self.assertEqual(result.missing_images, {"missing.jpg"})

    def test_xlsx_round_trips(self):
        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.append(["title", "sku", "price"])
        ws.append(["Sheeted", "XL-1", 77])
        buf = io.BytesIO()
        wb.save(buf)
        up = SimpleUploadedFile(
            "p.xlsx", buf.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        result = importer.run_import(self.project, importer.read_table(up, "p.xlsx"))
        self.assertEqual(result.created, 1)
        self.assertEqual(str(Product.objects.get(sku="XL-1").price), "77.00")


class ImportViewTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("o", "o@t.test", "pw", is_staff=True)
        self.project = Project.objects.create(
            name="ViewCo", feature_flags={"onboarded": True, "vertical": "general"},
        )
        Membership.objects.create(user=self.owner, project=self.project,
                                  role=StoreRole.OWNER, is_active=True)
        self.client.force_login(self.owner)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()

    def test_sample_download(self):
        r = self.client.get("/admin/products/import/sample.csv")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "text/csv")
        self.assertIn("attachment", r["Content-Disposition"])

    def test_import_page_renders(self):
        r = self.client.get("/admin/products/import/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Sample CSV")

    def test_upload_creates_products(self):
        r = self.client.post("/admin/products/import/", {
            "default_status": "active",
            "file": _csv({"title": "Via View", "sku": "V-1", "price": "12"}),
        })
        self.assertEqual(r.status_code, 302)
        self.assertTrue(Product.objects.filter(project=self.project, sku="V-1").exists())

    def test_media_link_is_under_catalog(self):
        from apps.control.navigation import _resolved_sections

        sections = {k: [i["name"] for i in items] for k, _l, _i, items in _resolved_sections()}
        self.assertIn("media", sections["catalog"])
        self.assertNotIn("media", sections["storefront"])
        self.assertIn("product_import", sections["catalog"])
