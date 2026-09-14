from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.accounts.models import Membership
from apps.billing import services as billing_svc
from apps.cms.models import ThemeSettings
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.projects.models import Project

User = get_user_model()


@override_settings(ALLOWED_HOSTS=["*"])
class HeadingStyleThemeFormTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="ThemeFormCo", status="active", feature_flags={"onboarded": True},
        )
        billing_svc.ensure_subscription(self.project)
        owner = User.objects.create_user("owner", "owner@t.test", "pw", is_staff=True)
        Membership.objects.create(user=owner, project=self.project, role="owner")
        self.client.force_login(owner)
        session = self.client.session
        session[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        session.save()

    def test_theme_screen_shows_the_new_heading_fields(self):
        body = self.client.get("/admin/cms/theme/").content.decode()
        self.assertIn('name="heading_align"', body)
        self.assertIn('name="heading_size"', body)
        self.assertIn('name="heading_font"', body)

    def test_saving_heading_style_persists(self):
        resp = self.client.post(
            "/admin/cms/theme/",
            {
                "primary_color": "#111111", "secondary_color": "#ffffff",
                "accent_color": "#2563eb",
                "heading_align": "center", "heading_size": "lg", "heading_font": "sans",
                "products_per_row": "4", "products_shown": "8",
            },
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        theme = ThemeSettings.objects.get(project=self.project)
        self.assertEqual(theme.heading_align, "center")
        self.assertEqual(theme.heading_size, "lg")
        self.assertEqual(theme.heading_font, "sans")
