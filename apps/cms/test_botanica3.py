import io
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from apps.accounts.models import Membership, StoreRole
from apps.categories.models import Category
from apps.catalog.models import Product, ProductImage
from apps.cms import instagram as ig
from apps.cms.models import BudgetBand, InstagramItem, Skin
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.core.store_resolver import bust_project_chrome, store_chrome
from apps.projects.models import Domain, Project

User = get_user_model()


def _png():
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (6, 6), "green").save(buf, format="PNG")
    return buf.getvalue()


class Botanica3SkinTests(TestCase):
    def test_skin_row_seeded(self):
        s = Skin.objects.get(slug="botanica3")
        self.assertEqual(s.source, "builtin")
        self.assertTrue(s.is_active)
        self.assertEqual(s.status, "approved")

    def test_botanica2_untouched(self):
        self.assertTrue(Skin.objects.filter(slug="botanica2", label="Botanica 2.0").exists())


class BudgetBandTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="BandCo", status="active")

    def test_query_string(self):
        self.assertEqual(
            BudgetBand(min_price=Decimal("500"), max_price=Decimal("999")).query,
            "min=500&max=999",
        )
        self.assertEqual(BudgetBand(max_price=Decimal("499")).query, "max=499")
        self.assertEqual(BudgetBand(min_price=Decimal("5000")).query, "min=5000")
        self.assertEqual(BudgetBand().query, "")

    def test_chrome_exposes_active_bands(self):
        BudgetBand.objects.create(project=self.project, label="Under 499",
                                  max_price=Decimal("499"), order=1)
        BudgetBand.objects.create(project=self.project, label="Hidden",
                                  is_active=False, order=2)
        bust_project_chrome(self.project.pk)
        bands = store_chrome(self.project)["budget_bands"]
        self.assertEqual([b.label for b in bands], ["Under 499"])


class InstagramFetchGuardTests(TestCase):
    def test_rejects_non_instagram_host(self):
        for bad in ["https://evil.test/p/x/", "http://instagram.com/p/x/",
                    "https://instagram.com.evil.test/p/x/"]:
            with self.assertRaises(ig.InstagramError):
                ig.normalize_post_url(bad)

    def test_normalizes_and_strips_query(self):
        self.assertEqual(
            ig.normalize_post_url("instagram.com/p/ABC123/?igshid=xyz"),
            "https://instagram.com/p/ABC123/",
        )

    def test_fetch_reads_og_image_from_allowed_cdn(self):
        page = (
            b'<html><head><meta property="og:image" '
            b'content="https://scontent.cdninstagram.com/v/pic.jpg"></head></html>'
        )
        img = _png()

        def fake_get(url, *, limit):
            return page if "instagram.com/p/" in url else img

        with mock.patch.object(ig, "_get", side_effect=fake_get):
            content, source = ig.fetch_post_image("https://www.instagram.com/p/ABC/")
        self.assertEqual(source, "https://www.instagram.com/p/ABC/")
        self.assertTrue(content.read())

    def test_fetch_rejects_image_from_foreign_host(self):
        page = (b'<meta property="og:image" content="https://evil.test/x.jpg">')
        with mock.patch.object(ig, "_get", return_value=page):
            with self.assertRaises(ig.InstagramError):
                ig.fetch_post_image("https://www.instagram.com/p/ABC/")


@override_settings(ALLOWED_HOSTS=["*"])
class Botanica3RenderTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="B3", status="active", feature_flags={"onboarded": True},
        )
        Domain.objects.create(project=self.project, host="b3.test", is_verified=True)
        Skin.objects.filter(slug="botanica3").update(is_default=False)
        from apps.cms.models import ThemeSettings

        ThemeSettings.objects.update_or_create(
            project=self.project,
            defaults={"skin": Skin.objects.get(slug="botanica3"),
                      "show_category_headings": True},
        )
        cat = Category.objects.create(project=self.project, name="Balms", is_active=True)
        p = Product.objects.create(project=self.project, title="Balm", category=cat,
                                   status="active", search_indexed=True, price=Decimal("299"))
        ProductImage.objects.create(product=p, image=SimpleUploadedFile("b.png", _png()))
        BudgetBand.objects.create(project=self.project, label="Under ₹499",
                                  max_price=Decimal("499"), order=1)
        it = InstagramItem(project=self.project, caption="reel", order=1)
        it.image.save("ig.png", SimpleUploadedFile("ig.png", _png()), save=True)
        bust_project_chrome(self.project.pk)

    def test_home_renders_new_sections(self):
        r = self.client.get("/", HTTP_HOST="b3.test")
        self.assertEqual(r.status_code, 200)
        body = r.content.decode()
        self.assertIn("Shop by category", body)
        self.assertIn("Shop by budget", body)
        self.assertIn("Under ₹499", body)
        self.assertIn("From our Instagram", body)
        self.assertIn("lg:grid-cols-8", body)  # 8-up category row


class BudgetBandAdminTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("b", "b@t.test", "pw", is_staff=True)
        self.project = Project.objects.create(
            name="AdminCo", feature_flags={"onboarded": True, "vertical": "general"},
        )
        Membership.objects.create(user=self.user, project=self.project,
                                  role=StoreRole.OWNER, is_active=True)
        self.client.force_login(self.user)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()

    def test_create_band(self):
        r = self.client.post("/admin/cms/budget-bands/new/", {
            "label": "₹500–₹999", "min_price": "500", "max_price": "999",
            "order": "2", "is_active": "on",
        })
        self.assertEqual(r.status_code, 302)
        self.assertTrue(BudgetBand.objects.filter(project=self.project, label="₹500–₹999").exists())

    def test_min_above_max_rejected(self):
        r = self.client.post("/admin/cms/budget-bands/new/", {
            "label": "bad", "min_price": "999", "max_price": "100",
            "order": "1", "is_active": "on",
        })
        self.assertEqual(r.status_code, 200)
        self.assertFalse(BudgetBand.objects.filter(label="bad").exists())

    def test_instagram_list_and_manual_add(self):
        r = self.client.get("/admin/cms/instagram/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Pull images from Instagram")
