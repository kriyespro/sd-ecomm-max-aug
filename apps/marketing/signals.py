"""Fire server-side conversion events off domain events. Purchase only for now —
the browser Pixel covers PageView / ViewContent / InitiateCheckout, and Purchase
is the one ad-blockers most often kill, so it gets the server path too (deduped
against the browser event by ``event_id`` = the order number)."""

import logging

from django.dispatch import receiver

from apps.core.events import Events, domain_event

logger = logging.getLogger(__name__)


@receiver(domain_event)
def _on_domain_event(sender, event, project, payload, instance=None, **kwargs):
    if event != Events.PAYMENT_SUCCESS or project is None:
        return

    from .capi import hash_user_data
    from .models import TrackingIntegration, TrackingProvider
    from .tasks import send_meta_capi_event

    row = (
        TrackingIntegration.objects
        .filter(project=project, provider=TrackingProvider.META, is_enabled=True)
        .first()
    )
    if row is None or not row.server_ready:
        return

    order = getattr(instance, "order", None) or instance   # PAYMENT_SUCCESS sends the Payment
    number = payload.get("order_number") or getattr(order, "number", "")
    if not number:
        return

    ship = getattr(order, "shipping_address", None) or {}
    user_data = hash_user_data(
        email=payload.get("email") or getattr(order, "email", ""),
        phone=getattr(order, "phone", ""),
        city=ship.get("city", ""), state=ship.get("state", ""),
        zip_code=ship.get("postal_code", ""), country=ship.get("country", ""),
        external_id=str(getattr(order, "customer_id", "") or ""),
    )
    try:
        contents = [
            {"id": it.sku or str(it.product_id), "quantity": it.quantity,
             "item_price": float(it.unit_price)}
            for it in order.items.all()
        ]
        num_items = sum(it.quantity for it in order.items.all())
    except Exception:  # noqa: BLE001
        contents, num_items = [], None

    custom_data = {
        "currency": payload.get("currency") or getattr(order, "currency", "INR"),
        "value": float(payload.get("total") or getattr(order, "grand_total", 0) or 0),
        "order_id": number,
    }
    if contents:
        custom_data["contents"] = contents
        custom_data["content_type"] = "product"
    if num_items:
        custom_data["num_items"] = num_items

    send_meta_capi_event.delay(
        integration_id=row.pk,
        event_name="Purchase",
        event_id=number,
        user_data=user_data,
        custom_data=custom_data,
        event_source_url=(project.public_url or "") + f"order/{number}/",
    )
