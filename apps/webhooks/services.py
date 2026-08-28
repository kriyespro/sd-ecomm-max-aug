"""Webhook signing + delivery.

Payloads are signed HMAC-SHA256 over the exact JSON body; receivers verify the
``X-Webhook-Signature: sha256=<hex>`` header. Delivery is attempted inline and
retried with exponential backoff by ``retry_due`` (call from a cron/worker).
"""

import hmac
import json
import urllib.error
import urllib.request
from datetime import timedelta
from hashlib import sha256

from django.utils import timezone

from .models import DeliveryStatus, WebhookDelivery, WebhookEndpoint

MAX_ATTEMPTS = 6
TIMEOUT = 10
_BACKOFF = [60, 300, 1800, 7200, 21600]  # seconds after attempt 1..5


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, sha256).hexdigest()


def trigger(*, project, event, data):
    """Create + attempt a delivery for every subscribed endpoint."""
    deliveries = []
    for endpoint in WebhookEndpoint.objects.filter(project=project, is_active=True):
        if not endpoint.wants(event):
            continue
        delivery = WebhookDelivery.objects.create(
            project=project, endpoint=endpoint, event=event,
            payload={"event": event, "created_at": timezone.now().isoformat(), "data": data},
        )
        _attempt(delivery)
        deliveries.append(delivery)
    return deliveries


def _attempt(delivery: WebhookDelivery):
    endpoint = delivery.endpoint
    body = json.dumps(delivery.payload, sort_keys=True, default=str).encode()
    signature = sign(endpoint.secret, body)
    delivery.signature = signature
    delivery.attempts += 1

    req = urllib.request.Request(
        endpoint.url, data=body, method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Webhook-Event": delivery.event,
            "X-Webhook-Signature": signature,
            "X-Webhook-Delivery": str(delivery.pk),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            delivery.response_status = resp.status
            delivery.response_body = resp.read(2048).decode("utf-8", "replace")
        delivery.status = DeliveryStatus.SUCCESS
        delivery.delivered_at = timezone.now()
        delivery.next_retry_at = None
        delivery.error = ""
    except urllib.error.HTTPError as exc:
        delivery.response_status = exc.code
        delivery.response_body = exc.read(2048).decode("utf-8", "replace") if hasattr(exc, "read") else ""
        _mark_retry(delivery, f"HTTP {exc.code}")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        _mark_retry(delivery, str(exc)[:255])

    delivery.save()
    return delivery


def _mark_retry(delivery, error):
    delivery.error = error
    if delivery.attempts >= MAX_ATTEMPTS:
        delivery.status = DeliveryStatus.EXHAUSTED
        delivery.next_retry_at = None
    else:
        delivery.status = DeliveryStatus.FAILED
        wait = _BACKOFF[min(delivery.attempts - 1, len(_BACKOFF) - 1)]
        delivery.next_retry_at = timezone.now() + timedelta(seconds=wait)


def retry_delivery(delivery):
    if delivery.status in {DeliveryStatus.SUCCESS}:
        return delivery
    return _attempt(delivery)


def retry_due(limit=100):
    now = timezone.now()
    due = WebhookDelivery.objects.filter(
        status=DeliveryStatus.FAILED, next_retry_at__lte=now
    ).select_related("endpoint")[:limit]
    return [_attempt(d) for d in due]
