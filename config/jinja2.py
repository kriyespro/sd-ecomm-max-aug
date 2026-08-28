"""Jinja2 environment for the primary template backend.

Exposes the helpers templates expect: ``static``, ``url`` (Django's
``reverse``). Per project.md, Jinja2 templates use ``{{ csrf_input }}`` — never
``{% csrf_token %}``; ``csrf_input`` and ``csrf_token`` are injected per request
by :func:`csrf` below, wired as a context processor in settings.
"""

from decimal import Decimal, InvalidOperation

from django.middleware.csrf import get_token
from django.templatetags.static import static
from django.urls import reverse
from jinja2 import Environment
from jinja2.exceptions import TemplateNotFound
from jinja2.loaders import BaseLoader
from markupsafe import Markup

from apps.shopfront.runtime import get_active_skin

# Template names under this prefix are skin-swappable. A request-scoped skin
# (see apps.shopfront.runtime) rewrites e.g. "shopfront/home.jinja" to
# "shopfront/skins/<skin>/home.jinja", falling back to the default skin and then
# to the bare name. Folding the skin into the cache key keeps compiled templates
# separate per skin.
_SKINNABLE_PREFIX = "shopfront/"
_SKIN_CACHE_PREFIX = "__skin__/"


class _SkinLoader(BaseLoader):
    """Wraps the real loader. Resolves ``__skin__/<skin>/shopfront/<x>`` names to
    the first template that exists among the skin, the default skin, and the
    bare name."""

    def __init__(self, inner):
        self.inner = inner

    def get_source(self, environment, template):
        if template.startswith(_SKIN_CACHE_PREFIX):
            rest = template[len(_SKIN_CACHE_PREFIX):]
            skin, _, real = rest.partition("/")  # real == "shopfront/<x>"
            sub = real[len(_SKINNABLE_PREFIX):]
            for candidate in (
                f"{_SKINNABLE_PREFIX}skins/{skin}/{sub}",
                f"{_SKINNABLE_PREFIX}skins/default/{sub}",
                real,
            ):
                try:
                    return self.inner.get_source(environment, candidate)
                except TemplateNotFound:
                    continue
            raise TemplateNotFound(template)
        return self.inner.get_source(environment, template)

    def list_templates(self):
        return self.inner.list_templates()


class SkinEnvironment(Environment):
    """Rewrites skin-swappable template names to a skin-scoped cache key before
    every load (page render, ``{% extends %}``, ``{% include %}``)."""

    def _load_template(self, name, *args, **kwargs):
        if (
            isinstance(name, str)
            and name.startswith(_SKINNABLE_PREFIX)
            and not name.startswith(_SKIN_CACHE_PREFIX)
            and not name.startswith(f"{_SKINNABLE_PREFIX}skins/")
        ):
            name = f"{_SKIN_CACHE_PREFIX}{get_active_skin()}/{name}"
        return super()._load_template(name, *args, **kwargs)


def _money(value, symbol="₹"):
    try:
        return f"{symbol}{Decimal(str(value)):,.0f}"
    except (InvalidOperation, TypeError, ValueError):
        return f"{symbol}{value}"


def environment(**options):
    options.setdefault("autoescape", True)
    inner_loader = options.get("loader")
    if inner_loader is not None and not isinstance(inner_loader, _SkinLoader):
        options["loader"] = _SkinLoader(inner_loader)
    env = SkinEnvironment(**options)
    env.globals.update(
        {
            "static": static,
            "url": reverse,
        }
    )
    env.filters["money"] = _money
    return env


def csrf(request):
    """Context processor: per-request CSRF helpers for Jinja2 templates.

    ``csrf_input`` is marked safe so autoescape leaves the ``<input>`` intact.
    """
    token = get_token(request)
    field = Markup(
        '<input type="hidden" name="csrfmiddlewaretoken" value="{}">'
    ).format(token)
    return {"csrf_input": field, "csrf_token": token}
