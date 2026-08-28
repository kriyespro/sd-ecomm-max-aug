"""Cash on delivery. No gateway — money is collected at delivery and the payment
is captured manually from the control panel (``services.capture_payment``).
"""

from decimal import Decimal

from .base import PaymentProvider, ProviderError, WebhookResult


class CODProvider(PaymentProvider):
    key = "cod"
    label = "Cash on delivery"
    instant = False

    def start(self, payment, order, *, context=None):
        return {"provider": self.key, "collect_on_delivery": True, "amount": str(payment.amount)}

    def verify(self, payment, data):
        # Nothing to verify for COD; capture happens on delivery.
        return True

    def parse_webhook(self, headers, body):
        return WebhookResult(signature_valid=False)

    def refund(self, payment, amount: Decimal, *, reason=""):
        # A COD refund is handled off-platform (cash/bank transfer); just record it.
        return ""
