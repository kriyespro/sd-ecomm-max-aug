"""Coupon code field had no way to auto-fill — merchant had to type one by
hand. Client-side "Generate" button next to the field, no server round trip
needed (Coupon.code has no uniqueness constraint to check against)."""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.accounts.models import Membership, StoreRole
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.projects.models import Project

User = get_user_model()


@override_settings(ALLOWED_HOSTS=["*"])
class CouponGenerateCodeButtonTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="CouponCo", status="active", feature_flags={"onboarded": True},
        )
        self.owner = User.objects.create_user("o", "o@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.project, user=self.owner, role=StoreRole.OWNER)
        self.client.force_login(self.owner)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()

    def test_generate_button_wired_to_code_field(self):
        resp = self.client.get("/admin/coupons/new/")
        self.assertContains(resp, 'id="id_code"')
        self.assertContains(resp, "Generate")
        self.assertContains(resp, "generateWired")
