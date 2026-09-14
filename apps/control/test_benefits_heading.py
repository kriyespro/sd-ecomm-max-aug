"""The "Why it works" main heading, editable right on /admin/cms/benefits/
instead of only via the Theme settings "Section titles" panel — both write
the same ThemeSettings.section_titles['benefits']."""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.accounts.models import Membership
from apps.billing import services as billing_svc
from apps.cms.models import ThemeSettings
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.core.store_resolver import bust_project_chrome
from apps.projects.models import Domain, Project

User = get_user_model()


@override_settings(ALLOWED_HOSTS=["*"])
class BenefitsHeadingTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="BenefitsCo", status="active", feature_flags={"onboarded": True},
        )
        Domain.objects.create(project=self.project, host="benefitsco.test", is_verified=True)
        billing_svc.ensure_subscription(self.project)
        owner = User.objects.create_user("owner", "owner@t.test", "pw", is_staff=True)
        Membership.objects.create(user=owner, project=self.project, role="owner")
        self.client.force_login(owner)
        session = self.client.session
        session[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        session.save()

    def test_page_shows_default_placeholder_when_untouched(self):
        body = self.client.get("/admin/cms/benefits/").content.decode()
        self.assertIn("Section heading", body)
        self.assertIn('placeholder="Why it works"', body)
        self.assertIn('name="heading" value=""', body)

    def test_saving_a_heading_persists_and_prefills(self):
        self.client.post("/admin/cms/benefits/heading/", {"heading": "The proof"})
        theme = ThemeSettings.objects.get(project=self.project)
        self.assertEqual(theme.section_titles.get("benefits"), "The proof")

        body = self.client.get("/admin/cms/benefits/").content.decode()
        self.assertIn('name="heading" value="The proof"', body)

    def test_saved_heading_renders_on_the_botanica2_home_page(self):
        from apps.cms.models import Skin

        Skin.objects.filter(is_default=True).update(is_default=False)
        theme, _ = ThemeSettings.objects.get_or_create(project=self.project)
        theme.skin = Skin.objects.get(slug="botanica2")
        theme.save()
        bust_project_chrome(self.project.pk)

        self.client.post("/admin/cms/benefits/heading/", {"heading": "The proof"})
        body = self.client.get("/", HTTP_HOST="benefitsco.test").content.decode()
        self.assertIn(">The proof</h2>", body)
        self.assertNotIn(">Why it works</h2>", body)

    def test_blank_clears_a_previously_saved_heading(self):
        self.client.post("/admin/cms/benefits/heading/", {"heading": "The proof"})
        self.client.post("/admin/cms/benefits/heading/", {"heading": ""})
        theme = ThemeSettings.objects.get(project=self.project)
        self.assertNotIn("benefits", theme.section_titles)

    def test_does_not_disturb_other_saved_section_titles(self):
        ThemeSettings.objects.update_or_create(
            project=self.project, defaults={"section_titles": {"featured": "Bestsellers"}},
        )
        self.client.post("/admin/cms/benefits/heading/", {"heading": "The proof"})
        theme = ThemeSettings.objects.get(project=self.project)
        self.assertEqual(theme.section_titles.get("featured"), "Bestsellers")
        self.assertEqual(theme.section_titles.get("benefits"), "The proof")
