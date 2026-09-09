"""Soft-delete ("Trash") for products and media assets.

A trashed row is hidden from the storefront and the normal admin lists. An
owner/manager can restore it, or delete it for good; a Celery beat task
(:mod:`apps.control.tasks`) hard-deletes anything trashed for more than
``TRASH_RETENTION_DAYS``.
"""

from django.db.models import ProtectedError
from django.utils import timezone

TRASH_RETENTION_DAYS = 30


def trash_product(product) -> None:
    if product.trashed_at is not None:
        return
    product.trashed_from_status = product.status
    product.status = "archived"
    product.trashed_at = timezone.now()
    product.save(update_fields=["status", "trashed_from_status", "trashed_at", "updated_at"])


def restore_product(product) -> None:
    if product.trashed_at is None:
        return
    product.status = product.trashed_from_status or "draft"
    product.trashed_from_status = ""
    product.trashed_at = None
    product.save(update_fields=["status", "trashed_from_status", "trashed_at", "updated_at"])


def purge_product(product) -> bool:
    """Hard delete. Returns False (and leaves the row) when a shopper's cart
    still protects it."""
    from apps.cart.models import CartItem

    CartItem.objects.filter(product=product).delete()
    try:
        product.delete()
    except ProtectedError:
        return False
    return True


def trash_asset(asset) -> None:
    if asset.trashed_at is None:
        asset.trashed_at = timezone.now()
        asset.save(update_fields=["trashed_at", "updated_at"])


def restore_asset(asset) -> None:
    if asset.trashed_at is not None:
        asset.trashed_at = None
        asset.save(update_fields=["trashed_at", "updated_at"])
