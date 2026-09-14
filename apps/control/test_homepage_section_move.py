from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.accounts.models import Membership
from apps.billing import services as billing_svc
from apps.cms import homepage_sections as hs
from apps.cms.models import ThemeSettings
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.projects.models import Project

User = get_user_model()


@override_settings(ALLOWED_HOSTS=["*"])
class ThemeSectionMoveViewTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="MoveSectionCo", status="active", feature_flags={"onboarded": True},
        )
        billing_svc.ensure_subscription(self.project)
        owner = User.objects.create_user("owner", "owner@t.test", "pw", is_staff=True)
        Membership.objects.create(user=owner, project=self.project, role="owner")
        self.client.force_login(owner)
        session = self.client.session
        session[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        session.save()

    def test_theme_screen_shows_the_reorder_panel(self):
        body = self.client.get("/admin/cms/theme/").content.decode()
        self.assertIn("Homepage section order", body)
        self.assertIn("Promo banner", body)
        self.assertIn("Shop by category", body)

    def test_move_down_then_up_round_trips(self):
        default_order = hs.section_keys_for_skin("default")
        first, second = default_order[0], default_order[1]

        self.client.post(
            "/admin/cms/theme/sections/move/", {"key": first, "direction": "down"},
        )
        theme = ThemeSettings.objects.get(project=self.project)
        self.assertEqual(theme.homepage_sections[0], second)
        self.assertEqual(theme.homepage_sections[1], first)

        self.client.post(
            "/admin/cms/theme/sections/move/", {"key": first, "direction": "up"},
        )
        theme.refresh_from_db()
        self.assertEqual(theme.homepage_sections, default_order)

    def test_move_redirects_back_to_theme_screen(self):
        default_order = hs.section_keys_for_skin("default")
        resp = self.client.post(
            "/admin/cms/theme/sections/move/",
            {"key": default_order[0], "direction": "down"},
        )
        self.assertRedirects(resp, "/admin/cms/theme/")

    def test_store_staff_can_also_view_and_reorder(self):
        """Theme (like Banners/Pages) isn't owner-restricted — any store
        staff member manages content, matching the rest of the CMS screens."""
        staff = User.objects.create_user("staff", "staff@t.test", "pw", is_staff=True)
        Membership.objects.create(user=staff, project=self.project, role="staff")
        self.client.force_login(staff)
        session = self.client.session
        session[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        session.save()
        self.assertEqual(self.client.get("/admin/cms/theme/").status_code, 200)

        default_order = hs.section_keys_for_skin("default")
        self.client.post(
            "/admin/cms/theme/sections/move/",
            {"key": default_order[0], "direction": "down"},
        )
        theme = ThemeSettings.objects.get(project=self.project)
        self.assertEqual(theme.homepage_sections[0], default_order[1])
