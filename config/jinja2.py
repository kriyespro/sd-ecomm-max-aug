"""Jinja2 environment for the primary template backend.

Exposes the helpers templates expect: ``static``, ``url`` (Django's
``reverse``). Per project.md, Jinja2 templates use ``{{ csrf_input }}`` — never
``{% csrf_token %}``; ``csrf_input`` and ``csrf_token`` are injected per request
by :func:`csrf` below, wired as a context processor in settings.
"""

import functools
import os
import tempfile
from decimal import Decimal, InvalidOperation

from django.middleware.csrf import get_token
from django.templatetags.static import static
from django.urls import reverse
from django.utils.functional import lazy
from jinja2 import Environment
from jinja2.bccache import FileSystemBytecodeCache
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


def _rgb_channels(value, fallback="17 17 17"):
    """``"#c9a55a"`` -> ``"201 165 90"`` for ``rgb(var(--accent) / <alpha>)``.

    Storefront skins expose the store's accent colour as the ``--accent`` custom
    property in space-separated RGB channels so the compiled Tailwind build can
    do ``bg-accent/25`` opacity modifiers against it.
    """
    if not value:
        return fallback
    hexv = str(value).strip().lstrip("#")
    if len(hexv) == 3:
        hexv = "".join(c * 2 for c in hexv)
    if len(hexv) != 6:
        return fallback
    try:
        return f"{int(hexv[0:2], 16)} {int(hexv[2:4], 16)} {int(hexv[4:6], 16)}"
    except ValueError:
        return fallback


@functools.lru_cache(maxsize=64)
def _skin_css_href(slug):
    """Static URL of the compiled Tailwind CSS for ``slug``, or ``None`` when it
    has not been built/collected (dev, or before the first image build) — the
    skin base template then falls back to the Tailwind Play CDN.

    Cached per process: the collected file set does not change while a worker
    runs.
    """
    path = f"shopfront/skins/{slug}.css"
    try:
        from django.contrib.staticfiles import finders
        from django.contrib.staticfiles.storage import staticfiles_storage

        if finders.find(path) or staticfiles_storage.exists(path):
            return staticfiles_storage.url(path)
    except Exception:  # noqa: BLE001 - never let a missing asset 500 a storefront
        pass
    return None


def _money(value, symbol="₹"):
    try:
        return f"{symbol}{Decimal(str(value)):,.0f}"
    except (InvalidOperation, TypeError, ValueError):
        return f"{symbol}{value}"


def _bytecode_cache():
    """Compiled-template cache on disk, shared by every gunicorn worker. Workers
    recycle on max_requests, so without this each fresh worker recompiles all
    skin templates on its first hits."""
    path = os.environ.get(
        "JINJA_BYTECODE_CACHE_DIR", os.path.join(tempfile.gettempdir(), "sd-jinja-bc")
    )
    try:
        os.makedirs(path, exist_ok=True)
        return FileSystemBytecodeCache(path)
    except OSError:
        return None


def environment(**options):
    options.setdefault("autoescape", True)
    options.setdefault("auto_reload", False)
    options.setdefault("bytecode_cache", _bytecode_cache())
    inner_loader = options.get("loader")
    if inner_loader is not None and not isinstance(inner_loader, _SkinLoader):
        options["loader"] = _SkinLoader(inner_loader)
    env = SkinEnvironment(**options)
    env.globals.update(
        {
            "static": static,
            "url": reverse,
            "skin_css_href": _skin_css_href,
        }
    )
    env.filters["money"] = _money
    env.filters["rgb_channels"] = _rgb_channels
    return env


def csrf(request):
    """Context processor: per-request CSRF helpers for Jinja2 templates.

    Both values are lazy: ``get_token`` (which forces the CSRF cookie to be
    set on the response) runs only if a template actually renders one of them.
    That keeps storefront pages with no form cookie-free, hence edge-cacheable.
    ``csrf_input`` is marked safe so autoescape leaves the ``<input>`` intact.
    """

    def _token():
        return get_token(request)

    def _field():
        return Markup(
            '<input type="hidden" name="csrfmiddlewaretoken" value="{}">'
        ).format(get_token(request))

    return {
        "csrf_input": lazy(_field, Markup)(),
        "csrf_token": lazy(_token, str)(),
    }
