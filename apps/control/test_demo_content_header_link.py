"""The demo-content header action (base_control.jinja): a red "Remove demo
content" button next to "See your store" while the seeded demo catalogue is
still around, replaced by an "Import demo content" link back to the Store
profile screen once it's gone -- on every /admin/ page, including /admin/start/,
not just the Store profile screen where the actual wipe-and-reseed form lives."""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.accounts.models import Membership, StoreRole
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.projects.models import Project

User = get_user_model()


@override_settings(ALLOWED_HOSTS=["*"])
class DemoContentHeaderLinkTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="HeaderCo", status="active", feature_flags={"onboarded": True},
        )
        self.owner = User.objects.create_user("ho", "ho@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.project, user=self.owner, role=StoreRole.OWNER)
        self.client.force_login(self.owner)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()

    def test_import_link_shown_on_start_page_once_demo_is_removed(self):
        resp = self.client.get("/admin/start/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Import demo content")
        self.assertContains(resp, "/admin/cms/store-profile/#demo-content")
        self.assertNotContains(resp, "Remove demo content")

    def test_remove_button_shown_while_seeded_instead(self):
        self.project.feature_flags = {"onboarded": True, "demo_seeded": True}
        self.project.save(update_fields=["feature_flags"])
        resp = self.client.get("/admin/start/")
        self.assertContains(resp, "Remove demo content")
        self.assertNotContains(resp, "Import demo content")

    def test_import_link_hidden_for_staff_without_manage_role(self):
        staff = User.objects.create_user("hs", "hs@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.project, user=staff, role=StoreRole.STAFF)
        self.client.force_login(staff)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()
        resp = self.client.get("/admin/products/")
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "Import demo content")

    def test_store_profile_page_has_the_anchor_target(self):
        resp = self.client.get("/admin/cms/store-profile/")
        self.assertContains(resp, 'id="demo-content"')
