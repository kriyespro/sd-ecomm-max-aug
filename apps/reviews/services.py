"""Review submission, moderation, and cached-rating refresh."""

from decimal import Decimal

from django.db import transaction
from django.db.models import Avg, Count
from django.utils import timezone

from apps.core.models import AuditLog
from apps.core.services import record_audit

from .models import Review, ReviewStatus, ReviewVote


class ReviewError(Exception):
    pass


def _verified_purchase(project, product, email):
    from apps.orders.models import OrderItem

    return (
        OrderItem.objects.filter(
            order__project=project, order__email=email.strip().lower(),
            product=product, order__status__in=["shipped", "delivered"],
        )
        .select_related("order")
        .first()
    )


@transaction.atomic
def submit_review(*, project, product, author_name, author_email, rating, title="", body="", customer=None):
    email = (author_email or "").strip().lower()
    if not email:
        raise ReviewError("Email is required.")
    if not (1 <= int(rating) <= 5):
        raise ReviewError("Rating must be 1–5.")
    if Review.objects.filter(project=project, product=product, author_email=email).exists():
        raise ReviewError("You have already reviewed this product.")

    order_item = _verified_purchase(project, product, email)
    review = Review.objects.create(
        project=project, product=product, customer=customer,
        order_item=order_item, is_verified_purchase=order_item is not None,
        author_name=author_name or "Anonymous", author_email=email,
        rating=int(rating), title=title, body=body,
        status=ReviewStatus.PENDING,
    )
    return review


@transaction.atomic
def moderate_review(*, review, status, actor=None, reply=""):
    status = ReviewStatus(status)
    review.status = status
    review.moderated_by = actor
    review.moderated_at = timezone.now()
    if reply:
        review.admin_reply = reply
    review.save(update_fields=["status", "moderated_by", "moderated_at", "admin_reply", "updated_at"])
    refresh_product_rating(review.product)
    record_audit(actor=actor, project=review.project, action=AuditLog.Action.UPDATE,
                 target=review, changes={"status": status})
    return review


def refresh_product_rating(product):
    agg = product.reviews.filter(status=ReviewStatus.APPROVED).aggregate(
        avg=Avg("rating"), n=Count("id")
    )
    product.rating_avg = Decimal(str(round(agg["avg"] or 0, 2)))
    product.rating_count = agg["n"] or 0
    product.save(update_fields=["rating_avg", "rating_count"])
    return product


@transaction.atomic
def vote_helpful(*, review, voter_email, is_helpful=True):
    vote, created = ReviewVote.objects.get_or_create(
        review=review, voter_email=voter_email.strip().lower(),
        defaults={"is_helpful": is_helpful},
    )
    if not created and vote.is_helpful != is_helpful:
        vote.is_helpful = is_helpful
        vote.save(update_fields=["is_helpful"])
    review.helpful_count = review.votes.filter(is_helpful=True).count()
    review.save(update_fields=["helpful_count"])
    return review
