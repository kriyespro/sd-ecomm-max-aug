"""Live Stores showcase: owner submit/approve flow, plus the platform admin's
direct 'add a store' shortcut that skips it."""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.accounts.models import Membership, StoreRole
from apps.projects.models import Project

User = get_user_model()


@override_settings(ALLOWED_HOSTS=["*"])
class ShowcaseAdminAddTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="Acme", status="active", primary_domain="acme.test",
            feature_flags={"onboarded": True},
        )
        self.owner = User.objects.create_user("o", "o@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.project, user=self.owner, role=StoreRole.OWNER)
        self.admin = User.objects.create_superuser("root", "root@t.test", "pw")

    def test_admin_can_add_store_directly(self):
        self.client.force_login(self.admin)
        resp = self.client.post("/admin/showcase/add/", {"project": self.project.pk})
        self.assertEqual(resp.status_code, 302)
        self.project.refresh_from_db()
        self.assertEqual(self.project.showcase_status, Project.ShowcaseStatus.APPROVED)
        self.assertEqual(self.project.showcase_reviewed_by_id, self.admin.pk)

    def test_owner_cannot_add_store_directly(self):
        self.client.force_login(self.owner)
        resp = self.client.post("/admin/showcase/add/", {"project": self.project.pk})
        self.assertEqual(resp.status_code, 403)
        self.project.refresh_from_db()
        self.assertEqual(self.project.showcase_status, Project.ShowcaseStatus.NOT_SUBMITTED)

    def test_store_without_domain_rejected(self):
        self.project.primary_domain = None
        self.project.save(update_fields=["primary_domain", "updated_at"])
        self.client.force_login(self.admin)
        resp = self.client.post("/admin/showcase/add/", {"project": self.project.pk})
        self.assertEqual(resp.status_code, 302)
        self.project.refresh_from_db()
        self.assertEqual(self.project.showcase_status, Project.ShowcaseStatus.NOT_SUBMITTED)

    def test_already_approved_rejected(self):
        self.project.showcase_status = Project.ShowcaseStatus.APPROVED
        self.project.save(update_fields=["showcase_status", "updated_at"])
        self.client.force_login(self.admin)
        resp = self.client.post("/admin/showcase/add/", {"project": self.project.pk})
        self.assertEqual(resp.status_code, 302)

    def test_list_page_shows_addable_dropdown(self):
        self.client.force_login(self.admin)
        resp = self.client.get("/admin/showcase/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Add a store directly")
        self.assertContains(resp, "Acme")
