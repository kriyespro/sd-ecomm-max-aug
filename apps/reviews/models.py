"""Product reviews with moderation (project.md sections 8 features, 13).

A review is project-scoped and tied to a product; optionally to a Customer and
an OrderItem (verified purchase). Aggregate rating is cached on the Product
(``rating_avg`` / ``rating_count``) and refreshed by ``services`` on moderation.
"""

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.core.models import TenantScopedModel, TimeStampedModel


class ReviewStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"


class Review(TenantScopedModel):
    product = models.ForeignKey("catalog.Product", on_delete=models.CASCADE, related_name="reviews")
    customer = models.ForeignKey(
        "customers.Customer", on_delete=models.SET_NULL, null=True, blank=True, related_name="reviews",
    )
    order_item = models.ForeignKey(
        "orders.OrderItem", on_delete=models.SET_NULL, null=True, blank=True, related_name="reviews",
    )
    author_name = models.CharField(max_length=120)
    author_email = models.EmailField(db_index=True)

    rating = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
    title = models.CharField(max_length=160, blank=True)
    body = models.TextField(blank=True)

    status = models.CharField(max_length=12, choices=ReviewStatus.choices, default=ReviewStatus.PENDING, db_index=True)
    is_verified_purchase = models.BooleanField(default=False)
    helpful_count = models.PositiveIntegerField(default=0)
    admin_reply = models.TextField(blank=True)
    moderated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="moderated_reviews",
    )
    moderated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "product", "author_email"], name="uniq_review_per_author_product"
            ),
        ]
        indexes = [
            models.Index(fields=["product", "status"]),
        ]

    def __str__(self):
        return f"{self.product_id} {self.rating}★ {self.author_name}"


class ReviewVote(TimeStampedModel):
    review = models.ForeignKey(Review, on_delete=models.CASCADE, related_name="votes")
    voter_email = models.EmailField()
    is_helpful = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["review", "voter_email"], name="uniq_review_vote"),
        ]

    def __str__(self):
        return f"{self.review_id} {'up' if self.is_helpful else 'down'}"
