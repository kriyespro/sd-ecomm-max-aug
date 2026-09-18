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

    def test_list_page_renders_compact_table(self):
        self.project.showcase_status = Project.ShowcaseStatus.APPROVED
        self.project.save(update_fields=["showcase_status", "updated_at"])
        self.client.force_login(self.admin)
        resp = self.client.get("/admin/showcase/")
        self.assertContains(resp, "<table")
        self.assertContains(resp, "<th class=\"px-4 py-2\">Store</th>")


@override_settings(ALLOWED_HOSTS=["*"])
class ShowcaseRemoveTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="Acme", status="active", primary_domain="acme.test",
            feature_flags={"onboarded": True},
            showcase_status=Project.ShowcaseStatus.APPROVED,
        )
        self.owner = User.objects.create_user("o", "o@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.project, user=self.owner, role=StoreRole.OWNER)
        self.admin = User.objects.create_superuser("root", "root@t.test", "pw")

    def test_admin_can_remove_approved_store(self):
        self.client.force_login(self.admin)
        resp = self.client.post(f"/admin/showcase/{self.project.pk}/remove/")
        self.assertEqual(resp.status_code, 302)
        self.project.refresh_from_db()
        self.assertEqual(self.project.showcase_status, Project.ShowcaseStatus.NOT_SUBMITTED)
        self.assertIsNone(self.project.showcase_submitted_at)
        self.assertIsNone(self.project.showcase_reviewed_by)
        self.assertIsNone(self.project.showcase_reviewed_at)
        self.assertEqual(self.project.showcase_review_note, "")

    def test_owner_cannot_remove_store(self):
        self.client.force_login(self.owner)
        resp = self.client.post(f"/admin/showcase/{self.project.pk}/remove/")
        self.assertEqual(resp.status_code, 403)
        self.project.refresh_from_db()
        self.assertEqual(self.project.showcase_status, Project.ShowcaseStatus.APPROVED)

    def test_cannot_remove_a_store_that_isnt_approved(self):
        self.project.showcase_status = Project.ShowcaseStatus.PENDING
        self.project.save(update_fields=["showcase_status", "updated_at"])
        self.client.force_login(self.admin)
        resp = self.client.post(f"/admin/showcase/{self.project.pk}/remove/")
        self.assertEqual(resp.status_code, 302)
        self.project.refresh_from_db()
        self.assertEqual(self.project.showcase_status, Project.ShowcaseStatus.PENDING)

    def test_remove_writes_audit_log(self):
        from apps.core.models import AuditLog

        self.client.force_login(self.admin)
        self.client.post(f"/admin/showcase/{self.project.pk}/remove/")
        entry = AuditLog.objects.filter(
            action=AuditLog.Action.UPDATE, changes__decision="admin_remove",
        ).latest("created_at")
        self.assertEqual(entry.actor_id, self.admin.pk)

    def test_list_page_shows_remove_button_only_for_approved(self):
        pending = Project.objects.create(
            name="Pending Co", status="active", primary_domain="pending.test",
            feature_flags={"onboarded": True}, showcase_status=Project.ShowcaseStatus.PENDING,
        )
        self.client.force_login(self.admin)
        resp = self.client.get("/admin/showcase/")
        self.assertContains(resp, f'/admin/showcase/{self.project.pk}/remove/')
        self.assertNotContains(resp, f'/admin/showcase/{pending.pk}/remove/')
