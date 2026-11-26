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


def base_domain(project=None, brand=None):
    """The apex a store's auto subdomain hangs off: the store's brand's
    ``base_domain`` (``project.brand``), else the default platform's."""
    from apps.core import brand as brands

    if brand is None:
        brand = brands.brand_by_key(getattr(project, "brand", "") if project is not None else "")
    return (brand.base_domain or "").strip().lower()


def slugify(value):
    s = _SLUG_RE.sub("-", (value or "").strip().lower())
    s = re.sub(r"-{2,}", "-", s).strip("-")[:MAX_LEN].strip("-")
    return s


def host_for(slug, base=None):
    base = base_domain() if base is None else base
    return f"{slug}.{base}" if base else ""


def is_available(slug, *, project=None, base=None):
    if not slug or slug in RESERVED or len(slug) < 2:
        return False
    host = host_for(slug, base if base is not None else base_domain(project))
    if not host:
        return False
    qs = Domain.objects.filter(host=host)
    if project is not None:
        qs = qs.exclude(project=project)
    return not qs.exists()


def unique_slug(preferred, *, project=None, base=None):
    """A free slug near ``preferred`` (email local-part or store name)."""
    stem = slugify(preferred) or "store"
    if stem in RESERVED or len(stem) < 2:
        stem = f"{stem}-store" if stem else "my-store"
    candidate, i = stem, 2
    while not is_available(candidate, project=project, base=base):
        candidate = f"{stem}-{i}"
        i += 1
        if i > 200:  # give up gracefully — practically unreachable
            candidate = f"{stem}-{timezone.now().strftime('%H%M%S')}"
            break
    return candidate


def assign(project, slug, *, make_primary=True):
    """Point ``<slug>.<base>`` at ``project`` as a verified Domain, demoting
    (not deleting) any previous platform subdomain for the project. Returns
    the Domain or ``None`` when no base domain is configured.

    The old subdomain row is kept, verified, resolvable -- only demoted to
    non-primary below. Deleting it outright used to mean any search engine or
    backlink still pointing at the renamed-away host hit a dead URL (no
    project bound -> generic marketing-site fallback, no redirect, no
    continuity). Keeping it means the old host keeps serving the real store
    (whose own canonical tag already points at the new host, so ranking
    signal consolidates there), and ``is_available()`` keeps treating that
    slug as taken so a different store can't immediately reuse a URL that
    may still have inbound links.
    """
    base = base_domain(project)
    if not base:
        return None
    host = host_for(slug, base)

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
    base = base_domain(project)
    if not base:
        return ""
    d = (
        Domain.objects.filter(project=project, host__endswith=f".{base}")
        .order_by("-is_primary", "created_at")
        .first()
    )
    return d.host[: -(len(base) + 1)] if d else ""
