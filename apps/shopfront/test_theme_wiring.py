"""ThemeSettings fields that used to be dead weight for built-in skins —
secondary_color, accent_color, custom_css, font_body, font_heading were saved
but never read back (``_build_chrome``'s ``.only(...)`` never fetched them).
Covers the fix on two representative skins: default (the fallback every
other unlisted skin uses) and kaanidhi2-0 (a from-scratch skin, same base.jinja
patch pattern)."""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.cms.models import Skin, ThemeSettings
from apps.projects.models import Domain, Project

User = get_user_model()


@override_settings(ALLOWED_HOSTS=["*"])
class ThemeWiringTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="Acme", feature_flags={"onboarded": True},
        )
        Domain.objects.create(project=self.project, host="acme.test", is_verified=True)
        self.admin = User.objects.create_superuser("root", "root@t.test", "pw")
        self.client.force_login(self.admin)

    def _get(self, path="/"):
        return self.client.get(path, HTTP_HOST="acme.test")

    def test_blank_theme_is_a_no_op(self):
        """An untouched store (no ThemeSettings row at all) renders with no
        stray empty <style></style> or broken markup from the new blocks."""
        resp = self._get()
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn("accent-color:#2563eb", html)
        self.assertIn("height:3px;background:#ffffff", html)
        self.assertNotIn('font-family:""', html)

    def test_secondary_and_accent_color_render(self):
        ThemeSettings.objects.create(
            project=self.project, secondary_color="#11ff22", accent_color="#ff00aa",
        )
        html = self._get().content.decode()
        self.assertIn("accent-color:#ff00aa", html)
        self.assertIn("height:3px;background:#11ff22", html)

    def test_custom_css_renders_and_is_stripped_of_angle_brackets(self):
        ThemeSettings.objects.create(
            project=self.project,
            custom_css='.hero{color:red} </style><script>alert(1)</script>',
        )
        html = self._get().content.decode()
        self.assertIn(".hero{color:red}", html)
        self.assertNotIn("<script>alert(1)</script>", html)

    def test_font_body_and_heading_override_render_only_when_set(self):
        ThemeSettings.objects.create(
            project=self.project, font_body="Poppins", font_heading="Fraunces",
        )
        html = self._get().content.decode()
        self.assertIn('family=Poppins:wght', html)
        self.assertIn('family=Fraunces:wght', html)
        self.assertIn('font-family:"Poppins"', html)
        self.assertIn('font-family:"Fraunces"', html)

    def test_font_fields_absent_when_blank(self):
        ThemeSettings.objects.create(project=self.project)
        html = self._get().content.decode()
        self.assertNotIn("fonts.googleapis.com/css2?family=:wght", html)

    def test_kaanidhi2_skin_also_wired(self):
        skin = Skin.objects.get(slug="kaanidhi2-0")
        ThemeSettings.objects.create(
            project=self.project, accent_color="#00ffcc", secondary_color="#ff8800",
        )
        resp = self.client.get(
            "/", HTTP_HOST="acme.test", data={"preview_skin": skin.pk},
        )
        html = resp.content.decode()
        self.assertEqual(resp.status_code, 200)
        self.assertIn("accent-color:#00ffcc", html)
        self.assertIn("height:3px;background:#ff8800", html)
        # the skin's own original wiring must still be intact
        self.assertIn("Jost", html)
        self.assertIn("Parisienne", html)
