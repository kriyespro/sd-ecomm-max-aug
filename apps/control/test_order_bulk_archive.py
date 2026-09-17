"""Bulk archive/unarchive on the order list — was one row at a time.
Same checkbox+toolbar shape as the product bulk-status action."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.accounts.models import Membership
from apps.billing import services as billing_svc
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.core.models import AuditLog
from apps.orders.models import Order
from apps.projects.models import Project

User = get_user_model()


@override_settings(ALLOWED_HOSTS=["*"])
class OrderBulkArchiveTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="BulkOrderCo", status="active", feature_flags={"onboarded": True},
        )
        billing_svc.ensure_subscription(self.project)
        self.owner = User.objects.create_user("owner", "owner@t.test", "pw", is_staff=True)
        Membership.objects.create(user=self.owner, project=self.project, role="owner")
        self.client.force_login(self.owner)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()
        self.o1 = Order.objects.create(
            project=self.project, number="BO-1", email="a@t.test", grand_total=Decimal("100"),
        )
        self.o2 = Order.objects.create(
            project=self.project, number="BO-2", email="b@t.test", grand_total=Decimal("200"),
        )

    def test_bulk_archive_updates_all_selected(self):
        resp = self.client.post("/admin/orders/bulk-archive/", {
            "action": "archive", "pks": [self.o1.pk, self.o2.pk],
        }, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.o1.refresh_from_db()
        self.o2.refresh_from_db()
        self.assertTrue(self.o1.is_archived)
        self.assertTrue(self.o2.is_archived)
        self.assertIsNotNone(self.o1.archived_at)

    def test_bulk_unarchive(self):
        Order.objects.filter(pk=self.o1.pk).update(is_archived=True)
        self.client.post("/admin/orders/bulk-archive/", {
            "action": "unarchive", "pks": [self.o1.pk],
        })
        self.o1.refresh_from_db()
        self.assertFalse(self.o1.is_archived)
        self.assertIsNone(self.o1.archived_at)

    def test_one_audit_row_for_the_whole_batch(self):
        before = AuditLog.objects.count()
        self.client.post("/admin/orders/bulk-archive/", {
            "action": "archive", "pks": [self.o1.pk, self.o2.pk],
        })
        self.assertEqual(AuditLog.objects.count(), before + 1)

    def test_no_selection_is_a_no_op(self):
        resp = self.client.post("/admin/orders/bulk-archive/", {"action": "archive"}, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.o1.refresh_from_db()
        self.assertFalse(self.o1.is_archived)

    def test_cannot_touch_another_projects_order(self):
        other = Project.objects.create(name="OtherOrderCo", status="active")
        billing_svc.ensure_subscription(other)
        foreign = Order.objects.create(project=other, number="OO-1", email="x@t.test", grand_total=Decimal("5"))
        self.client.post("/admin/orders/bulk-archive/", {
            "action": "archive", "pks": [foreign.pk],
        })
        foreign.refresh_from_db()
        self.assertFalse(foreign.is_archived)

    def test_list_page_renders_checkboxes_and_toolbar(self):
        resp = self.client.get("/admin/orders/")
        self.assertContains(resp, 'name="pks"')
        self.assertContains(resp, 'id="bulk-select-all"')
        self.assertContains(resp, "Archive selected")

    def test_staff_cannot_bulk_archive(self):
        staff = User.objects.create_user("staff1", "staff1@t.test", "pw", is_staff=True)
        Membership.objects.create(user=staff, project=self.project, role="staff")
        self.client.force_login(staff)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()
        resp = self.client.post("/admin/orders/bulk-archive/", {
            "action": "archive", "pks": [self.o1.pk],
        })
        self.assertEqual(resp.status_code, 403)
