"""Auto platform subdomains — ``<slug>.PLATFORM_BASE_DOMAIN``.

A new store gets one during signup so its storefront is live immediately (the
platform owns a wildcard ``*.<base>`` A record, so the subdomain is verified on
creation — no DNS action by the owner). The owner can rename the slug in the
setup wizard.
"""

import re

from django.conf import settings
from django.utils import timezone

from apps.projects.models import Domain

# Slugs we never hand out — platform infra + confusable names.
RESERVED = {
    "www", "admin", "api", "app", "apps", "mail", "smtp", "imap", "pop",
    "static", "assets", "cdn", "media", "img", "images", "ns", "ns1", "ns2",
    "dns", "mx", "ftp", "shop", "store", "stores", "help", "support", "docs",
    "status", "blog", "news", "dev", "staging", "test", "demo", "beta",
    "dashboard", "billing", "pay", "checkout", "account", "accounts", "login",
    "signup", "auth", "oauth", "webhook", "webhooks", "partners", "go",
}

_SLUG_RE = re.compile(r"[^a-z0-9-]+")
MAX_LEN = 40


def base_domain():
    return (getattr(settings, "PLATFORM_BASE_DOMAIN", "") or "").strip().lower()


def slugify(value):
    s = _SLUG_RE.sub("-", (value or "").strip().lower())
    s = re.sub(r"-{2,}", "-", s).strip("-")[:MAX_LEN].strip("-")
    return s


def host_for(slug):
    return f"{slug}.{base_domain()}" if base_domain() else ""


def is_available(slug, *, project=None):
    if not slug or slug in RESERVED or len(slug) < 2:
        return False
    host = host_for(slug)
    if not host:
        return False
    qs = Domain.objects.filter(host=host)
    if project is not None:
        qs = qs.exclude(project=project)
    return not qs.exists()


def unique_slug(preferred, *, project=None):
    """A free slug near ``preferred`` (email local-part or store name)."""
    base = slugify(preferred) or "store"
    if base in RESERVED or len(base) < 2:
        base = f"{base}-store" if base else "my-store"
    candidate, i = base, 2
    while not is_available(candidate, project=project):
        candidate = f"{base}-{i}"
        i += 1
        if i > 200:  # give up gracefully — practically unreachable
            candidate = f"{base}-{timezone.now().strftime('%H%M%S')}"
            break
    return candidate


def assign(project, slug, *, make_primary=True):
    """Point ``<slug>.<base>`` at ``project`` as a verified Domain, dropping any
    previous platform subdomain for the project. Returns the Domain or ``None``
    when no base domain is configured."""
    base = base_domain()
    if not base:
        return None
    host = host_for(slug)

    Domain.objects.filter(
        project=project, host__endswith=f".{base}"
    ).exclude(host=host).delete()

    domain, _ = Domain.objects.get_or_create(project=project, host=host)
    if not domain.is_verified:
        domain.is_verified = True
        domain.verified_at = domain.verified_at or timezone.now()
    if make_primary:
        Domain.objects.filter(project=project, is_primary=True).exclude(
            pk=domain.pk
        ).update(is_primary=False)
        domain.is_primary = True
    domain.save()

    if make_primary:
        project.primary_domain = host
        project.save(update_fields=["primary_domain", "updated_at"])
    return domain


def current_slug(project):
    """The project's platform-subdomain slug, or ``""``."""
    base = base_domain()
    if not base:
        return ""
    d = (
        Domain.objects.filter(project=project, host__endswith=f".{base}")
        .order_by("-is_primary", "created_at")
        .first()
    )
    return d.host[: -(len(base) + 1)] if d else ""
