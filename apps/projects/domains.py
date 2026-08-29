"""Custom-domain onboarding + DNS verification.

Flow (any one of these proves control and flips ``is_verified``):

  1. **TXT**  — owner adds ``_sd-verify.<host>`` = ``sd-verify=<token>``.
  2. **A record** — ``<host>`` resolves straight to ``PLATFORM_PUBLIC_IP``
     (DNS-only / grey cloud). Nothing else to add.
  3. **Cloudflare** — ``<host>`` resolves to Cloudflare edge IPs (orange cloud)
     *and* a public ``GET https://<host>/.well-known/sd-domain-check`` returns
     this domain's token — i.e. the proxied host really routes here.

Only verified domains route traffic (see ``apps.core.middleware``).

Security notes:
- ``host`` is validated against a strict hostname regex before it ever reaches
  the shell; ``dig`` runs with a fixed argv list (shell=False) — no injection.
- The token probe only fires once every A record is a Cloudflare edge IP, so the
  outbound request lands on Cloudflare, never an internal address. The path is
  fixed and the token is already public (it is the TXT challenge value).
- A host already verified by another project cannot be re-added.
"""

import ipaddress
import re
import subprocess
import urllib.error
import urllib.request

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from .models import Domain, validate_hostname

_TXT_TIMEOUT = 6
_PROBE_TIMEOUT = 6
_SAFE_HOST = re.compile(r"^[a-z0-9.-]{1,253}$")

# Cloudflare's published edge ranges — a proxied ("orange cloud") record resolves
# to one of these. Source: https://www.cloudflare.com/ips/
_CLOUDFLARE_NETS = [
    ipaddress.ip_network(n)
    for n in (
        "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22",
        "103.31.4.0/22", "141.101.64.0/18", "108.162.192.0/18",
        "190.93.240.0/20", "188.114.96.0/20", "197.234.240.0/22",
        "198.41.128.0/17", "162.158.0.0/15", "104.16.0.0/13",
        "104.24.0.0/14", "172.64.0.0/13", "131.0.72.0/22",
        "2400:cb00::/32", "2606:4700::/32", "2803:f800::/32",
        "2405:b500::/32", "2405:8100::/32", "2a06:98c0::/29",
        "2c0f:f248::/32",
    )
]


class DomainError(Exception):
    pass


def add_domain(*, project, host, make_primary=False):
    host = (host or "").strip().lower().rstrip(".")
    try:
        validate_hostname(host)
    except ValidationError as exc:
        raise DomainError(exc.messages[0]) from exc

    existing = Domain.objects.filter(host=host).select_related("project").first()
    if existing is not None:
        if existing.project_id == project.pk:
            return existing
        raise DomainError("That domain is already connected to another store.")

    domain = Domain.objects.create(project=project, host=host)
    if make_primary:
        set_primary(domain=domain)
    return domain


def set_primary(*, domain):
    if not domain.is_verified:
        raise DomainError("Verify the domain before making it primary.")
    Domain.objects.filter(project=domain.project, is_primary=True).exclude(pk=domain.pk).update(is_primary=False)
    domain.is_primary = True
    domain.save(update_fields=["is_primary", "updated_at"])
    domain.project.primary_domain = domain.host
    domain.project.save(update_fields=["primary_domain", "updated_at"])
    return domain


def remove_domain(*, domain):
    project = domain.project
    was_primary = domain.is_primary
    domain.delete()
    if was_primary:
        fallback = Domain.objects.filter(project=project, is_verified=True).order_by("created_at").first()
        project.primary_domain = fallback.host if fallback else ""
        project.save(update_fields=["primary_domain", "updated_at"])


def _dig(qtype, name):
    """``dig +short <qtype> <name>`` -> list of answer lines (empty on failure)."""
    if not _SAFE_HOST.match(name):
        return []
    try:
        out = subprocess.run(
            ["dig", "+short", "+time=3", "+tries=1", qtype, name],
            capture_output=True, text=True, timeout=_TXT_TIMEOUT, check=False,
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return []
    return [line.strip().strip('"') for line in out.splitlines() if line.strip()]


def _lookup_txt(name):
    return _dig("TXT", name)


def _lookup_ips(host):
    """Resolved A + AAAA addresses for ``host`` (CNAME lines dropped)."""
    ips = []
    for qtype in ("A", "AAAA"):
        for line in _dig(qtype, host):
            try:
                ipaddress.ip_address(line)
            except ValueError:
                continue
            ips.append(line)
    return ips


def _is_cloudflare_ip(ip):
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in net for net in _CLOUDFLARE_NETS)


def _fetch_domain_check(host):
    """``GET https://<host>/.well-known/sd-domain-check`` -> body (empty on any failure)."""
    if not _SAFE_HOST.match(host):
        return ""
    req = urllib.request.Request(
        f"https://{host}/.well-known/sd-domain-check",
        headers={"User-Agent": "sd-domain-verifier/1"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_PROBE_TIMEOUT) as resp:
            if resp.status != 200:
                return ""
            return resp.read(128).decode("ascii", "ignore").strip()
    except (urllib.error.URLError, OSError, ValueError):
        return ""


def verify_domain(domain):
    """Check DNS / routing and flip ``is_verified`` on the first method that passes."""
    domain.last_checked_at = timezone.now()
    host = domain.host
    method = ""

    # 1. DNS TXT challenge — independent of where the A record points.
    if domain.txt_value in _lookup_txt(domain.txt_name):
        method = "txt"

    if not method:
        server_ip = (getattr(settings, "PLATFORM_PUBLIC_IP", "") or "").strip()
        ips = _lookup_ips(host)
        # 2. A record points straight at this server (DNS-only / grey cloud).
        if server_ip and server_ip in ips:
            method = "a-record"
        # 3. Cloudflare-proxied: confirm the proxied host actually routes here.
        elif ips and all(_is_cloudflare_ip(ip) for ip in ips):
            if _fetch_domain_check(host) == domain.verification_token:
                method = "cloudflare"
            else:
                domain.last_check_error = (
                    "Cloudflare proxy detected, but the domain is not routing to "
                    "this store yet. Keep the record proxied (orange cloud) and "
                    "retry in a minute."
                )
                domain.save(update_fields=[
                    "is_verified", "verified_at", "last_checked_at",
                    "last_check_error", "updated_at",
                ])
                return False

    if method:
        domain.is_verified = True
        domain.verified_at = domain.verified_at or timezone.now()
        domain.last_check_error = ""
    else:
        domain.last_check_error = (
            "No matching TXT record and the domain does not point here yet — "
            "DNS changes can take a few minutes to propagate."
        )
    domain.save(update_fields=[
        "is_verified", "verified_at", "last_checked_at", "last_check_error", "updated_at",
    ])
    return domain.is_verified


def domain_token_for_host(host):
    """Token to serve at ``/.well-known/sd-domain-check`` for ``host`` (or '')."""
    host = (host or "").strip().lower().rstrip(".")
    if not _SAFE_HOST.match(host):
        return ""
    return (
        Domain.objects.filter(host=host)
        .values_list("verification_token", flat=True)
        .first()
        or ""
    )


def domains_for(project):
    return Domain.objects.filter(project=project).order_by("-is_primary", "host")


def assert_can_manage_domains(user, project):
    """Store owner/manager, or platform staff who administer this store."""
    from apps.accounts.permissions import has_store_role

    if has_store_role(user, project, {"owner", "manager"}):
        return
    raise PermissionDenied("Only the store owner or a manager can change domains.")
