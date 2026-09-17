"""Bulk approve/reject on review moderation — was one click per review.
Loops moderate_review() per row (not a raw bulk .update()) since a review
touches its product's rating_avg/rating_count aggregate on every status
change, and a batch can span several different products."""

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
class ReviewBulkModerateTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="BulkRevCo", status="active", feature_flags={"onboarded": True},
        )
        self.owner = User.objects.create_user("rowner2", "rowner2@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.project, user=self.owner, role=StoreRole.OWNER)
        self.p1 = Product.objects.create(project=self.project, title="Mug", slug="mug-b", price=Decimal("100"), status="active")
        self.p2 = Product.objects.create(project=self.project, title="Cup", slug="cup-b", price=Decimal("50"), status="active")
        self.r1 = Review.objects.create(
            project=self.project, product=self.p1, rating=5,
            author_name="A", author_email="a@t.test", status=ReviewStatus.PENDING,
        )
        self.r2 = Review.objects.create(
            project=self.project, product=self.p2, rating=3,
            author_name="B", author_email="b@t.test", status=ReviewStatus.PENDING,
        )
        self.client.force_login(self.owner)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()

    def test_bulk_approve_updates_all_selected(self):
        resp = self.client.post("/admin/reviews/bulk-moderate/", {
            "status": "approved", "pks": [self.r1.pk, self.r2.pk],
        }, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.r1.refresh_from_db()
        self.r2.refresh_from_db()
        self.assertEqual(self.r1.status, ReviewStatus.APPROVED)
        self.assertEqual(self.r2.status, ReviewStatus.APPROVED)

    def test_bulk_approve_refreshes_each_products_rating(self):
        """The whole reason this isn't a raw bulk .update() — spans two
        different products, both must get their aggregate recalculated."""
        self.client.post("/admin/reviews/bulk-moderate/", {
            "status": "approved", "pks": [self.r1.pk, self.r2.pk],
        })
        self.p1.refresh_from_db()
        self.p2.refresh_from_db()
        self.assertEqual(self.p1.rating_count, 1)
        self.assertEqual(self.p1.rating_avg, Decimal("5.00"))
        self.assertEqual(self.p2.rating_count, 1)
        self.assertEqual(self.p2.rating_avg, Decimal("3.00"))

    def test_bulk_reject(self):
        self.client.post("/admin/reviews/bulk-moderate/", {
            "status": "rejected", "pks": [self.r1.pk],
        })
        self.r1.refresh_from_db()
        self.assertEqual(self.r1.status, ReviewStatus.REJECTED)

    def test_no_selection_is_a_no_op(self):
        resp = self.client.post("/admin/reviews/bulk-moderate/", {"status": "approved"}, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.r1.refresh_from_db()
        self.assertEqual(self.r1.status, ReviewStatus.PENDING)

    def test_cannot_touch_another_projects_review(self):
        other = Project.objects.create(name="OtherRevCo", status="active")
        other_product = Product.objects.create(project=other, title="X", slug="x-rev", price=Decimal("1"), status="active")
        foreign = Review.objects.create(
            project=other, product=other_product, rating=1,
            author_name="Z", author_email="z@t.test", status=ReviewStatus.PENDING,
        )
        self.client.post("/admin/reviews/bulk-moderate/", {
            "status": "approved", "pks": [foreign.pk],
        })
        foreign.refresh_from_db()
        self.assertEqual(foreign.status, ReviewStatus.PENDING)

    def test_list_page_renders_checkboxes_and_toolbar(self):
        resp = self.client.get("/admin/reviews/?status=pending")
        self.assertContains(resp, 'name="pks"')
        self.assertContains(resp, 'id="bulk-select-all"')
        self.assertContains(resp, "Approve selected")

    def test_checkboxes_have_accessible_names(self):
        resp = self.client.get("/admin/reviews/?status=pending")
        self.assertContains(resp, 'aria-label="Select all reviews"')
        self.assertContains(resp, 'aria-label="Select review by A"')
