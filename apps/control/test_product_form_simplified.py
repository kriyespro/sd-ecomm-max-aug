"""Product create/edit form (apps/control/forms.py ProductForm): slug and
SKU inputs are gone (Product.save() auto-generates both from the title
when blank, so there was nothing for a merchant to fill in), Status
defaults to Active on a brand-new product, and Featured/New arrival/
Bestseller moved out to the per-row checkboxes on /admin/products/
(test_product_flag_toggle.py) instead of living on the form too."""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.accounts.models import Membership
from apps.billing import services as billing_svc
from apps.catalog.models import Product
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.projects.models import Project

User = get_user_model()


@override_settings(ALLOWED_HOSTS=["*"])
class ProductFormSimplifiedTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="SimpleCo", status="active", feature_flags={"onboarded": True},
        )
        billing_svc.ensure_subscription(self.project)
        owner = User.objects.create_user("owner", "owner@t.test", "pw", is_staff=True)
        Membership.objects.create(user=owner, project=self.project, role="owner")
        self.client.force_login(owner)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()

    def test_create_form_has_no_slug_sku_or_flag_inputs(self):
        body = self.client.get("/admin/products/new/").content.decode()
        self.assertNotIn('name="slug"', body)
        self.assertNotIn('name="sku"', body)
        self.assertNotIn('name="is_featured"', body)
        self.assertNotIn('name="is_new_arrival"', body)
        self.assertNotIn('name="is_bestseller"', body)

    def test_create_form_defaults_status_to_active(self):
        body = self.client.get("/admin/products/new/").content.decode()
        start = body.index('name="status"')
        end = body.index("</select>", start)
        chunk = body[start:end]
        active_pos = chunk.index('value="active"')
        self.assertIn("selected", chunk[active_pos:active_pos + 40])

    def test_saving_without_slug_or_sku_auto_generates_both(self):
        resp = self.client.post("/admin/products/new/", {
            "title": "Auto Gen Tee", "price": "499", "status": "active", "kind": "simple",
        }, follow=True)
        self.assertEqual(resp.status_code, 200)
        product = Product.objects.get(title="Auto Gen Tee")
        self.assertTrue(product.slug)
        self.assertTrue(product.sku)

    def test_edit_form_also_has_no_slug_sku_or_flag_inputs(self):
        product = Product.objects.create(
            project=self.project, title="Existing", slug="existing", status="active", price="10",
        )
        body = self.client.get(f"/admin/products/{product.pk}/").content.decode()
        self.assertNotIn('name="slug"', body)
        self.assertNotIn('name="sku"', body)
        self.assertNotIn('name="is_featured"', body)

    def test_editing_an_existing_product_does_not_reset_status_to_active(self):
        product = Product.objects.create(
            project=self.project, title="Stays Draft", slug="stays-draft",
            status="draft", price="10",
        )
        body = self.client.get(f"/admin/products/{product.pk}/").content.decode()
        start = body.index('name="status"')
        end = body.index("</select>", start)
        chunk = body[start:end]
        draft_pos = chunk.index('value="draft"')
        self.assertIn("selected", chunk[draft_pos:draft_pos + 40])

    def test_hsn_sac_and_tax_class_live_under_a_collapsed_extra_section(self):
        body = self.client.get("/admin/products/new/").content.decode()
        extra_pos = body.index(">Extra<")
        details_start = body.rindex("<details", 0, extra_pos)
        details_end = body.index("</details>", extra_pos)
        # not collapsed open by default
        self.assertNotIn("open", body[details_start:details_start + 20])
        chunk = body[details_start:details_end]
        self.assertIn('name="hsn_sac"', chunk)
        self.assertIn('name="tax_class"', chunk)
        # and not duplicated in the main field grid above it
        self.assertNotIn('name="hsn_sac"', body[:details_start])
        self.assertNotIn('name="tax_class"', body[:details_start])

    def test_saving_hsn_sac_and_tax_class_from_the_extra_section_persists(self):
        resp = self.client.post("/admin/products/new/", {
            "title": "Taxed Thing", "price": "250", "status": "active", "kind": "simple",
            "hsn_sac": "6109", "tax_class": "GST18",
        }, follow=True)
        self.assertEqual(resp.status_code, 200)
        product = Product.objects.get(title="Taxed Thing")
        self.assertEqual(product.hsn_sac, "6109")
        self.assertEqual(product.tax_class, "GST18")

    def test_existing_slug_and_sku_survive_an_unrelated_edit(self):
        product = Product.objects.create(
            project=self.project, title="Keep Me", slug="keep-me-slug",
            sku="KEEP-SKU-1", status="active", price="10",
        )
        self.client.post(f"/admin/products/{product.pk}/", {
            "title": "Keep Me Renamed", "price": "10", "status": "active", "kind": "simple",
        })
        product.refresh_from_db()
        self.assertEqual(product.slug, "keep-me-slug")
        self.assertEqual(product.sku, "KEEP-SKU-1")
        self.assertEqual(product.title, "Keep Me Renamed")
