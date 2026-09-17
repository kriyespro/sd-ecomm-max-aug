"""Customer list: CSV export (orders already had this, customers didn't)
and bulk group-assign (was one-at-a-time via the customer detail form)."""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.accounts.models import Membership
from apps.billing import services as billing_svc
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.core.models import AuditLog
from apps.customers.models import Customer, CustomerGroup
from apps.projects.models import Project

User = get_user_model()


@override_settings(ALLOWED_HOSTS=["*"])
class CustomerExportTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="ExportCo", status="active", feature_flags={"onboarded": True},
        )
        billing_svc.ensure_subscription(self.project)
        self.owner = User.objects.create_user("owner", "owner@t.test", "pw", is_staff=True)
        Membership.objects.create(user=self.owner, project=self.project, role="owner")
        self.client.force_login(self.owner)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()
        Customer.objects.create(project=self.project, email="a@t.test", first_name="Ann")
        Customer.objects.create(project=self.project, email="b@t.test", first_name="Bob")

    def test_export_returns_csv_with_all_customers(self):
        resp = self.client.get("/admin/customers/export/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "text/csv")
        body = resp.content.decode()
        self.assertIn("a@t.test", body)
        self.assertIn("b@t.test", body)

    def test_export_respects_search_filter(self):
        resp = self.client.get("/admin/customers/export/?q=Ann")
        body = resp.content.decode()
        self.assertIn("a@t.test", body)
        self.assertNotIn("b@t.test", body)

    def test_staff_cannot_export(self):
        staff = User.objects.create_user("staff1", "staff1@t.test", "pw", is_staff=True)
        Membership.objects.create(user=staff, project=self.project, role="staff")
        self.client.force_login(staff)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()
        resp = self.client.get("/admin/customers/export/")
        self.assertEqual(resp.status_code, 403)

    def test_export_link_shown_to_owner(self):
        resp = self.client.get("/admin/customers/")
        self.assertContains(resp, "Export CSV")


@override_settings(ALLOWED_HOSTS=["*"])
class CustomerBulkGroupAssignTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="GroupCo", status="active", feature_flags={"onboarded": True},
        )
        billing_svc.ensure_subscription(self.project)
        self.owner = User.objects.create_user("owner2", "owner2@t.test", "pw", is_staff=True)
        Membership.objects.create(user=self.owner, project=self.project, role="owner")
        self.client.force_login(self.owner)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()
        self.group = CustomerGroup.objects.create(project=self.project, name="VIP")
        self.c1 = Customer.objects.create(project=self.project, email="c1@t.test")
        self.c2 = Customer.objects.create(project=self.project, email="c2@t.test")

    def test_bulk_assign_to_group(self):
        resp = self.client.post("/admin/customers/bulk-group/", {
            "group": self.group.pk, "pks": [self.c1.pk, self.c2.pk],
        }, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.c1.refresh_from_db()
        self.c2.refresh_from_db()
        self.assertEqual(self.c1.group_id, self.group.pk)
        self.assertEqual(self.c2.group_id, self.group.pk)

    def test_bulk_clear_group_with_blank_option(self):
        Customer.objects.filter(pk=self.c1.pk).update(group=self.group)
        self.client.post("/admin/customers/bulk-group/", {"group": "", "pks": [self.c1.pk]})
        self.c1.refresh_from_db()
        self.assertIsNone(self.c1.group_id)

    def test_one_audit_row_for_the_batch(self):
        before = AuditLog.objects.count()
        self.client.post("/admin/customers/bulk-group/", {
            "group": self.group.pk, "pks": [self.c1.pk, self.c2.pk],
        })
        self.assertEqual(AuditLog.objects.count(), before + 1)

    def test_no_selection_is_a_no_op(self):
        resp = self.client.post("/admin/customers/bulk-group/", {"group": self.group.pk}, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.c1.refresh_from_db()
        self.assertIsNone(self.c1.group_id)

    def test_cannot_touch_another_projects_customer(self):
        other = Project.objects.create(name="OtherGroupCo", status="active")
        billing_svc.ensure_subscription(other)
        foreign = Customer.objects.create(project=other, email="foreign@t.test")
        self.client.post("/admin/customers/bulk-group/", {
            "group": self.group.pk, "pks": [foreign.pk],
        })
        foreign.refresh_from_db()
        self.assertIsNone(foreign.group_id)

    def test_group_from_another_project_is_rejected(self):
        other = Project.objects.create(name="OtherGroupCo2", status="active")
        billing_svc.ensure_subscription(other)
        other_group = CustomerGroup.objects.create(project=other, name="Foreign VIP")
        resp = self.client.post("/admin/customers/bulk-group/", {
            "group": other_group.pk, "pks": [self.c1.pk],
        })
        self.assertEqual(resp.status_code, 404)

    def test_list_page_renders_checkboxes_and_toolbar(self):
        resp = self.client.get("/admin/customers/")
        self.assertContains(resp, 'name="pks"')
        self.assertContains(resp, 'id="bulk-select-all"')
        self.assertContains(resp, "Move to group")

    def test_checkboxes_have_accessible_names(self):
        resp = self.client.get("/admin/customers/")
        self.assertContains(resp, 'aria-label="Select all customers"')
        self.assertContains(resp, 'aria-label="Select c1@t.test"')

    def test_oversized_batch_is_rejected(self):
        from apps.control.mixins import BULK_ACTION_MAX_ROWS

        too_many = [str(self.c1.pk)] + [str(-n) for n in range(1, BULK_ACTION_MAX_ROWS + 1)]
        resp = self.client.post("/admin/customers/bulk-group/", {
            "group": self.group.pk, "pks": too_many,
        }, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "or fewer at a time")
        self.c1.refresh_from_db()
        self.assertIsNone(self.c1.group_id)
