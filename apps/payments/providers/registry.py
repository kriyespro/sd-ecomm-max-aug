"""Maps a provider key to its implementation class."""

from .cod import CODProvider
from .manual import ManualProvider
from .razorpay import RazorpayProvider

_PROVIDERS = {
    CODProvider.key: CODProvider,
    ManualProvider.key: ManualProvider,
    RazorpayProvider.key: RazorpayProvider,
}


def get_provider_class(key):
    try:
        return _PROVIDERS[key]
    except KeyError:
        raise ValueError(f"Unknown payment provider: {key}")


def provider_keys():
    return list(_PROVIDERS)
