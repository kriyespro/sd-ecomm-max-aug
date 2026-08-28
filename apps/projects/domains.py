"""Custom-domain onboarding + DNS verification.

Flow:
  1. add_domain(project, host)  -> Domain(is_verified=False) with a token
  2. owner adds a TXT record   _sd-verify.<host>  =  sd-verify=<token>
  3. verify_domain(domain)      -> dig TXT lookup; flips is_verified on match

Only verified domains route traffic (see apps.core.middleware).

Security notes:
- ``host`` is validated against a strict hostname regex before it ever reaches
  the shell, and dig is invoked with a fixed argv list (shell=False), so there
  is no command-injection surface.
- A host already claimed (verified) by another project cannot be re-added.
"""

import re
import subprocess

from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from .models import Domain, validate_hostname

_TXT_TIMEOUT = 6
_SAFE_HOST = re.compile(r"^[a-z0-9.-]{1,253}$")


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


def _lookup_txt(name):
    """Return the list of TXT strings for ``name`` (empty on any failure)."""
    if not _SAFE_HOST.match(name):
        return []
    try:
        out = subprocess.run(
            ["dig", "+short", "+time=3", "+tries=1", "TXT", name],
            capture_output=True, text=True, timeout=_TXT_TIMEOUT, check=False,
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return []
    values = []
    for line in out.splitlines():
        line = line.strip().strip('"')
        if line:
            values.append(line)
    return values


def verify_domain(domain):
    """Check the TXT record and flip ``is_verified`` on success."""
    domain.last_checked_at = timezone.now()
    found = _lookup_txt(domain.txt_name)
    ok = domain.txt_value in found

    if ok:
        domain.is_verified = True
        domain.verified_at = domain.verified_at or timezone.now()
        domain.last_check_error = ""
    else:
        domain.last_check_error = (
            "TXT record not found yet — DNS can take a few minutes to propagate."
            if not found else
            "A TXT record exists but does not match the expected value."
        )
    domain.save(update_fields=[
        "is_verified", "verified_at", "last_checked_at", "last_check_error", "updated_at",
    ])
    return domain.is_verified


def domains_for(project):
    return Domain.objects.filter(project=project).order_by("-is_primary", "host")


def assert_can_manage_domains(user, project):
    """Store owner/manager, or platform staff who administer this store."""
    from apps.accounts.permissions import has_store_role

    if has_store_role(user, project, {"owner", "manager"}):
        return
    raise PermissionDenied("Only the store owner or a manager can change domains.")
