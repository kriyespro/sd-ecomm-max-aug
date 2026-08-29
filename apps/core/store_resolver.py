"""Cached Host -> store resolution.

Every request looks up two things that change rarely but would otherwise cost
3-5 DB queries per hit:

* which project serves this ``Host`` (verified custom ``Domain`` or a project's
  ``primary_domain``), and whether that host is the store's *own* dedicated
  domain (drives serving the storefront at ``/`` — see ``StorefrontHostMiddleware``);
* which storefront skin that project renders with.

Both are cached in the shared cache (Redis in production). ``apps.core.signals``
busts the relevant key when a Domain, Project, ThemeSettings or Skin changes.
"""

from django.core.cache import cache

_TTL = 600
_NEG = "\x00"  # sentinel so a "nothing here" answer is cached too

_HOST_KEY = "storebind:v2:{}"
_SKIN_KEY = "skinbind:v2:{}"


# ---- host -> (project_id, is_dedicated_domain) -----------------------------

def binding_for_host(host):
    """``(project_id | None, is_dedicated_domain: bool)`` for ``host``."""
    if not host:
        return (None, False)
    key = _HOST_KEY.format(host)
    hit = cache.get(key)
    if hit is not None:
        return (None, False) if hit == _NEG else tuple(hit)
    val = _lookup_host(host)
    cache.set(key, _NEG if val == (None, False) else list(val), _TTL)
    return val


def _lookup_host(host):
    from apps.projects.models import Domain, Project

    pid = (
        Domain.objects.filter(host=host, is_verified=True)
        .values_list("project_id", flat=True)
        .first()
    )
    if pid:
        return (pid, True)
    pid = (
        Project.objects.filter(primary_domain=host)
        .values_list("id", flat=True)
        .first()
    )
    if pid:
        return (pid, True)
    return (None, False)


def bust_host(host):
    if host:
        cache.delete(_HOST_KEY.format(host))


# ---- project -> skin ------------------------------------------------------

def skin_binding_for_project(project):
    """``(slug: str, skin_id | None, is_sandboxed: bool)`` for ``project``."""
    if project is None:
        return ("default", None, False)
    key = _SKIN_KEY.format(project.pk)
    hit = cache.get(key)
    if hit is not None:
        return tuple(hit)
    from apps.cms.skins import skin_for_project

    skin = skin_for_project(project)
    val = (
        (skin.slug, skin.pk, bool(skin.is_sandboxed))
        if skin is not None
        else ("default", None, False)
    )
    cache.set(key, list(val), _TTL)
    return val


def bust_project_skin(project_id):
    if project_id:
        cache.delete(_SKIN_KEY.format(project_id))


# ---- project -> storefront "chrome" (nav / footer / banners) --------------
#
# base_context() builds these on every storefront page. They are store-wide and
# change only when the owner edits categories, pages, banners, the theme colour
# or a shipping method — all of which bust the key via apps.core.signals.

_CHROME_KEY = "storechrome:v1:{}"


def store_chrome(project):
    if project is None:
        return None
    key = _CHROME_KEY.format(project.pk)
    hit = cache.get(key)
    if hit is not None:
        return hit
    val = _build_chrome(project)
    cache.set(key, val, 300)
    return val


def _build_chrome(project):
    from django.db.models import Min

    from apps.categories.models import Category
    from apps.cms.models import Page, StoreProfile, ThemeSettings
    from apps.shipping.models import ShippingMethod

    theme = (
        ThemeSettings.objects.filter(project=project)
        .only("primary_color", "project_id")
        .first()
    )
    profile = StoreProfile.objects.filter(project=project).first()
    banners = {}
    for b in project.banners.filter(
        placement__in=("announcement", "hero")
    ).order_by("priority"):
        if b.is_live and b.placement not in banners:
            banners[b.placement] = b
    return {
        "accent": theme.primary_color if theme else "#b08d57",
        "profile": profile,
        "store_logo": profile.logo.url if (profile and profile.logo) else "",
        "categories": list(
            Category.objects.filter(project=project, is_active=True)[:10]
        ),
        "footer_pages": [
            p
            for p in Page.objects.filter(project=project).only(
                "title", "slug", "status", "published_at", "kind"
            )
            if p.is_live
        ],
        "announcement": banners.get("announcement"),
        "hero_banner": banners.get("hero"),
        "free_ship_over": ShippingMethod.objects.filter(
            project=project, is_active=True, free_over__isnull=False
        ).aggregate(m=Min("free_over"))["m"],
    }


def bust_project_chrome(project_id):
    if project_id:
        cache.delete(_CHROME_KEY.format(project_id))
