"""Two fixes to the catalog screens:

1. The "Products / Types / Tags / Brands / Categories" cross-nav tab strip
   only ever existed on the products page — Types/Tags/Brands/Categories had
   no way back to each other or to Products without the full sidebar.
2. /admin/products/ had no category filter, only search + status.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.accounts.models import Membership
from apps.billing import services as billing_svc
from apps.catalog.models import Product
from apps.categories.models import Category
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.projects.models import Project

User = get_user_model()

_CATALOG_PAGES = [
    "/admin/products/",
    "/admin/product-types/",
    "/admin/tags/",
    "/admin/brands/",
    "/admin/categories/",
]


@override_settings(ALLOWED_HOSTS=["*"])
class CatalogCrossNavTabsTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="CatalogNavCo", status="active", feature_flags={"onboarded": True},
        )
        billing_svc.ensure_subscription(self.project)
        owner = User.objects.create_user("owner", "owner@t.test", "pw", is_staff=True)
        Membership.objects.create(user=owner, project=self.project, role="owner")
        self.client.force_login(owner)
        session = self.client.session
        session[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        session.save()

    def test_every_catalog_page_links_to_every_other_catalog_page(self):
        for page in _CATALOG_PAGES:
            with self.subTest(page=page):
                body = self.client.get(page).content.decode()
                for target in _CATALOG_PAGES:
                    self.assertIn(f'href="{target}"', body)

    def test_active_tab_is_marked_current_not_a_plain_link_style(self):
        body = self.client.get("/admin/tags/").content.decode()
        self.assertIn('href="/admin/tags/" class="font-medium text-slate-900 underline"', body)


@override_settings(ALLOWED_HOSTS=["*"])
class ProductCategoryFilterTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="FilterCo", status="active", feature_flags={"onboarded": True},
        )
        billing_svc.ensure_subscription(self.project)
        owner = User.objects.create_user("owner2", "owner2@t.test", "pw", is_staff=True)
        Membership.objects.create(user=owner, project=self.project, role="owner")
        self.client.force_login(owner)
        session = self.client.session
        session[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        session.save()

        self.rings = Category.objects.create(
            project=self.project, name="Rings", slug="rings", is_active=True,
        )
        self.necklaces = Category.objects.create(
            project=self.project, name="Necklaces", slug="necklaces", is_active=True,
        )
        Product.objects.create(
            project=self.project, title="Gold Ring", slug="gold-ring",
            status="active", price="999", category=self.rings,
        )
        Product.objects.create(
            project=self.project, title="Silver Necklace", slug="silver-necklace",
            status="active", price="1499", category=self.necklaces,
        )
        Product.objects.create(
            project=self.project, title="Uncategorised Thing", slug="uncat",
            status="active", price="199",
        )

    def test_category_dropdown_lists_active_categories(self):
        body = self.client.get("/admin/products/").content.decode()
        self.assertIn('<option value="">All categories</option>', body)
        self.assertIn("Rings", body)
        self.assertIn("Necklaces", body)

    def test_filtering_by_category_shows_only_matching_products(self):
        resp = self.client.get(f"/admin/products/?category={self.rings.pk}")
        body = resp.content.decode()
        self.assertIn("Gold Ring", body)
        self.assertNotIn("Silver Necklace", body)
        self.assertNotIn("Uncategorised Thing", body)

    def test_no_category_filter_shows_everything(self):
        body = self.client.get("/admin/products/").content.decode()
        self.assertIn("Gold Ring", body)
        self.assertIn("Silver Necklace", body)
        self.assertIn("Uncategorised Thing", body)

    def test_category_filter_combines_with_search(self):
        resp = self.client.get(f"/admin/products/?category={self.rings.pk}&q=Silver")
        body = resp.content.decode()
        self.assertNotIn("Gold Ring", body)
        self.assertNotIn("Silver Necklace", body)  # right category, wrong search term

    def test_selected_category_stays_selected_in_the_dropdown(self):
        body = self.client.get(f"/admin/products/?category={self.necklaces.pk}").content.decode()
        self.assertIn(f'<option value="{self.necklaces.pk}" selected>Necklaces</option>', body)
