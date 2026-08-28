"""Cart business logic. Views/APIs call these, never manipulate rows directly."""

from decimal import Decimal

from django.db import transaction

from apps.catalog.models import Product, Variant

from .models import Cart, CartItem


def _line_price(product: Product, variant: Variant | None) -> Decimal:
    if variant is not None:
        return variant.effective_price
    return product.current_price


def get_or_create_cart(*, project, user=None, session_key="", email=""):
    """Return the active cart for this project + identity, creating one if needed."""
    qs = Cart.objects.filter(project=project, is_active=True)
    cart = None
    if user is not None and user.is_authenticated:
        cart = qs.filter(user=user).order_by("-created_at").first()
    elif session_key:
        cart = qs.filter(session_key=session_key, user__isnull=True).order_by("-created_at").first()

    if cart is None:
        cart = Cart.objects.create(
            project=project,
            user=user if (user is not None and user.is_authenticated) else None,
            session_key=session_key or "",
            email=email or "",
        )
    elif email and not cart.email:
        cart.email = email
        cart.save(update_fields=["email", "updated_at"])
    return cart


@transaction.atomic
def add_to_cart(*, cart, product, variant=None, quantity=1):
    if variant is not None and variant.product_id != product.id:
        raise ValueError("Variant does not belong to product.")
    quantity = max(1, int(quantity))
    item, created = CartItem.objects.select_for_update().get_or_create(
        cart=cart,
        product=product,
        variant=variant,
        defaults={"quantity": quantity, "unit_price": _line_price(product, variant)},
    )
    if not created:
        item.quantity += quantity
        item.save(update_fields=["quantity", "updated_at"])
    return item


@transaction.atomic
def set_quantity(*, cart, item, quantity):
    quantity = int(quantity)
    if quantity <= 0:
        item.delete()
        return None
    item.quantity = quantity
    item.save(update_fields=["quantity", "updated_at"])
    return item


def remove_item(*, cart, item):
    item.delete()


def clear_cart(cart):
    cart.items.all().delete()


def cart_summary(cart):
    return {
        "subtotal": cart.subtotal,
        "item_count": cart.item_count,
        "currency": cart.project.currency,
    }
