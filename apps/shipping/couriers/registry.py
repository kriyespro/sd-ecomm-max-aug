"""Maps a courier key to its implementation class."""

from .manual import ManualCourier

_COURIERS = {
    ManualCourier.key: ManualCourier,
}


def get_courier_class(key):
    return _COURIERS.get(key or "manual", ManualCourier)


def courier_keys():
    return list(_COURIERS)
