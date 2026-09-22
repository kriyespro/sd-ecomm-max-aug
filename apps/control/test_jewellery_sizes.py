"""Jewellery stores get the same Size & Colour quick builder as clothing/
fashion, relabelled to ring/bangle size and metal/gem colour -- both axes,
same as apparel, reusing apps.catalog.variants' existing two-axis handling
untouched (colour was previously hidden for jewellery; now shown, so a
gold/rose-gold/silver ring can have per-colour price and stock)."""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.accounts.models import Membership
from apps.billing import services as billing_svc
from apps.catalog.models import Attribute, Product
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.projects.models import Project

User = get_user_model()


@override_settings(ALLOWED_HOSTS=["*"])
class JewellerySizeBuilderTests(TestCase):
    def _login(self, vertical):
        project = Project.objects.create(
            name="JewelCo", status="active",
            feature_flags={"onboarded": True, "vertical": vertical},
        )
        billing_svc.ensure_subscription(project)
        owner = User.objects.create_user("owner", "owner@t.test", "pw", is_staff=True)
        Membership.objects.create(user=owner, project=project, role="owner")
        self.client.force_login(owner)
        session = self.client.session
        session[ACTIVE_PROJECT_SESSION_KEY] = project.pk
        session.save()
        return project

    def test_jewellery_product_form_shows_ring_bangle_labelling_and_colour(self):
        project = self._login("jewellery")
        product = Product.objects.create(
            project=project, title="Ring", price="999", status="draft",
        )
        body = self.client.get(f"/admin/products/{product.pk}/").content.decode()
        self.assertIn("Ring &amp; bangle size, and colour", body)
        self.assertIn("Ring / bangle size", body)
        self.assertIn("Colours</label>", body)
        self.assertIn("Yellow gold, Rose gold, White gold", body)

    def test_clothing_product_form_is_unaffected(self):
        project = self._login("clothing")
        product = Product.objects.create(
            project=project, title="Tee", price="999", status="draft",
        )
        body = self.client.get(f"/admin/products/{product.pk}/").content.decode()
        self.assertIn("Size &amp; colour", body)
        self.assertIn("Colours</label>", body)
        self.assertNotIn("Ring &amp; bangle size", body)

    def test_saving_jewellery_sizes_only_creates_one_variant_per_size(self):
        # Colour is optional -- a seller who leaves it blank still gets the
        # one-axis (sizes-only) product apps.catalog.variants already
        # handled before colour was ever shown for jewellery.
        project = self._login("jewellery")
        product = Product.objects.create(
            project=project, title="Ring", price="999", status="draft",
        )
        resp = self.client.post(
            f"/admin/products/{product.pk}/",
            {
                "title": "Ring", "price": "999", "status": "draft", "kind": "simple",
                "tags": "", "sizes": "6, 7, 8", "colors": "",
            },
        )
        self.assertEqual(resp.status_code, 302)
        product.refresh_from_db()
        self.assertEqual(product.variants.filter(is_active=True).count(), 3)
        self.assertTrue(Attribute.objects.filter(project=project, name="Size").exists())
        self.assertFalse(Attribute.objects.filter(project=project, name="Color").exists())

    def test_saving_jewellery_size_and_colour_creates_one_variant_per_combo(self):
        project = self._login("jewellery")
        product = Product.objects.create(
            project=project, title="Ring", price="999", status="draft",
        )
        resp = self.client.post(
            f"/admin/products/{product.pk}/",
            {
                "title": "Ring", "price": "999", "status": "draft", "kind": "simple",
                "tags": "", "sizes": "6, 7", "colors": "Yellow gold, Rose gold",
                "combo_price[6|||Yellow gold]": "1200",
            },
        )
        self.assertEqual(resp.status_code, 302)
        product.refresh_from_db()
        variants = product.variants.filter(is_active=True)
        self.assertEqual(variants.count(), 4)  # 2 sizes x 2 colours
        self.assertTrue(Attribute.objects.filter(project=project, name="Size").exists())
        self.assertTrue(Attribute.objects.filter(project=project, name="Color").exists())
        priced = variants.get(price="1200")
        names = {av.value for av in priced.attribute_values.all()}
        self.assertEqual(names, {"6", "Yellow gold"})

    def test_onboarding_step_two_mentions_jewellery_colour(self):
        self._login("jewellery")
        body = self.client.get("/admin/start/").content.decode()
        self.assertIn("ring &amp; bangle size and colour", body)
