from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.accounts.models import Membership, StoreRole
from apps.catalog.models import Product
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.projects.models import Project

User = get_user_model()


class BulkTrashTest(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="BulkCo", status="active", feature_flags={"onboarded": True}
        )
        self.owner = User.objects.create_user("bulkowner", "owner@bulkco.test", "x", is_staff=True)
        Membership.objects.create(project=self.project, user=self.owner, role=StoreRole.OWNER)
        self.staff = User.objects.create_user("bulkstaff", "staff@bulkco.test", "x", is_staff=True)
        Membership.objects.create(project=self.project, user=self.staff, role=StoreRole.STAFF)
        self.products = [
            Product.objects.create(project=self.project, title=f"P{i}", status="active")
            for i in range(3)
        ]

    def _login(self, user):
        self.client.force_login(user)
        session = self.client.session
        session[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        session.save()

    def test_owner_can_bulk_trash(self):
        self._login(self.owner)
        pks = [p.pk for p in self.products]
        resp = self.client.post(
            "/admin/products/bulk-status/", {"action": "trash", "pks": pks}
        )
        self.assertEqual(resp.status_code, 302)
        for p in self.products:
            p.refresh_from_db()
            self.assertIsNotNone(p.trashed_at)
            self.assertEqual(p.status, "archived")
        self.assertNotContains(self.client.get("/admin/products/"), "P0")
        self.assertContains(self.client.get("/admin/products/?trash=1"), "P0")

    def test_staff_cannot_bulk_trash(self):
        self._login(self.staff)
        pks = [p.pk for p in self.products]
        resp = self.client.post(
            "/admin/products/bulk-status/", {"action": "trash", "pks": pks}
        )
        self.assertEqual(resp.status_code, 403)
        for p in self.products:
            p.refresh_from_db()
            self.assertIsNone(p.trashed_at)
