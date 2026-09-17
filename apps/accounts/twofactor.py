"""TOTP two-factor auth for every Mission Control account.

Mandatory for anyone who can reach ``/admin/`` — platform admin, store
owner, manager, staff, DGC (in this codebase that's exactly ``User.is_staff``,
kept in sync with store membership by ``apps.accounts.team``). Enforced by
``TwoFactorEnforcementMiddleware``: an account without a confirmed TOTP
secret is redirected to setup on every ``/admin/`` request until they
finish it. Storefront shoppers are never touched — they don't have
``is_staff`` and never hit this gate.

Codes are checked with ``pyotp`` (30s window, ±1 step of clock drift
tolerated). Backup codes are single-use, stored hashed with Django's own
password hasher — never plaintext, never logged, shown to the admin exactly
once at generation time.
"""

import base64
import io
import secrets

import pyotp
import qrcode
from django.contrib.auth.hashers import check_password, make_password
from django.utils import timezone

ISSUER = "Mission Control"
BACKUP_CODE_COUNT = 10

# A brand-new account (self-signup or affiliate join, both Google-only)
# isn't forced into 2FA setup mid-signup — the very first thing they'd see
# would be a security wall before they've even looked at the product.
# grant_signup_grace() marks *this session only* exempt from
# TwoFactorEnforcementMiddleware; logging out (which flushes the session)
# or a fresh sign-in on another device/session doesn't carry the grace, so
# the very next real login is where setup becomes mandatory — "set it up
# after signup, on your next login," not "never."
_GRACE_SESSION_KEY = "2fa_signup_grace"


def grant_signup_grace(request) -> None:
    request.session[_GRACE_SESSION_KEY] = True


def has_signup_grace(request) -> bool:
    return bool(request.session.get(_GRACE_SESSION_KEY))


def is_enabled(user) -> bool:
    profile = getattr(user, "profile", None)
    return bool(profile and profile.totp_enabled and profile.totp_secret)


def generate_secret() -> str:
    return pyotp.random_base32()


def provisioning_uri(user, secret: str) -> str:
    return pyotp.totp.TOTP(secret).provisioning_uri(name=user.email or user.get_username(), issuer_name=ISSUER)


def qr_data_uri(uri: str) -> str:
    """A scannable QR code for ``uri``, rendered server-side (no third-party
    request — the secret never leaves the server) as an inline data: URI."""
    img = qrcode.make(uri, box_size=6, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def verify_totp(secret: str, code: str) -> bool:
    code = (code or "").strip().replace(" ", "")
    if not secret or not code:
        return False
    try:
        return pyotp.totp.TOTP(secret).verify(code, valid_window=1)
    except Exception:  # noqa: BLE001 - malformed input, not a valid code
        return False


def generate_backup_codes(n: int = BACKUP_CODE_COUNT) -> list[str]:
    """Plaintext codes to show the admin once. Caller must hash+store via
    ``hash_backup_codes`` — these are never persisted as-is."""
    return [f"{secrets.token_hex(4)}" for _ in range(n)]


def hash_backup_codes(codes: list[str]) -> list[str]:
    return [make_password(c) for c in codes]


def consume_backup_code(profile, code: str) -> bool:
    """Check ``code`` against the profile's stored hashes; if it matches,
    remove that hash (single use) and save. Returns whether it matched."""
    code = (code or "").strip().replace(" ", "").replace("-", "")
    if not code:
        return False
    for hashed in list(profile.backup_codes or []):
        if check_password(code, hashed):
            profile.backup_codes = [h for h in profile.backup_codes if h != hashed]
            profile.save(update_fields=["backup_codes", "updated_at"])
            return True
    return False


def enable(profile, *, secret: str, code: str) -> list[str] | None:
    """Confirm setup with a real code from the authenticator app. On
    success, enables 2FA and returns the plaintext backup codes to show
    once; on a bad code, returns None and nothing is persisted."""
    if not verify_totp(secret, code):
        return None
    plain_codes = generate_backup_codes()
    profile.totp_secret = secret
    profile.totp_enabled = True
    profile.totp_confirmed_at = timezone.now()
    profile.backup_codes = hash_backup_codes(plain_codes)
    profile.save(update_fields=[
        "totp_secret", "totp_enabled", "totp_confirmed_at", "backup_codes", "updated_at",
    ])
    return plain_codes


def reset(profile) -> None:
    """Recovery path: a platform admin clears a locked-out account's 2FA
    (device + backup codes both lost) so they can set it up again from
    scratch. Only a platform admin may trigger this — see
    apps.control.services.reset_two_factor for the authorization check."""
    profile.totp_secret = ""
    profile.totp_enabled = False
    profile.totp_confirmed_at = None
    profile.backup_codes = []
    profile.save(update_fields=[
        "totp_secret", "totp_enabled", "totp_confirmed_at", "backup_codes", "updated_at",
    ])
