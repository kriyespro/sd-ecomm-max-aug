"""Shiprocket integration — apiv2.shiprocket.in/v1/external/.

No SDK, stdlib urllib only (same convention as apps.payments.providers.
razorpay). Auth is email+password -> a bearer token (Shiprocket has no
long-lived API key; the token is fetched fresh per call here for
simplicity — this is a low-volume admin action, not a hot request path).

Test mode (``CourierConfig.is_test_mode``, the default until a store
turns it off) or missing credentials never calls the network: it returns a
synthetic AWB so the flow is fully exercisable before a real account is
wired in, exactly like Razorpay's test-mode order creation.

Field names are per Shiprocket's documented v1 external API as of writing
(auth/login, orders/create/adhoc, courier/assign/awb, courier/track/awb,
orders/cancel) — verify against https://apidocs.shiprocket.in/ if any of
these ever 4xx, gateways do rename fields over time.
"""

import json
import urllib.error
import urllib.request

from django.utils.crypto import get_random_string

from .base import Courier, CourierError, TrackingEvent, WebhookResult

_BASE = "https://apiv2.shiprocket.in/v1/external"


def _post(path, body, *, token=None, timeout=15):
    req = urllib.request.Request(
        f"{_BASE}{path}",
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise CourierError(f"Shiprocket {path} failed ({exc.code}): {detail}") from exc
    except urllib.error.URLError as exc:
        raise CourierError(f"Shiprocket {path} unreachable: {exc.reason}") from exc


def _get(path, *, token, timeout=15):
    req = urllib.request.Request(
        f"{_BASE}{path}", method="GET",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise CourierError(f"Shiprocket {path} failed ({exc.code}): {detail}") from exc
    except urllib.error.URLError as exc:
        raise CourierError(f"Shiprocket {path} unreachable: {exc.reason}") from exc


class ShiprocketCourier(Courier):
    key = "shiprocket"
    label = "Shiprocket"
    integrated = True

    def _test_mode(self):
        creds = getattr(self.config, "credentials", None) or {}
        is_test = getattr(self.config, "is_test_mode", True)
        return is_test or not (creds.get("email") and creds.get("password"))

    def _token(self):
        creds = self.config.credentials or {}
        data = _post("/auth/login", {
            "email": creds.get("email", ""), "password": creds.get("password", ""),
        })
        token = data.get("token")
        if not token:
            raise CourierError("Shiprocket login did not return a token — check the email/password.")
        return token

    def create_shipment(self, shipment) -> dict:
        if self._test_mode():
            awb = f"SRTEST{get_random_string(8, allowed_chars='0123456789')}"
            return {
                "tracking_number": awb,
                "tracking_url": f"https://shiprocket.co/tracking/{awb}",
                "label_url": "",
            }

        order = shipment.order
        addr = order.shipping_address or {}
        token = self._token()
        items = [
            {
                "name": (si.order_item.product_title or "Item")[:120],
                "sku": si.order_item.sku or str(si.order_item.product_id or si.pk),
                "units": si.quantity,
                "selling_price": str(si.order_item.unit_price),
            }
            for si in shipment.items.select_related("order_item").all()
        ] or [{"name": "Order items", "sku": order.number, "units": 1, "selling_price": str(order.grand_total)}]

        created = _post("/orders/create/adhoc", {
            "order_id": f"{order.number}-{shipment.pk}",
            "order_date": order.created_at.strftime("%Y-%m-%d %H:%M"),
            "pickup_location": (self.config.credentials or {}).get("pickup_location", "Primary"),
            "billing_customer_name": addr.get("name", order.email),
            "billing_address": addr.get("line1", ""),
            "billing_city": addr.get("city", ""),
            "billing_pincode": addr.get("postal_code", ""),
            "billing_state": addr.get("state", ""),
            "billing_country": addr.get("country", "India"),
            "billing_email": order.email,
            "billing_phone": addr.get("phone", order.phone or ""),
            "shipping_is_billing": True,
            "order_items": items,
            "payment_method": "COD" if order.payment_status != "paid" else "Prepaid",
            "sub_total": str(order.grand_total),
            "length": 10, "breadth": 10, "height": 10,
            "weight": float(shipment.weight or 1),
        }, token=token)

        shipment_id = created.get("shipment_id")
        awb_code = ""
        if shipment_id:
            assigned = _post("/courier/assign/awb", {"shipment_id": shipment_id}, token=token)
            awb_code = (assigned.get("response", {}).get("data", {}) or {}).get("awb_code", "")

        return {
            "tracking_number": awb_code,
            "tracking_url": f"https://shiprocket.co/tracking/{awb_code}" if awb_code else "",
            "label_url": "",
        }

    def track(self, tracking_number) -> list["TrackingEvent"]:
        if self._test_mode() or not tracking_number:
            return []
        data = _get(f"/courier/track/awb/{tracking_number}", token=self._token())
        track_data = (data.get("tracking_data") or {})
        return [
            TrackingEvent(
                status=e.get("status", ""), description=e.get("activity", ""),
                location=e.get("location", ""), occurred_at=e.get("date"), raw=e,
            )
            for e in (track_data.get("shipment_track_activities") or [])
        ]

    def parse_webhook(self, headers, body: bytes) -> "WebhookResult":
        # Shiprocket's delivery-status webhook has no HMAC signature to verify
        # (unlike a payment gateway) — treat it as informational only until a
        # store actually needs it wired up.
        return WebhookResult(signature_valid=False)

    def cancel(self, shipment) -> None:
        if self._test_mode() or not shipment.tracking_number:
            return
        _post("/orders/cancel", {"ids": [shipment.order.number]}, token=self._token())
