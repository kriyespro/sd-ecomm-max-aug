"""Maps a courier key to its implementation class."""

from .delhivery import DelhiveryCourier
from .manual import ManualCourier
from .shiprocket import ShiprocketCourier

_COURIERS = {
    ManualCourier.key: ManualCourier,
    ShiprocketCourier.key: ShiprocketCourier,
    DelhiveryCourier.key: DelhiveryCourier,
}


def get_courier_class(key):
    return _COURIERS.get(key or "manual", ManualCourier)


def courier_keys():
    return list(_COURIERS)
