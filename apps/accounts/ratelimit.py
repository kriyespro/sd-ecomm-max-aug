"""Cache-backed brute-force brake for the browser login forms.

The DRF API login is already throttled (``apps.api.throttling.AuthThrottle``);
the HTML forms — Mission Control (`/accounts/login/`) and the storefront customer
login — were not. ``authenticate()`` runs against the single global user table,
so an un-limited storefront login on any tenant is a password-guessing oracle
for platform-owner / superuser accounts.

Counters live in the shared cache (Redis in prod, LocMem in dev). A lockout is
best-effort: if the cache is unavailable the check fails open rather than
locking everyone out.
"""

from django.core.cache import cache

WINDOW = 15 * 60        # seconds a failure is remembered
MAX_FAILURES = 8        # failures in the window before the lock trips
LOCK_SECONDS = 15 * 60  # how long the lock holds once tripped

_KEY = "loginfail:v1:{}"


def _client_ip(request):
    return request.META.get("REMOTE_ADDR", "") or "unknown"


def _idents(request, username):
    ip = _client_ip(request)
    user = (username or "").strip().lower()[:150]
    idents = [f"ip:{ip}"]
    if user:
        idents.append(f"user:{user}|ip:{ip}")
    return idents


def is_locked(request, username):
    for ident in _idents(request, username):
        try:
            if (cache.get(_KEY.format(ident)) or 0) >= MAX_FAILURES:
                return True
        except Exception:  # noqa: BLE001 - cache down: fail open
            return False
    return False


def record_failure(request, username):
    for ident in _idents(request, username):
        key = _KEY.format(ident)
        try:
            added = cache.add(key, 1, WINDOW)
            if not added:
                count = cache.incr(key)
                if count >= MAX_FAILURES:
                    cache.touch(key, LOCK_SECONDS)
        except Exception:  # noqa: BLE001,S110 - cache hiccup must not break auth
            pass


def clear(request, username):
    for ident in _idents(request, username):
        try:
            cache.delete(_KEY.format(ident))
        except Exception:  # noqa: BLE001,S110 - cache hiccup must not break auth
            pass


LOCK_MESSAGE = (
    "Too many failed sign-in attempts. Wait a few minutes and try again."
)
