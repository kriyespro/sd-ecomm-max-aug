"""Core services. Business logic lives here, never in views or templates."""

from django.conf import settings
from django.utils.http import url_has_allowed_host_and_scheme

from .models import AuditLog


def absolute_url(request, path):
    """Build an absolute URL for a platform-level email link (account
    verification, team invite) that may be sent from a Celery task with no
    live request — falls back to ``PLATFORM_BASE_DOMAIN``."""
    if request is not None:
        return request.build_absolute_uri(path)
    host = getattr(settings, "PLATFORM_BASE_DOMAIN", "") or "localhost"
    return f"https://{host}{path}"


def safe_next(request, next_url, fallback):
    """Validate a ``?next=``/``next`` form value before handing it to
    ``redirect()`` — an unchecked one is an open redirect. ``fallback`` is a
    URL name (reversed) or a path, returned as-is when ``next_url`` is
    missing, external, or scheme-mismatched (e.g. ``javascript:``)."""
    if next_url and url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return next_url
    return fallback


def record_audit(*, actor=None, project=None, action, target=None, changes=None, request=None):
    """Write an :class:`AuditLog` row.

    ``target`` may be any model instance; its type and pk are stored.
    """
    target_type = ""
    target_id = ""
    if target is not None:
        target_type = f"{target._meta.app_label}.{target._meta.model_name}"
        target_id = str(target.pk)

    ip = None
    if request is not None:
        ip = request.META.get("REMOTE_ADDR")

    return AuditLog.objects.create(
        actor=actor,
        project=project,
        action=action,
        target_type=target_type,
        target_id=target_id,
        changes=changes or {},
        ip_address=ip,
    )
