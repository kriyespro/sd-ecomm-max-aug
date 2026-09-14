"""Theme settings screen: "Products shown" (5/8/10/15/20, right below
"Products per row") and the expanded "Products per row" choices (3/4/5/6)."""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.accounts.models import Membership
from apps.billing import services as billing_svc
from apps.cms.models import ThemeSettings
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.projects.models import Project

User = get_user_model()


@override_settings(ALLOWED_HOSTS=["*"])
class ProductsShownFormTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="ShownFormCo", status="active", feature_flags={"onboarded": True},
        )
        billing_svc.ensure_subscription(self.project)
        owner = User.objects.create_user("owner", "owner@t.test", "pw", is_staff=True)
        Membership.objects.create(user=owner, project=self.project, role="owner")
        self.client.force_login(owner)
        session = self.client.session
        session[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        session.save()

    def _base_payload(self, **extra):
        payload = {
            "primary_color": "#111111", "secondary_color": "#ffffff",
            "accent_color": "#2563eb",
            "heading_align": "", "heading_size": "md", "heading_font": "display",
            "products_per_row": "4", "products_shown": "8",
        }
        payload.update(extra)
        return payload

    def test_theme_screen_shows_products_shown_right_below_products_per_row(self):
        body = self.client.get("/admin/cms/theme/").content.decode()
        row_pos = body.index('name="products_per_row"')
        shown_pos = body.index('name="products_shown"')
        self.assertLess(row_pos, shown_pos)

    def test_products_per_row_offers_3_to_6(self):
        body = self.client.get("/admin/cms/theme/").content.decode()
        start = body.index('name="products_per_row"')
        end = body.index("</select>", start)
        chunk = body[start:end]
        for n in ("3", "4", "5", "6"):
            self.assertIn(f'value="{n}"', chunk)

    def test_products_shown_offers_5_8_10_15_20(self):
        body = self.client.get("/admin/cms/theme/").content.decode()
        start = body.index('name="products_shown"')
        end = body.index("</select>", start)
        chunk = body[start:end]
        for n in ("5", "8", "10", "15", "20"):
            self.assertIn(f'value="{n}"', chunk)

    def test_saving_six_per_row_and_twenty_shown_persists(self):
        self.client.post(
            "/admin/cms/theme/",
            self._base_payload(products_per_row="6", products_shown="20"),
        )
        theme = ThemeSettings.objects.get(project=self.project)
        self.assertEqual(theme.products_per_row, 6)
        self.assertEqual(theme.products_shown, 20)

    def test_saving_three_per_row_and_five_shown_persists(self):
        self.client.post(
            "/admin/cms/theme/",
            self._base_payload(products_per_row="3", products_shown="5"),
        )
        theme = ThemeSettings.objects.get(project=self.project)
        self.assertEqual(theme.products_per_row, 3)
        self.assertEqual(theme.products_shown, 5)
