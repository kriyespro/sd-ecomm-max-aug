from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.accounts.models import Membership
from apps.billing import services as billing_svc
from apps.cms.models import ThemeSettings
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.projects.models import Domain, Project

User = get_user_model()


@override_settings(ALLOWED_HOSTS=["*"])
class ThemeSectionTitlesFormTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="TitleFormCo", status="active", feature_flags={"onboarded": True},
        )
        Domain.objects.create(project=self.project, host="titleformco.test", is_verified=True)
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
            "products_per_row": "4",
        }
        payload.update(extra)
        return payload

    def test_theme_screen_shows_a_field_per_heading_with_default_as_placeholder(self):
        body = self.client.get("/admin/cms/theme/").content.decode()
        self.assertIn("Section titles", body)
        self.assertIn('name="title__categories"', body)
        self.assertIn('placeholder="Shop by category"', body)
        self.assertIn('name="title__featured"', body)
        self.assertIn('placeholder="Featured"', body)

    def test_saving_a_custom_title_renders_on_the_home_page(self):
        self.client.post(
            "/admin/cms/theme/", self._base_payload(title__featured="Bestsellers"),
        )
        theme = ThemeSettings.objects.get(project=self.project)
        self.assertEqual(theme.section_titles.get("featured"), "Bestsellers")

        body = self.client.get("/", HTTP_HOST="titleformco.test").content.decode()
        self.assertIn(">Bestsellers</h2>", body)
        self.assertNotIn(">Featured</h2>", body)

    def test_blank_field_clears_a_previously_saved_override(self):
        self.client.post(
            "/admin/cms/theme/", self._base_payload(title__featured="Bestsellers"),
        )
        self.client.post("/admin/cms/theme/", self._base_payload(title__featured=""))
        theme = ThemeSettings.objects.get(project=self.project)
        self.assertNotIn("featured", theme.section_titles)

    def test_a_field_missing_from_the_post_leaves_its_override_untouched(self):
        """A partial POST (no `title__categories` key at all, as opposed to
        one present but blank) must not silently clear that override — the
        real Theme page always submits every field, blank or not, but this
        guards against any other partial submission."""
        self.client.post(
            "/admin/cms/theme/",
            self._base_payload(title__featured="Bestsellers", title__categories="Collections"),
        )
        self.client.post(
            "/admin/cms/theme/", self._base_payload(title__featured="Bestsellers 2"),
        )
        theme = ThemeSettings.objects.get(project=self.project)
        self.assertEqual(theme.section_titles.get("featured"), "Bestsellers 2")
        self.assertEqual(theme.section_titles.get("categories"), "Collections")
