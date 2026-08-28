"""Manual courier: no API. The admin creates shipments and enters tracking
numbers by hand; status updates come from the control panel, not a webhook.
"""

from .base import Courier


class ManualCourier(Courier):
    key = "manual"
    label = "Manual / offline"
    integrated = False

    def create_shipment(self, shipment) -> dict:
        return {
            "tracking_number": shipment.tracking_number,
            "tracking_url": shipment.tracking_url,
            "label_url": "",
        }
