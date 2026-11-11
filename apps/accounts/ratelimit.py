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


# --- Signup flood brake -----------------------------------------------------
# Unlike login, a signup attempt with a fresh email always "succeeds" (no
# failure to count), so a bot cycling through random addresses never trips
# the brute-force brake above. This counts every registration POST per
# IP+store (not bare IP — shoppers behind a shared/CGNAT IP legitimately
# register at several unrelated stores; a flood only has one store in common).

SIGNUP_WINDOW = 60 * 60    # seconds a signup attempt is remembered
SIGNUP_MAX = 6             # signups from one IP at one store per window before locking
SIGNUP_LOCK_SECONDS = 60 * 60

_SIGNUP_KEY = "signupflood:v1:{}"


def _signup_ident(request, project):
    return f"ip:{_client_ip(request)}|store:{project.pk}"


def signup_is_locked(request, project):
    ident = _signup_ident(request, project)
    try:
        return (cache.get(_SIGNUP_KEY.format(ident)) or 0) >= SIGNUP_MAX
    except Exception:  # noqa: BLE001 - cache down: fail open
        return False


def record_signup_attempt(request, project):
    key = _SIGNUP_KEY.format(_signup_ident(request, project))
    try:
        added = cache.add(key, 1, SIGNUP_WINDOW)
        if not added:
            count = cache.incr(key)
            if count >= SIGNUP_MAX:
                cache.touch(key, SIGNUP_LOCK_SECONDS)
    except Exception:  # noqa: BLE001,S110 - cache hiccup must not break auth
        pass


SIGNUP_LOCK_MESSAGE = (
    "Too many accounts created from this network. Wait a while and try again."
)
