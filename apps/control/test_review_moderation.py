from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.accounts.models import Membership, StoreRole
from apps.catalog.models import Product
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.projects.models import Project
from apps.reviews.models import Review, ReviewStatus

User = get_user_model()


@override_settings(ALLOWED_HOSTS=["*"])
class ReviewModerateButtonTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="RevCo", status="active",
                                              feature_flags={"onboarded": True})
        self.owner = User.objects.create_user("rowner", "rowner@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.project, user=self.owner, role=StoreRole.OWNER)
        self.product = Product.objects.create(project=self.project, title="Mug", slug="mug-rev",
                                              price=Decimal("100"), status="active")
        self.review = Review.objects.create(
            project=self.project, product=self.product, rating=4,
            author_name="A", author_email="a@t.test", status=ReviewStatus.PENDING, body="nice",
        )
        self.client.force_login(self.owner)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()

    def test_approve_button(self):
        resp = self.client.post(f"/admin/reviews/{self.review.pk}/moderate/",
                                {"status": "approved", "reply": "thanks"})
        self.assertEqual(resp.status_code, 302)
        self.review.refresh_from_db()
        self.assertEqual(self.review.status, ReviewStatus.APPROVED)
        self.assertEqual(self.review.admin_reply, "thanks")
        self.product.refresh_from_db()
        self.assertEqual(self.product.rating_count, 1)

    def test_reject_button(self):
        resp = self.client.post(f"/admin/reviews/{self.review.pk}/moderate/",
                                {"status": "rejected"})
        self.assertEqual(resp.status_code, 302)
        self.review.refresh_from_db()
        self.assertEqual(self.review.status, ReviewStatus.REJECTED)

    def test_list_page_renders(self):
        resp = self.client.get("/admin/reviews/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Approve")
        self.assertContains(resp, "moderate")
