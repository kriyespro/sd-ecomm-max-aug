"""Direct UPI (store owner's own UPI ID + QR). No gateway: the shopper pays the
owner straight from any UPI app, then submits the UTR / reference. Money is
confirmed only by an authenticated staff capture in Mission Control, so this
provider never self-settles from a public callback.
"""

import re
from decimal import Decimal
from urllib.parse import quote, urlencode

from .base import PaymentProvider, WebhookResult

# handle@bank — letters/digits/dot/dash/underscore, 2+ char bank handle.
UPI_ID_RE = re.compile(r"^[A-Za-z0-9.\-_]{2,256}@[A-Za-z][A-Za-z0-9.\-]{1,63}$")
UTR_RE = re.compile(r"^[A-Za-z0-9]{8,30}$")


class UPIProvider(PaymentProvider):
    key = "upi"
    label = "UPI (QR / direct)"
    instant = False

    @property
    def upi_id(self):
        return (self.credentials.get("upi_id") or "").strip()

    @property
    def payee_name(self):
        return (self.options.get("payee_name") or "").strip()

    def intent_url(self, *, amount, order_number, currency="INR"):
        """``upi://pay`` deep link — opens the shopper's UPI app on mobile and is
        also what the generated QR encodes (amount + order ref pre-filled)."""
        params = {
            "pa": self.upi_id,
            "pn": self.payee_name or "Store",
            "am": f"{Decimal(amount):.2f}",
            "cu": currency,
            "tn": f"Order {order_number}",
        }
        return "upi://pay?" + urlencode(params, quote_via=quote)

    def start(self, payment, order, *, context=None):
        return {"provider": self.key, "upi_id": self.upi_id}

    def verify(self, payment, data):
        return False

    def parse_webhook(self, headers, body):
        return WebhookResult(signature_valid=False)

    def refund(self, payment, amount: Decimal, *, reason=""):
        # Refund is sent by the owner from their own UPI app; recorded only.
        return ""
