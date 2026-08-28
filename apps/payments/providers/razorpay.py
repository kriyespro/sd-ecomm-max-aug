"""Razorpay provider — India first (project.md section 11).

Signature verification is pure stdlib HMAC-SHA256, so callbacks and webhooks are
validated with no third-party SDK. The one operation that needs the network is
creating a remote order; in test mode (or when the API is unreachable) a
synthetic order id is generated so the rest of the flow stays exercisable.

Expected ``credentials``: ``{"key_id": "...", "key_secret": "...",
"webhook_secret": "..."}``.
"""

import base64
import hmac
import json
import urllib.error
import urllib.request
from decimal import Decimal
from hashlib import sha256

from .base import PaymentProvider, ProviderError, WebhookResult

API_ROOT = "https://api.razorpay.com/v1"


def _hmac_sha256(secret: str, message: str) -> str:
    return hmac.new(secret.encode(), message.encode(), sha256).hexdigest()


class RazorpayProvider(PaymentProvider):
    key = "razorpay"
    label = "Razorpay"
    instant = True

    def _key_id(self):
        kid = self.credentials.get("key_id")
        if not kid:
            raise ProviderError("Razorpay key_id is not configured.")
        return kid

    def _key_secret(self):
        secret = self.credentials.get("key_secret")
        if not secret:
            raise ProviderError("Razorpay key_secret is not configured.")
        return secret

    # --- API ------------------------------------------------------

    def _api(self, method, path, payload=None):
        kid, secret = self._key_id(), self._key_secret()
        token = base64.b64encode(f"{kid}:{secret}".encode()).decode()
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(
            f"{API_ROOT}{path}", data=data, method=method,
            headers={"Authorization": f"Basic {token}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())

    # --- lifecycle -----------------------------------------------

    def start(self, payment, order, *, context=None):
        amount_paise = int((payment.amount * 100).to_integral_value())
        body = {
            "amount": amount_paise,
            "currency": payment.currency or "INR",
            "receipt": order.number,
            "notes": {"order": order.number, "project": str(order.project_id)},
        }
        try:
            remote = self._api("POST", "/orders", body)
            payment.provider_order_id = remote.get("id", "")
            payment.meta = {**(payment.meta or {}), "razorpay_order": remote}
        except (urllib.error.URLError, ProviderError, ValueError) as exc:
            if not self.config.is_test_mode:
                raise ProviderError(f"Razorpay order creation failed: {exc}") from exc
            # Test mode: keep going with a synthetic id.
            payment.provider_order_id = f"order_test_{order.number}"
            payment.meta = {**(payment.meta or {}), "razorpay_order": {"synthetic": True}}
        payment.save(update_fields=["provider_order_id", "meta", "updated_at"])
        return {
            "provider": self.key,
            "key_id": self.credentials.get("key_id", ""),
            "razorpay_order_id": payment.provider_order_id,
            "amount": amount_paise,
            "currency": payment.currency or "INR",
            "name": order.project.name,
            "prefill": {"email": order.email, "contact": order.phone},
        }

    def verify(self, payment, data):
        order_id = data.get("razorpay_order_id") or payment.provider_order_id
        payment_id = data.get("razorpay_payment_id", "")
        signature = data.get("razorpay_signature", "")
        if not (order_id and payment_id and signature):
            return False
        expected = _hmac_sha256(self._key_secret(), f"{order_id}|{payment_id}")
        ok = hmac.compare_digest(expected, signature)
        if ok:
            payment.provider_payment_id = payment_id
            payment.provider_signature = signature
        return ok

    def parse_webhook(self, headers, body: bytes) -> WebhookResult:
        secret = self.credentials.get("webhook_secret", "")
        sent_sig = headers.get("X-Razorpay-Signature", "") or headers.get("x-razorpay-signature", "")
        valid = False
        if secret and sent_sig:
            expected = _hmac_sha256(secret, body.decode("utf-8", "replace"))
            valid = hmac.compare_digest(expected, sent_sig)

        try:
            data = json.loads(body.decode("utf-8"))
        except ValueError:
            return WebhookResult(signature_valid=valid)

        event_type = data.get("event", "")
        entity = (
            data.get("payload", {}).get("payment", {}).get("entity", {})
            or data.get("payload", {}).get("refund", {}).get("entity", {})
        )
        return WebhookResult(
            event_type=event_type,
            provider_payment_id=entity.get("id", "") if event_type.startswith("payment") else entity.get("payment_id", ""),
            provider_order_id=entity.get("order_id", ""),
            provider_refund_id=entity.get("id", "") if event_type.startswith("refund") else "",
            signature_valid=valid,
            raw=data,
        )

    def refund(self, payment, amount: Decimal, *, reason=""):
        amount_paise = int((amount * 100).to_integral_value())
        try:
            remote = self._api(
                "POST", f"/payments/{payment.provider_payment_id}/refund",
                {"amount": amount_paise, "notes": {"reason": reason or ""}},
            )
            return remote.get("id", "")
        except (urllib.error.URLError, ProviderError, ValueError) as exc:
            if not self.config.is_test_mode:
                raise ProviderError(f"Razorpay refund failed: {exc}") from exc
            return f"rfnd_test_{payment.pk}"
