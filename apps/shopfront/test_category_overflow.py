"""'Shop by category' home-row: more than 8 categories must all reach the
page (the view used to hard-cap at 8, silently dropping the rest) and the
overflow must render as a horizontal-scroll slider, not just vanish."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.categories.models import Category
from apps.catalog.models import Product
from apps.cms.models import Skin
from apps.projects.models import Domain, Project

User = get_user_model()


@override_settings(ALLOWED_HOSTS=["*"])
class CategoryRowOverflowTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="Acme", feature_flags={"onboarded": True},
        )
        Domain.objects.create(project=self.project, host="acme.test", is_verified=True)
        self.admin = User.objects.create_superuser("root", "root@t.test", "pw")
        self.client.force_login(self.admin)

        for i in range(11):
            cat = Category.objects.create(
                project=self.project, name=f"Cat {i}", slug=f"cat-{i}", order=i,
            )
            Product.objects.create(
                project=self.project, category=cat, title=f"P{i}", slug=f"p{i}",
                price=Decimal("100"), status="active",
            )

    def _render(self, skin_slug):
        skin = Skin.objects.get(slug=skin_slug)
        resp = self.client.get("/", HTTP_HOST="acme.test", data={"preview_skin": skin.pk})
        self.assertEqual(resp.status_code, 200, f"{skin_slug}: {resp.status_code}")
        return resp.content.decode()

    def test_view_does_not_truncate_to_8(self):
        from apps.shopfront.views import HomeView  # noqa: F401 — sanity the view still imports

        html = self._render("botanica3")
        for i in range(11):
            self.assertIn(f"category=cat-{i}", html, f"cat-{i} missing from botanica3 home")

    def test_botanica3_slider_kicks_in_past_8(self):
        html = self._render("botanica3")
        self.assertIn("overflow-x-auto", html)
        self.assertIn("snap-mandatory", html)

    def test_default_skin_shows_all_11(self):
        html = self._render("default")
        for i in range(11):
            self.assertIn(f"category=cat-{i}", html)

    def test_botanica2_skin_shows_all_11(self):
        html = self._render("botanica2")
        for i in range(11):
            self.assertIn(f"category=cat-{i}", html)

    def test_ornza_skin_shows_all_11(self):
        html = self._render("ornza")
        for i in range(11):
            self.assertIn(f"category=cat-{i}", html)

    def test_eight_or_fewer_stays_plain_grid_no_slider(self):
        Category.objects.filter(project=self.project).exclude(
            slug__in=[f"cat-{i}" for i in range(8)]
        ).delete()
        html = self._render("botanica3")
        for i in range(8):
            self.assertIn(f"category=cat-{i}", html)
        # the category row's own grid class, not tripped by an unrelated
        # overflow-x-auto elsewhere on the page (e.g. a product slider)
        self.assertIn("lg:grid-cols-8", html)
