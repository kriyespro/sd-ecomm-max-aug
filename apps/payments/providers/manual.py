"""Manual / offline payment. An admin records that money arrived by some means
the platform does not integrate (bank transfer, UPI screenshot, etc.).
"""

from decimal import Decimal

from .base import PaymentProvider, WebhookResult


class ManualProvider(PaymentProvider):
    key = "manual"
    label = "Manual / offline"
    instant = False

    def start(self, payment, order, *, context=None):
        return {"provider": self.key}

    def verify(self, payment, data):
        # Manual payments never self-settle from an untrusted callback. Money is
        # confirmed only by an authenticated staff capture in Mission Control
        # (``services.capture_payment``), so the public verify path must fail.
        return False

    def parse_webhook(self, headers, body):
        return WebhookResult(signature_valid=False)

    def refund(self, payment, amount: Decimal, *, reason=""):
        return ""
