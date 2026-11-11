"""Security audit finding: coupon create/update/delete had no role gate at
all -- unlike every other money-adjacent admin action (credits, loyalty
tiers, segment broadcast, payment providers), a plain STAFF login could
create or delete coupons. Now OWNER_MANAGER only, matching that convention."""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.accounts.models import Membership, StoreRole
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.coupons.models import Coupon
from apps.projects.models import Project

User = get_user_model()


@override_settings(ALLOWED_HOSTS=["*"])
class CouponRoleGateTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="CouponGateCo", status="active", feature_flags={"onboarded": True},
        )
        self.staff = User.objects.create_user("staffer", "staffer@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.project, user=self.staff, role=StoreRole.STAFF)
        self.client.force_login(self.staff)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()

    def test_staff_cannot_reach_coupon_create_form(self):
        resp = self.client.get("/admin/coupons/new/")
        self.assertEqual(resp.status_code, 403)

    def test_staff_cannot_create_a_coupon(self):
        resp = self.client.post("/admin/coupons/new/", {
            "code": "STAFFCODE", "discount_type": "percent", "value": "10",
        })
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(Coupon.objects.filter(project=self.project, code="STAFFCODE").exists())

    def test_staff_cannot_delete_a_coupon(self):
        coupon = Coupon.objects.create(project=self.project, code="KEEPME", discount_type="percent", value=10)
        resp = self.client.post(f"/admin/coupons/{coupon.pk}/delete/")
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(Coupon.objects.filter(pk=coupon.pk).exists())

    def test_owner_can_still_create_a_coupon(self):
        owner = User.objects.create_user("owner2", "owner2@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.project, user=owner, role=StoreRole.OWNER)
        self.client.force_login(owner)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()
        resp = self.client.post("/admin/coupons/new/", {
            "code": "OWNERCODE", "discount_type": "percent", "value": "10",
            "min_order_amount": "0", "buy_quantity": "1", "get_quantity": "1",
            "get_discount_percent": "100", "applies_to": "all",
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(Coupon.objects.filter(project=self.project, code="OWNERCODE").exists())
