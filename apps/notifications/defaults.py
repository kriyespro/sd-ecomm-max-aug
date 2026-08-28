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
}
