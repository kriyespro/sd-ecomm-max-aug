"""Built-in templates used when a project has not overridden an event.

Bodies are formatted with ``str.format(**context)`` — keep placeholders simple.
"""

DEFAULTS = {
    "order_confirmation": (
        "Your order {order_number} is confirmed",
        "Hi {name},\n\nThanks for your order {order_number}.\n"
        "Total: {currency} {total}\n\nWe'll email you when it ships.",
    ),
    "payment_confirmation": (
        "Payment received for {order_number}",
        "Hi {name},\n\nWe received your payment of {currency} {total} for order {order_number}.",
    ),
    "shipment": (
        "Your order {order_number} has shipped",
        "Hi {name},\n\nOrder {order_number} is on its way.\n"
        "Carrier: {carrier}\nTracking: {tracking}",
    ),
    "delivery": (
        "Your order {order_number} was delivered",
        "Hi {name},\n\nOrder {order_number} has been delivered. Enjoy!",
    ),
    "order_cancelled": (
        "Order {order_number} cancelled",
        "Hi {name},\n\nYour order {order_number} has been cancelled."
        " Any payment will be refunded.",
    ),
    "refund": (
        "Refund processed for {order_number}",
        "Hi {name},\n\nA refund of {currency} {amount} for order {order_number} has been processed.",
    ),
    "welcome": (
        "Welcome to {store_name}",
        "Hi {name},\n\nThanks for creating an account at {store_name}.",
    ),
    "password_reset": (
        "Reset your {store_name} password",
        "Use this link to reset your password: {reset_url}",
    ),
    "low_stock_alert": (
        "Low stock: {product}",
        "{product} at {warehouse} is low: {available} left (threshold {threshold}).",
    ),
    "cart_abandoned": (
        "You left something in your cart at {store_name}",
        "Hi {name},\n\nYou still have {item_count} item(s) worth {currency} {total} "
        "waiting in your cart at {store_name}.\n\nCome back and finish checking out: "
        "{cart_url}",
    ),
    "trial_ending": (
        "Your {store_name} trial ends in {days_left} day(s)",
        "Hi {name},\n\nYour free trial of {store_name} on the {plan_name} plan ends in "
        "{days_left} day(s). Add a payment method from Mission Control to keep your "
        "store live without interruption.",
    ),
    "gift_card_issued": (
        "You've received a {currency} {value} gift card for {store_name}",
        "Hi there,\n\nYou've been sent a {currency} {value} gift card for {store_name}.\n\n"
        "Code: {code}\n\nSign in or create an account and enter this code on your "
        "account page to add it as store credit.",
    ),
    "return_requested": (
        "Return/exchange requested for {order_number}",
        "A {kind} request has come in for order {order_number}.\n\nReason: {reason}\n\n"
        "Review and approve or reject it from Mission Control.",
    ),
    "return_approved": (
        "Your {kind} request for {order_number} was approved",
        "Hi {name},\n\nYour {kind} request for order {order_number} has been approved.\n"
        "{resolution_note}",
    ),
    "return_rejected": (
        "Your {kind} request for {order_number} was declined",
        "Hi {name},\n\nYour {kind} request for order {order_number} could not be approved.\n"
        "{resolution_note}",
    ),
}
