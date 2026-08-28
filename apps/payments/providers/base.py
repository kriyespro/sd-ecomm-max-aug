"""Payment provider contract.

A provider is instantiated with its :class:`PaymentProviderConfig` and knows how
to: hand the frontend what it needs to start a payment, verify the callback,
parse a webhook, and issue a refund. Order/inventory logic never touches these
classes directly — it goes through ``apps.payments.services``.
"""

from decimal import Decimal


class ProviderError(Exception):
    """Raised for provider misconfiguration or a failed gateway operation."""


class WebhookResult:
    def __init__(self, *, event_type="", provider_payment_id="", provider_order_id="",
                 provider_refund_id="", signature_valid=False, raw=None):
        self.event_type = event_type
        self.provider_payment_id = provider_payment_id
        self.provider_order_id = provider_order_id
        self.provider_refund_id = provider_refund_id
        self.signature_valid = signature_valid
        self.raw = raw or {}


class PaymentProvider:
    key = ""
    label = ""
    #: True when a payment settles the moment the order is placed (no gateway
    #: round-trip). COD/manual set this False (collected later); gateways True.
    instant = True

    def __init__(self, config):
        self.config = config

    @property
    def credentials(self):
        return self.config.credentials or {}

    @property
    def options(self):
        return self.config.config or {}

    # --- lifecycle ---------------------------------------------------

    def start(self, payment, order, *, context=None):
        """Return a dict the frontend uses to launch checkout.

        May call the gateway to create a remote order and set
        ``payment.provider_order_id``. Default: nothing to do.
        """
        return {}

    def verify(self, payment, data):
        """Validate the client callback. Return True on success.

        ``data`` is the untrusted dict posted back by the frontend.
        """
        raise NotImplementedError

    def parse_webhook(self, headers, body: bytes) -> "WebhookResult":
        raise NotImplementedError

    def refund(self, payment, amount: Decimal, *, reason=""):
        """Issue a refund at the gateway. Return a provider refund id (str)."""
        raise NotImplementedError
