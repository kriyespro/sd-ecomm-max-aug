"""Courier contract.

Order/shipping orchestration talks to this interface only. A real integration
(Shiprocket, Delhivery, Blue Dart, DTDC) subclasses ``Courier`` and is added to
``registry``. ``ManualCourier`` is the no-integration default: the admin types
the tracking number in.
"""


class CourierError(Exception):
    pass


class TrackingEvent:
    def __init__(self, *, status="", description="", location="", occurred_at=None, raw=None):
        self.status = status
        self.description = description
        self.location = location
        self.occurred_at = occurred_at
        self.raw = raw or {}


class WebhookResult:
    def __init__(self, *, tracking_number="", events=None, signature_valid=False, raw=None):
        self.tracking_number = tracking_number
        self.events = events or []
        self.signature_valid = signature_valid
        self.raw = raw or {}


class Courier:
    key = ""
    label = ""
    #: True when the platform creates the shipment at the courier's API.
    integrated = False

    def __init__(self, config=None):
        self.config = config or {}

    def create_shipment(self, shipment) -> dict:
        """Book the shipment. Return ``{tracking_number, tracking_url, label_url}``."""
        return {}

    def track(self, tracking_number) -> list["TrackingEvent"]:
        return []

    def parse_webhook(self, headers, body: bytes) -> "WebhookResult":
        return WebhookResult(signature_valid=False)

    def cancel(self, shipment) -> None:
        return None
