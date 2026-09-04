"""Webhook signing + delivery.

Payloads are signed HMAC-SHA256 over the exact JSON body; receivers verify the
``X-Webhook-Signature: sha256=<hex>`` header. Delivery is attempted inline and
retried with exponential backoff by ``retry_due`` (call from a cron/worker).
"""

import hmac
import ipaddress
import json
import socket
import urllib.error
import urllib.request
from datetime import timedelta
from hashlib import sha256
from urllib.parse import urlsplit

from django.utils import timezone

from .models import DeliveryStatus, WebhookDelivery, WebhookEndpoint

MAX_ATTEMPTS = 6
TIMEOUT = 10
_BACKOFF = [60, 300, 1800, 7200, 21600]  # seconds after attempt 1..5

# Ports that are never a legitimate public webhook receiver — blocking them
# blunts using the delivery as an internal port scanner.
_BLOCKED_PORTS = frozenset({
    22, 23, 25, 111, 135, 139, 445, 1433, 2049, 3306, 3389,
    5432, 5672, 6379, 9200, 9300, 11211, 27017,
})


class WebhookURLError(Exception):
    """The endpoint URL is not a safe outbound target (SSRF guard)."""


def _ip_is_disallowed(ip: "ipaddress._BaseAddress") -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return (
        ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
        or ip.is_reserved or ip.is_unspecified
        or getattr(ip, "is_site_local", False)
    )


def validate_endpoint_url(url: str) -> None:
    """Reject non-http(s) schemes, internal hosts, and odd ports before we make
    an outbound request to a store-supplied URL. Raises ``WebhookURLError``.

    Note: DNS is re-resolved by urllib at connect time, so a rebinding attacker
    could still flip an allowed name to an internal address in the window
    between this check and the socket connect. Redirects are refused (below);
    a fully rebind-proof fix would pin the connection to a validated IP.
    """
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise WebhookURLError("Webhook URL must use http or https.")
    host = parts.hostname
    if not host:
        raise WebhookURLError("Webhook URL has no host.")
    if parts.port in _BLOCKED_PORTS:
        raise WebhookURLError("Webhook URL uses a disallowed port.")
    try:
        infos = socket.getaddrinfo(host, parts.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise WebhookURLError("Webhook host does not resolve.") from exc
    for *_, sockaddr in infos:
        try:
            ip = ipaddress.ip_address(sockaddr[0])
        except ValueError:
            raise WebhookURLError("Webhook host resolves to an invalid address.")
        if _ip_is_disallowed(ip):
            raise WebhookURLError("Webhook URL points to a non-public address.")


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """A redirect to an internal host would bypass ``validate_endpoint_url`` —
    refuse them outright."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(
            req.full_url, code, f"redirect blocked ({newurl})", headers, fp
        )


_opener = urllib.request.build_opener(_NoRedirects)


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

    try:
        validate_endpoint_url(endpoint.url)
    except WebhookURLError as exc:
        # Not retryable — the URL itself is the problem.
        delivery.status = DeliveryStatus.EXHAUSTED
        delivery.next_retry_at = None
        delivery.error = f"blocked: {exc}"[:255]
        delivery.save()
        return delivery

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
        with _opener.open(req, timeout=TIMEOUT) as resp:
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
