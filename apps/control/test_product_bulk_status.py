"""Bulk activate/deactivate/archive on /admin/products/ — was edit-one-
at-a-time only. One audit-log entry for the whole batch."""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.accounts.models import Membership
from apps.billing import services as billing_svc
from apps.catalog.models import Product
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.core.models import AuditLog
from apps.projects.models import Project

User = get_user_model()


@override_settings(ALLOWED_HOSTS=["*"])
class ProductBulkStatusTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="BulkCo", status="active", feature_flags={"onboarded": True},
        )
        billing_svc.ensure_subscription(self.project)
        self.owner = User.objects.create_user("owner", "owner@t.test", "pw", is_staff=True)
        Membership.objects.create(user=self.owner, project=self.project, role="owner")
        self.client.force_login(self.owner)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()
        self.p1 = Product.objects.create(project=self.project, title="A", slug="a", status="draft", price="10")
        self.p2 = Product.objects.create(project=self.project, title="B", slug="b", status="draft", price="20")

    def test_bulk_activate_updates_all_selected(self):
        resp = self.client.post("/admin/products/bulk-status/", {
            "action": "activate", "pks": [self.p1.pk, self.p2.pk],
        }, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.p1.refresh_from_db()
        self.p2.refresh_from_db()
        self.assertEqual(self.p1.status, "active")
        self.assertEqual(self.p2.status, "active")

    def test_bulk_deactivate(self):
        self.p1.status = "active"
        self.p1.save(update_fields=["status"])
        self.client.post("/admin/products/bulk-status/", {
            "action": "deactivate", "pks": [self.p1.pk],
        })
        self.p1.refresh_from_db()
        self.assertEqual(self.p1.status, "draft")

    def test_one_audit_row_for_the_whole_batch(self):
        before = AuditLog.objects.count()
        self.client.post("/admin/products/bulk-status/", {
            "action": "archive", "pks": [self.p1.pk, self.p2.pk],
        })
        self.assertEqual(AuditLog.objects.count(), before + 1)

    def test_no_selection_is_a_no_op(self):
        resp = self.client.post("/admin/products/bulk-status/", {"action": "activate"}, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.p1.refresh_from_db()
        self.assertEqual(self.p1.status, "draft")

    def test_cannot_touch_another_projects_product(self):
        other = Project.objects.create(name="OtherCo", status="active")
        billing_svc.ensure_subscription(other)
        foreign = Product.objects.create(project=other, title="Foreign", slug="foreign", status="draft", price="5")
        self.client.post("/admin/products/bulk-status/", {
            "action": "activate", "pks": [foreign.pk],
        })
        foreign.refresh_from_db()
        self.assertEqual(foreign.status, "draft")

    def test_list_page_renders_checkboxes_and_toolbar(self):
        resp = self.client.get("/admin/products/")
        self.assertContains(resp, 'name="pks"')
        self.assertContains(resp, 'id="bulk-select-all"')
        self.assertContains(resp, 'value="activate"')

    def test_trash_view_has_no_bulk_toolbar(self):
        resp = self.client.get("/admin/products/?trash=1")
        self.assertNotContains(resp, "bulk-status-form")
