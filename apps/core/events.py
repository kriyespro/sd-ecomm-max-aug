"""Domain event bus.

App services call :func:`emit` at meaningful moments; notifications, webhooks and
analytics attach receivers. This keeps those cross-cutting concerns out of the
core order/payment/shipping logic while avoiding hard imports between apps.

Event keys match project.md section 22:
    order.created / order.updated / order.cancelled
    payment.success / payment.failed / payment.refunded
    shipment.created / shipment.delivered
    customer.created / product.updated / inventory.low
"""

from django.dispatch import Signal

# kwargs: event (str), project (Project), payload (dict), instance (model | None)
domain_event = Signal()


class Events:
    ORDER_CREATED = "order.created"
    ORDER_UPDATED = "order.updated"
    ORDER_CANCELLED = "order.cancelled"
    PAYMENT_SUCCESS = "payment.success"
    PAYMENT_FAILED = "payment.failed"
    PAYMENT_REFUNDED = "payment.refunded"
    SHIPMENT_CREATED = "shipment.created"
    SHIPMENT_DELIVERED = "shipment.delivered"
    CUSTOMER_CREATED = "customer.created"
    PRODUCT_UPDATED = "product.updated"
    INVENTORY_LOW = "inventory.low"

    ALL = [
        ORDER_CREATED, ORDER_UPDATED, ORDER_CANCELLED,
        PAYMENT_SUCCESS, PAYMENT_FAILED, PAYMENT_REFUNDED,
        SHIPMENT_CREATED, SHIPMENT_DELIVERED,
        CUSTOMER_CREATED, PRODUCT_UPDATED, INVENTORY_LOW,
    ]


def emit(event, *, project, payload, instance=None):
    """Fire a domain event. Receiver failures are swallowed so a downstream
    integration can never break the transaction that produced the event.
    """
    # send_robust: a raising receiver is logged, not propagated.
    domain_event.send_robust(
        sender=None, event=event, project=project, payload=payload or {}, instance=instance
    )
