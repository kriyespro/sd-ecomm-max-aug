"""Checkout orchestration.

Thin layer between a cart and an order: validate the cart and the supplied
addresses, then hand off to ``apps.orders.services.place_order``. Payment is a
separate concern (Phase 6) — checkout leaves the order PENDING / unpaid.
"""

from apps.orders import services as orders

REQUIRED_ADDRESS_FIELDS = ("name", "line1", "city", "postal_code", "country")


class CheckoutError(Exception):
    pass


def _validate_address(address, label):
    if not isinstance(address, dict):
        raise CheckoutError(f"{label} address is missing.")
    missing = [f for f in REQUIRED_ADDRESS_FIELDS if not str(address.get(f, "")).strip()]
    if missing:
        raise CheckoutError(f"{label} address is incomplete: {', '.join(missing)}.")


def validate_checkout(*, cart, email, shipping_address, billing_address=None):
    if not cart.items.exists():
        raise CheckoutError("Your cart is empty.")
    if not str(email or "").strip():
        raise CheckoutError("An email address is required.")
    _validate_address(shipping_address, "Shipping")
    if billing_address:
        _validate_address(billing_address, "Billing")


def complete_checkout(
    *, project, cart, email, shipping_address, billing_address=None,
    phone="", customer_note="", warehouse=None, actor=None, user=None,
    payment_method=None, coupon_code=None,
):
    """Create the order. If ``payment_method`` is given, also start payment.

    Returns ``(order, payment_context)``. ``payment_context`` is ``None`` for a
    plain order, a provider client-params dict for a gateway, or ``{}`` for COD.
    """
    validate_checkout(
        cart=cart, email=email, shipping_address=shipping_address,
        billing_address=billing_address,
    )
    order = orders.place_order(
        project=project,
        cart=cart,
        email=email,
        phone=phone,
        billing_address=billing_address or shipping_address,
        shipping_address=shipping_address,
        customer_note=customer_note,
        warehouse=warehouse,
        actor=actor,
        user=user,
    )

    # Link/refresh the customer record (imported here to keep orders decoupled).
    from apps.customers import services as customers
    customers.attach_customer(order, actor=actor)

    if coupon_code:
        from apps.coupons import services as coupons
        order.refresh_from_db()
        try:
            coupons.apply_to_order(order=order, code=coupon_code, actor=actor)
        except coupons.CouponError as exc:
            raise CheckoutError(str(exc)) from exc

    if not payment_method:
        return order, None

    # Imported here so apps.orders / apps.cart never depend on apps.payments.
    from apps.payments import services as payments
    from apps.payments.models import Provider

    if payment_method in {Provider.COD, Provider.MANUAL}:
        payments.record_offline_payment(
            order=order, provider_key=payment_method, actor=actor,
            mark_collected=(payment_method == Provider.MANUAL),
        )
        return order, {}

    _, client_params = payments.initiate_payment(
        order=order, provider_key=payment_method, actor=actor,
    )
    return order, client_params
