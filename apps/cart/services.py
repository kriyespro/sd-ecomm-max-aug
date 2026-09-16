"""Cart business logic. Views/APIs call these, never manipulate rows directly."""

from datetime import timedelta
from decimal import Decimal

from django.db import models, transaction

from apps.catalog.models import Product, Variant

from .models import Cart, CartItem


class EmptyCart:
    """Stand-in for "this visitor has no cart yet", used by read-only storefront
    renders so a browsing anonymous visitor never creates a Cart row (or, up in
    the view layer, a session cookie). Exposes just what the templates read."""

    pk = None
    id = None
    email = ""
    is_active = True
    _is_empty = True
    item_count = 0
    subtotal = Decimal("0.00")

    def __init__(self, project):
        self.project = project
        self.items = CartItem.objects.none()


def _line_price(product: Product, variant: Variant | None) -> Decimal:
    if variant is not None:
        return variant.effective_price
    return product.current_price


def get_or_create_cart(*, project, user=None, session_key="", email="", create=True):
    """Return the active cart for this project + identity.

    ``create=False`` returns an unsaved, empty ``Cart`` when the visitor has no
    cart yet — used by read-only storefront page renders so a browsing
    anonymous visitor never spawns a Cart row (or, upstream, a session cookie),
    which keeps the response cacheable at the edge.
    """
    qs = Cart.objects.filter(project=project, is_active=True)
    cart = None
    if user is not None and user.is_authenticated:
        cart = qs.filter(user=user).order_by("-created_at").first()
    elif session_key:
        cart = qs.filter(session_key=session_key, user__isnull=True).order_by("-created_at").first()

    if cart is None:
        if not create:
            return EmptyCart(project)
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
def merge_session_cart_into_user(*, project, user, session_key):
    """Fold an anonymous session's cart into the account's own cart right after
    login/registration — otherwise whatever the shopper added while browsing
    anonymously silently vanishes the moment they sign in (the view switches to
    looking up the cart by ``user``, never ``session_key``, again)."""
    if not session_key or user is None or not user.is_authenticated:
        return
    session_cart = (
        Cart.objects.select_for_update()
        .filter(project=project, is_active=True, session_key=session_key, user__isnull=True)
        .first()
    )
    if session_cart is None or not session_cart.items.exists():
        return

    user_cart = (
        Cart.objects.select_for_update()
        .filter(project=project, is_active=True, user=user)
        .order_by("-created_at")
        .first()
    )
    if user_cart is None:
        # No cart of their own yet — the session cart just becomes theirs.
        session_cart.user = user
        session_cart.session_key = ""
        session_cart.save(update_fields=["user", "session_key", "updated_at"])
        return

    for item in session_cart.items.select_related("product", "variant"):
        existing = CartItem.objects.select_for_update().filter(
            cart=user_cart, product=item.product, variant=item.variant
        ).first()
        if existing is not None:
            existing.quantity += item.quantity
            existing.save(update_fields=["quantity", "updated_at"])
        else:
            item.cart = user_cart
            item.save(update_fields=["cart", "updated_at"])
    session_cart.is_active = False
    session_cart.save(update_fields=["is_active", "updated_at"])


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


def recoverable_carts(*, idle_hours=3, max_age_days=14):
    """Carts worth sending an abandoned-cart email for right now: active,
    never converted, has at least one item, idle at least ``idle_hours``
    (but not older than ``max_age_days`` — a truly stale cart is dead, not
    "abandoned"), never already recovered, and belongs to a *known* email —
    a registered shopper's account, or ``Cart.email`` if some other flow set
    it. A guest cart with no email captured anywhere can't be reached; that's
    a real gap, not a bug here.
    """
    from django.utils import timezone

    now = timezone.now()
    cutoff = now - timedelta(hours=idle_hours)
    floor = now - timedelta(days=max_age_days)
    return (
        Cart.objects.filter(
            is_active=True, converted_order_id__isnull=True,
            recovery_sent_at__isnull=True,
            updated_at__lte=cutoff, updated_at__gte=floor,
        )
        .filter(models.Q(user__isnull=False) | ~models.Q(email=""))
        .exclude(items__isnull=True)
        .select_related("project", "user")
        .distinct()
    )


def mark_recovery_sent(cart):
    from django.utils import timezone

    cart.recovery_sent_at = timezone.now()
    cart.save(update_fields=["recovery_sent_at", "updated_at"])
