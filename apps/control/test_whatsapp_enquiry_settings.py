"""Marketing -> WhatsApp enquiry button (/admin/marketing/whatsapp-enquiry/):
the on/off toggle that drives templates/shopfront/partials/_whatsapp_enquiry_btn.jinja.
Storefront-side rendering itself is covered by
apps/shopfront/test_whatsapp_enquiry_button.py."""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.accounts.models import Membership, StoreRole
from apps.cms.models import StoreProfile
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.projects.models import Project

User = get_user_model()


@override_settings(ALLOWED_HOSTS=["*"])
class WhatsAppEnquirySettingsTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="EnquiryCo", status="active", feature_flags={"onboarded": True},
        )
        self.owner = User.objects.create_user("eo", "eo@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.project, user=self.owner, role=StoreRole.OWNER)
        self.client.force_login(self.owner)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()

    def test_screen_reachable_from_marketing_nav(self):
        resp = self.client.get("/admin/marketing/whatsapp-enquiry/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "WhatsApp enquiry button")

    def test_enabling_without_a_number_is_rejected(self):
        StoreProfile.objects.create(project=self.project)
        resp = self.client.post(
            "/admin/marketing/whatsapp-enquiry/", {"whatsapp_enquiry_enabled": "on"},
        )
        self.assertEqual(resp.status_code, 200)  # re-renders with the error
        self.assertContains(resp, "Add a WhatsApp number on Store profile first.")
        profile = StoreProfile.objects.get(project=self.project)
        self.assertFalse(profile.whatsapp_enquiry_enabled)

    def test_enabling_with_a_number_saved_persists(self):
        StoreProfile.objects.create(project=self.project, whatsapp="+919812345678")
        resp = self.client.post(
            "/admin/marketing/whatsapp-enquiry/", {"whatsapp_enquiry_enabled": "on"},
        )
        self.assertRedirects(resp, "/admin/marketing/whatsapp-enquiry/")
        profile = StoreProfile.objects.get(project=self.project)
        self.assertTrue(profile.whatsapp_enquiry_enabled)

    def test_unchecking_disables_it(self):
        profile = StoreProfile.objects.create(
            project=self.project, whatsapp="+919812345678", whatsapp_enquiry_enabled=True,
        )
        resp = self.client.post("/admin/marketing/whatsapp-enquiry/", {})
        self.assertRedirects(resp, "/admin/marketing/whatsapp-enquiry/")
        profile.refresh_from_db()
        self.assertFalse(profile.whatsapp_enquiry_enabled)

    def test_no_number_shows_a_warning_and_link_to_store_profile(self):
        StoreProfile.objects.create(project=self.project)
        resp = self.client.get("/admin/marketing/whatsapp-enquiry/")
        self.assertContains(resp, "No WhatsApp number set yet")
        self.assertContains(resp, "/admin/cms/store-profile/")

    def test_staff_without_manage_role_is_denied(self):
        staff = User.objects.create_user("es", "es@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.project, user=staff, role=StoreRole.STAFF)
        self.client.force_login(staff)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()
        resp = self.client.get("/admin/marketing/whatsapp-enquiry/")
        self.assertEqual(resp.status_code, 403)

    def test_nav_lists_the_screen_under_marketing(self):
        body = self.client.get("/admin/products/").content.decode()
        self.assertIn("/admin/marketing/whatsapp-enquiry/", body)
        self.assertIn("WhatsApp enquiry button", body)
