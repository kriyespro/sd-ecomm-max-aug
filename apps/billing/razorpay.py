"""Minimal Razorpay client for platform subscription invoices.

Separate from the store-scoped ``apps.payments`` provider: this uses the
platform's own Razorpay account (``BillingSettings``) and only needs order
creation + signature verification. stdlib HMAC — no SDK.
"""

import base64
import hmac
import json
import urllib.error
import urllib.request
from decimal import Decimal
from hashlib import sha256

_API = "https://api.razorpay.com/v1"


class RazorpayError(Exception):
    pass


def _hmac(secret: str, message: str) -> str:
    return hmac.new(secret.encode(), message.encode(), sha256).hexdigest()


def create_order(*, amount: Decimal, receipt: str, notes: dict, settings) -> dict:
    """Create a Razorpay order for an invoice. Returns the checkout params.

    In test mode (or when the API is unreachable) a synthetic order id is
    returned so the flow stays exercisable without live keys.
    """
    paise = int((Decimal(amount) * 100).to_integral_value())
    body = json.dumps({
        "amount": paise, "currency": settings.currency or "INR",
        "receipt": receipt[:40], "notes": notes,
    }).encode()

    kid, secret = settings.razorpay_key_id, settings.razorpay_key_secret
    if kid and secret:
        token = base64.b64encode(f"{kid}:{secret}".encode()).decode()
        req = urllib.request.Request(
            f"{_API}/orders", data=body, method="POST",
            headers={"Authorization": f"Basic {token}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                remote = json.loads(resp.read().decode())
            return {"order_id": remote["id"], "amount": paise, "currency": settings.currency,
                    "key_id": kid, "synthetic": False}
        except (urllib.error.URLError, KeyError, ValueError) as exc:
            if not settings.is_test_mode:
                raise RazorpayError(f"Razorpay order creation failed: {exc}") from exc

    return {"order_id": f"order_test_{receipt}", "amount": paise,
            "currency": settings.currency, "key_id": kid, "synthetic": True}


def verify_payment_signature(*, order_id: str, payment_id: str, signature: str, secret: str) -> bool:
    if not (order_id and payment_id and signature and secret):
        return False
    expected = _hmac(secret, f"{order_id}|{payment_id}")
    return hmac.compare_digest(expected, signature)


def verify_webhook(*, body: bytes, signature: str, secret: str) -> bool:
    if not (signature and secret):
        return False
    expected = _hmac(secret, body.decode("utf-8", "replace"))
    return hmac.compare_digest(expected, signature)
