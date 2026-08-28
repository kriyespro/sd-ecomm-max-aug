"""Wishlist logic."""

from django.db import transaction

from .models import Wishlist, WishlistItem


def get_or_create_wishlist(*, project, customer, name="Wishlist"):
    wishlist = customer.wishlists.filter(project=project).order_by("id").first()
    if wishlist is None:
        wishlist = Wishlist.objects.create(project=project, customer=customer, name=name)
    return wishlist


@transaction.atomic
def add_item(*, wishlist, product, variant=None, note=""):
    if variant is not None and variant.product_id != product.id:
        raise ValueError("Variant does not belong to product.")
    item, _ = WishlistItem.objects.get_or_create(
        wishlist=wishlist, product=product, variant=variant, defaults={"note": note},
    )
    return item


def remove_item(*, wishlist, product, variant=None):
    wishlist.items.filter(product=product, variant=variant).delete()


def move_to_cart(*, wishlist, cart, product, variant=None, quantity=1):
    from apps.cart import services as cart_svc

    cart_svc.add_to_cart(cart=cart, product=product, variant=variant, quantity=quantity)
    remove_item(wishlist=wishlist, product=product, variant=variant)
