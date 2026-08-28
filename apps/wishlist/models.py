"""Customer wishlists (project.md section 13)."""

from django.db import models
from django.utils.crypto import get_random_string

from apps.core.models import TenantScopedModel, TimeStampedModel


class Wishlist(TenantScopedModel):
    customer = models.ForeignKey(
        "customers.Customer", on_delete=models.CASCADE, related_name="wishlists"
    )
    name = models.CharField(max_length=120, default="Wishlist")
    is_public = models.BooleanField(default=False)
    share_token = models.CharField(max_length=32, blank=True, db_index=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.name} <{self.customer_id}>"

    def save(self, *args, **kwargs):
        if self.is_public and not self.share_token:
            self.share_token = get_random_string(24)
        super().save(*args, **kwargs)

    @property
    def item_count(self):
        return self.items.count()


class WishlistItem(TimeStampedModel):
    wishlist = models.ForeignKey(Wishlist, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey("catalog.Product", on_delete=models.CASCADE, related_name="wishlist_items")
    variant = models.ForeignKey(
        "catalog.Variant", on_delete=models.CASCADE, null=True, blank=True, related_name="wishlist_items"
    )
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["wishlist", "product", "variant"], name="uniq_wishlist_item"),
        ]

    def __str__(self):
        return f"{self.product_id} in {self.wishlist_id}"
