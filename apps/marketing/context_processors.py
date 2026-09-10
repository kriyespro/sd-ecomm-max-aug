"""Inject the platform marketing-site pixels into every page that extends
``base.jinja`` — the landing page, pricing, partners, signup / login. The
control panel extends the same base but blanks the block, and storefront skins
never use it, so this only ever fires on the public marketing surface.
"""

from markupsafe import Markup

from . import providers
from .models import platform_tracking


def platform_pixels(request):
    path = getattr(request, "path", "") or ""
    if path.startswith("/admin/") or path.startswith("/sd/"):
        return {}
    if getattr(request, "storefront_host", False):
        return {}
    active = platform_tracking()
    if not active:
        return {}
    head = "".join(providers.head_snippet(name, cfg) for name, cfg in active.items())
    return {"platform_tracking_head": Markup(head)}
