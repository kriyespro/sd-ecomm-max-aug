"""Delhivery integration — track.delhivery.com.

No SDK, stdlib urllib only (same convention as shiprocket.py /
apps.payments.providers.razorpay). Auth is a single long-lived API token
(``Authorization: Token <key>``) — unlike Shiprocket there is no login step.

Test mode (``CourierConfig.is_test_mode``, default True) or a missing token
never calls the network: returns a synthetic waybill so the flow works
before a real account is wired in.

Field names are per Delhivery's documented Cargo/Track API as of writing
(cmu/create.json for booking, api/v1/packages/json for tracking, api/p/edit
for cancel) — verify against Delhivery's partner docs if any of these ever
4xx, gateways do rename fields over time.
"""

import json
import urllib.error
import urllib.parse
import urllib.request

from django.utils.crypto import get_random_string

from .base import Courier, CourierError, TrackingEvent, WebhookResult

_BASE = "https://track.delhivery.com"


def _request(method, path, *, token, data=None, timeout=15):
    url = f"{_BASE}{path}"
    body = None
    headers = {"Authorization": f"Token {token}", "Accept": "application/json"}
    if data is not None:
        body = urllib.parse.urlencode(data).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise CourierError(f"Delhivery {path} failed ({exc.code}): {detail}") from exc
    except urllib.error.URLError as exc:
        raise CourierError(f"Delhivery {path} unreachable: {exc.reason}") from exc


class DelhiveryCourier(Courier):
    key = "delhivery"
    label = "Delhivery"
    integrated = True

    def _token(self):
        return (getattr(self.config, "credentials", None) or {}).get("api_token", "")

    def _test_mode(self):
        is_test = getattr(self.config, "is_test_mode", True)
        return is_test or not self._token()

    def create_shipment(self, shipment) -> dict:
        if self._test_mode():
            wbn = f"DLTEST{get_random_string(8, allowed_chars='0123456789')}"
            return {
                "tracking_number": wbn,
                "tracking_url": f"https://www.delhivery.com/track/package/{wbn}",
                "label_url": "",
            }

        order = shipment.order
        addr = order.shipping_address or {}
        creds = self.config.credentials or {}
        payload = {
            "shipments": [{
                "name": addr.get("name", order.email),
                "add": addr.get("line1", ""),
                "city": addr.get("city", ""),
                "state": addr.get("state", ""),
                "country": addr.get("country", "India"),
                "pin": addr.get("postal_code", ""),
                "phone": addr.get("phone", order.phone or ""),
                "order": order.number,
                "payment_mode": "COD" if order.payment_status != "paid" else "Prepaid",
                "cod_amount": str(order.grand_total) if order.payment_status != "paid" else "0",
                "total_amount": str(order.grand_total),
                "weight": str(float(shipment.weight or 1)),
            }],
            "pickup_location": {"name": creds.get("pickup_location", "Primary")},
        }
        data = _request("POST", "/api/cmu/create.json", token=self._token(), data={
            "format": "json", "data": json.dumps(payload),
        })
        packages = data.get("packages") or []
        wbn = packages[0].get("waybill", "") if packages else ""
        if not wbn:
            raise CourierError(f"Delhivery did not return a waybill: {data}")
        return {
            "tracking_number": wbn,
            "tracking_url": f"https://www.delhivery.com/track/package/{wbn}",
            "label_url": "",
        }

    def track(self, tracking_number) -> list["TrackingEvent"]:
        if self._test_mode() or not tracking_number:
            return []
        data = _request(
            "GET", f"/api/v1/packages/json/?waybill={tracking_number}", token=self._token(),
        )
        shipments = data.get("ShipmentData") or []
        scans = (shipments[0].get("Shipment", {}).get("Scans") or []) if shipments else []
        return [
            TrackingEvent(
                status=(s.get("ScanDetail") or {}).get("Scan", ""),
                description=(s.get("ScanDetail") or {}).get("Instructions", ""),
                location=(s.get("ScanDetail") or {}).get("ScannedLocation", ""),
                occurred_at=(s.get("ScanDetail") or {}).get("ScanDateTime"),
                raw=s,
            )
            for s in scans
        ]

    def parse_webhook(self, headers, body: bytes) -> "WebhookResult":
        # Delhivery's push-update webhook has no HMAC signature to verify —
        # informational only until a store actually needs it wired up.
        return WebhookResult(signature_valid=False)

    def cancel(self, shipment) -> None:
        if self._test_mode() or not shipment.tracking_number:
            return
        _request("POST", "/api/p/edit", token=self._token(), data={
            "waybill": shipment.tracking_number, "cancellation": "true",
        })
