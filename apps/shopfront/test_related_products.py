"""Product detail page: "You may also like" — same-category products,
excluding the product itself."""

from decimal import Decimal

from django.test import TestCase, override_settings

from apps.categories.models import Category
from apps.catalog.models import Product
from apps.cms.models import Skin
from apps.projects.models import Domain, Project


@override_settings(ALLOWED_HOSTS=["*"])
class RelatedProductsTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="Acme", feature_flags={"onboarded": True},
        )
        Domain.objects.create(project=self.project, host="acme.test", is_verified=True)
        self.rings = Category.objects.create(project=self.project, name="Rings", slug="rings")
        self.necklaces = Category.objects.create(
            project=self.project, name="Necklaces", slug="necklaces",
        )
        self.main = Product.objects.create(
            project=self.project, category=self.rings, title="Gold Ring", slug="gold-ring",
            price=Decimal("999"), status="active",
        )
        # 7 more rings — only 5 should show up as related.
        for i in range(7):
            Product.objects.create(
                project=self.project, category=self.rings, title=f"Ring {i}", slug=f"ring-{i}",
                price=Decimal("500"), status="active",
            )
        Product.objects.create(
            project=self.project, category=self.necklaces, title="Chain", slug="chain",
            price=Decimal("300"), status="active",
        )

    def _render(self, skin_slug=None):
        kwargs = {}
        if skin_slug:
            kwargs["data"] = {"preview_skin": Skin.objects.get(slug=skin_slug).pk}
            from django.contrib.auth import get_user_model
            self.client.force_login(get_user_model().objects.create_superuser(
                f"root-{skin_slug}", f"root-{skin_slug}@t.test", "pw"))
        resp = self.client.get("/p/gold-ring/", HTTP_HOST="acme.test", **kwargs)
        self.assertEqual(resp.status_code, 200)
        return resp.content.decode()

    def test_shows_five_related_same_category(self):
        html = self._render()
        self.assertIn("You may also like", html)
        # each card links its image and its title to the same slug
        self.assertEqual(html.count('href="/p/ring-'), 10)

    def test_excludes_other_category(self):
        html = self._render()
        self.assertNotIn("/p/chain/", html)

    def test_excludes_self(self):
        html = self._render()
        # the buy box / breadcrumb legitimately link to the product's own
        # slug elsewhere on the page, so check it's absent specifically from
        # the "You may also like" card list, not the whole page.
        section = html.split("You may also like", 1)[1]
        self.assertNotIn('href="/p/gold-ring/"', section)

    def test_falls_back_to_default_skin_template_on_ornza(self):
        # ornza has no product.jinja of its own — confirms the skin loader
        # fallback still serves the same related-products section.
        html = self._render("ornza")
        self.assertIn("You may also like", html)
        self.assertEqual(html.count('href="/p/ring-'), 10)
