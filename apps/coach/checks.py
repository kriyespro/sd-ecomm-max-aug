"""Auto-detected missions: ``auto_key`` -> how many times the store did it today.

Each checker takes ``(project, start, end)`` — the store's local day as a UTC
datetime range — and returns an int compared against ``Mission.target``.
"""

from django.db.models import Count, Q


def _products_added(project, start, end):
    from apps.catalog.models import Product

    return Product.objects.filter(project=project, created_at__gte=start, created_at__lt=end).count()


def _products_with_photos(project, start, end, *, min_images=4):
    from apps.catalog.models import Product

    return (
        Product.objects.filter(project=project, created_at__gte=start, created_at__lt=end)
        .annotate(n=Count("images"))
        .filter(n__gte=min_images)
        .count()
    )


def _products_updated(project, start, end):
    from apps.catalog.models import Product

    return Product.objects.filter(project=project, updated_at__gte=start, updated_at__lt=end).count()


def _coupons_created(project, start, end):
    from apps.coupons.models import Coupon

    return Coupon.objects.filter(project=project, created_at__gte=start, created_at__lt=end).count()


def _orders_cleared(project, start, end):
    """1 when no paid order is still waiting to be packed/shipped — nothing to
    do counts as done, so the task never nags a store with no backlog."""
    from apps.orders.models import Order

    waiting = Order.objects.filter(
        project=project, is_archived=False,
        status__in=["confirmed", "processing", "packed"],
        fulfillment_status__in=["unfulfilled", "partial"],
    ).exists()
    return 0 if waiting else 1


def _reviews_cleared(project, start, end):
    from apps.reviews.models import Review

    return 0 if Review.objects.filter(project=project, status="pending").exists() else 1


def _banner_refreshed(project, start, end):
    from apps.cms.models import Banner

    return Banner.objects.filter(project=project).filter(
        Q(created_at__gte=start, created_at__lt=end) | Q(updated_at__gte=start, updated_at__lt=end)
    ).count()


# key -> (label shown to the superadmin, checker, default CTA label, default CTA url name)
REGISTRY = {
    "products_added": ("New products added today", _products_added, "Add a product", "control:product_create"),
    "products_with_photos": (
        "New products added today with 4+ photos each", _products_with_photos,
        "Add a product", "control:product_create",
    ),
    "products_updated": ("Products edited today (price, stock, photos)", _products_updated,
                         "Open products", "control:product_list"),
    "coupons_created": ("Coupons created today", _coupons_created, "Create a coupon", "control:coupon_list"),
    "orders_cleared": ("No paid orders waiting to ship", _orders_cleared, "Open orders", "control:order_list"),
    "reviews_cleared": ("No reviews waiting for approval", _reviews_cleared, "Open reviews", "control:review_list"),
    "banner_refreshed": ("Banner added or edited today", _banner_refreshed, "Open banners", "control:cms_banners"),
}

AUTO_CHOICES = [("", "Owner ticks it manually")] + [(k, v[0]) for k, v in REGISTRY.items()]
