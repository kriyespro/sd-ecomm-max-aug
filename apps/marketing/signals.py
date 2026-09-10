"""Fire server-side conversion events off domain events. Purchase only for now —
the browser pixels cover PageView / ViewContent / InitiateCheckout, and Purchase
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

    from .models import TrackingIntegration, TrackingProvider

    rows = [
        r for r in TrackingIntegration.objects.filter(project=project, is_enabled=True)
        if r.server_ready
    ]
    if not rows:
        return

    order = getattr(instance, "order", None) or instance   # PAYMENT_SUCCESS sends the Payment
    number = payload.get("order_number") or getattr(order, "number", "")
    if not number:
        return

    ship = getattr(order, "shipping_address", None) or {}
    contact = {
        "email": payload.get("email") or getattr(order, "email", ""),
        "phone": getattr(order, "phone", ""),
        "city": ship.get("city", ""), "state": ship.get("state", ""),
        "zip_code": ship.get("postal_code", ""), "country": ship.get("country", ""),
        "external_id": str(getattr(order, "customer_id", "") or ""),
    }
    try:
        items = list(order.items.all())
    except Exception:  # noqa: BLE001
        items = []
    contents = [
        {"id": it.sku or str(it.product_id), "quantity": it.quantity,
         "item_price": float(it.unit_price)}
        for it in items
    ]
    num_items = sum(it.quantity for it in items) or None
    value = float(payload.get("total") or getattr(order, "grand_total", 0) or 0)
    currency = payload.get("currency") or getattr(order, "currency", "INR")
    source_url = (getattr(project, "public_url", "") or "") + f"order/{number}/"

    for row in rows:
        if row.provider == TrackingProvider.META:
            _enqueue_meta(row, number, contact, contents, num_items, value, currency, source_url)
        elif row.provider == TrackingProvider.GA4:
            _enqueue_ga4(row, number, contents, value, currency)
        elif row.provider == TrackingProvider.TIKTOK:
            _enqueue_tiktok(row, number, contact, contents, value, currency, source_url)


def _enqueue_meta(row, number, contact, contents, num_items, value, currency, url):
    from .capi import hash_user_data
    from .tasks import send_meta_capi_event

    user_data = hash_user_data(
        email=contact["email"], phone=contact["phone"],
        city=contact["city"], state=contact["state"],
        zip_code=contact["zip_code"], country=contact["country"],
        external_id=contact["external_id"],
    )
    custom_data = {"currency": currency, "value": value, "order_id": number}
    if contents:
        custom_data["contents"] = contents
        custom_data["content_type"] = "product"
    if num_items:
        custom_data["num_items"] = num_items
    send_meta_capi_event.delay(
        integration_id=row.pk, event_name="Purchase", event_id=number,
        user_data=user_data, custom_data=custom_data, event_source_url=url,
    )


def _enqueue_ga4(row, number, contents, value, currency):
    from .tasks import send_ga4_event

    params = {"currency": currency, "value": value, "transaction_id": number}
    if contents:
        params["items"] = [
            {"item_id": c["id"], "quantity": c["quantity"], "price": c["item_price"]}
            for c in contents
        ]
    send_ga4_event.delay(
        integration_id=row.pk, name="purchase", event_id=number, params=params,
    )


def _enqueue_tiktok(row, number, contact, contents, value, currency, url):
    from .tasks import send_tiktok_event

    properties = {"currency": currency, "value": value, "content_type": "product"}
    if contents:
        properties["contents"] = [
            {"content_id": c["id"], "quantity": c["quantity"], "price": c["item_price"]}
            for c in contents
        ]
    send_tiktok_event.delay(
        integration_id=row.pk, event_name="CompletePayment", event_id=number,
        contact={"email": contact["email"], "phone": contact["phone"],
                 "external_id": contact["external_id"]},
        properties=properties, event_source_url=url,
    )
