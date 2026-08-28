"""Render an uploaded skin in a restricted sandbox.

* ``ImmutableSandboxedEnvironment`` — blocks attribute access to internals,
  mutation of shared objects, unsafe callables.
* ``DictLoader`` over just this skin's :class:`SkinFile` rows — no filesystem,
  no access to trusted templates or other skins.
* Curated context only (see ``theme_context.build``) — plain dicts, no ORM.
* ``url`` restricted to storefront view names; ``asset`` to this skin's files.
* Render runs on a worker thread with a wall-clock timeout and the output is
  size-capped.
"""

import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

from django.http import HttpResponse
from django.middleware.csrf import get_token
from django.urls import NoReverseMatch, reverse
from jinja2 import TemplateNotFound
from jinja2.exceptions import TemplateError
from jinja2.loaders import DictLoader
from jinja2.sandbox import ImmutableSandboxedEnvironment, SecurityError
from markupsafe import Markup

from . import theme_context

log = logging.getLogger("shopfront.skin")

RENDER_TIMEOUT = 2.5          # seconds
MAX_OUTPUT = 3_000_000        # bytes of rendered HTML
MAX_RANGE = 5_000             # cap on range() inside a skin — bounds loop bombs

_ALLOWED_URL_NAMES = frozenset(
    f"shopfront:{n}" for n in (
        "home", "shop", "product", "quickview", "search_suggest",
        "cart", "cart_add", "cart_update", "cart_remove", "cart_drawer",
        "checkout", "shipping_quote", "coupon_preview", "order",
        "account", "login", "register", "logout",
        "wishlist", "wishlist_toggle", "track", "page", "review",
    )
)

_REQUIRED_TEMPLATES = (
    "base.jinja", "home.jinja", "shop.jinja", "product.jinja", "cart.jinja",
    "checkout.jinja", "account.jinja", "wishlist.jinja", "order.jinja",
    "track.jinja", "page.jinja", "not_found.jinja",
    "partials/_card.jinja", "partials/_grid.jinja", "partials/_cart_drawer.jinja",
    "partials/_cart_page.jinja", "partials/_cart_fragments.jinja",
    "partials/_checkout_summary.jinja", "partials/_reviews.jinja",
    "partials/_quickview.jinja", "partials/_shipping_methods.jinja",
    "partials/_suggest.jinja", "partials/_wishlist_btn.jinja",
)

_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="skin-render")
_env_cache = {}


class SkinRenderError(Exception):
    pass


def _restricted_url(name, **kwargs):
    if name not in _ALLOWED_URL_NAMES:
        raise SecurityError(f"url('{name}') is not allowed in a skin")
    try:
        return reverse(name, kwargs=kwargs or None)
    except NoReverseMatch as exc:
        raise SecurityError(str(exc))


def _money(value, symbol="₹"):
    from decimal import Decimal, InvalidOperation
    try:
        return f"{symbol}{Decimal(str(value)):,.0f}"
    except (InvalidOperation, TypeError, ValueError):
        return f"{symbol}{value}"


def _capped_range(*args):
    """range() for skins — hard-capped so ``{% for i in range(1e9) %}`` can't
    hang the render thread (which cannot be force-killed)."""
    r = range(*args)
    if len(r) > MAX_RANGE:
        raise SecurityError(f"range() is capped at {MAX_RANGE} in a skin")
    return r


class _SkinLoader(DictLoader):
    """DictLoader that tolerates ``shopfront/`` / ``skins/<slug>/`` prefixes."""

    def __init__(self, mapping, slug):
        super().__init__(mapping)
        self._slug = slug

    def get_source(self, environment, template):
        for cand in (
            template,
            template.removeprefix("shopfront/skins/" + self._slug + "/"),
            template.removeprefix("shopfront/"),
        ):
            if cand in self.mapping:
                return super().get_source(environment, cand)
        raise TemplateNotFound(template)


def _build_env(skin):
    files = {f.path: f.content for f in skin.files.all()}
    assets = {a.path: a.file.url for a in skin.assets.all()}

    env = ImmutableSandboxedEnvironment(
        loader=_SkinLoader(files, skin.slug),
        autoescape=True,
        auto_reload=False,
        cache_size=0,
    )
    env.globals.update({
        "url": _restricted_url,
        "asset": lambda path: assets.get(path.lstrip("/"), ""),
        "range": _capped_range,
    })
    env.filters["money"] = _money
    return env


_ENV_CACHE_MAX = 8


def _env_for(skin):
    key = (skin.id, skin.updated_at.timestamp())
    env = _env_cache.get(key)
    if env is None:
        env = _build_env(skin)
        # drop any stale versions of this same skin, then bound total size
        for k in [k for k in _env_cache if k[0] == skin.id]:
            del _env_cache[k]
        if len(_env_cache) >= _ENV_CACHE_MAX:
            del _env_cache[next(iter(_env_cache))]
        _env_cache[key] = env
    return env


def missing_required(paths):
    """Return the required template paths absent from ``paths`` (an iterable)."""
    have = set(paths)
    return [p for p in _REQUIRED_TEMPLATES if p not in have]


def _template_name(template_name):
    # "shopfront/partials/_card.jinja" -> "partials/_card.jinja"
    return template_name.split("shopfront/", 1)[-1]


def _render(template, ctx):
    def job():
        return template.render(ctx)

    future = _executor.submit(job)
    try:
        html = future.result(timeout=RENDER_TIMEOUT)
    except FutureTimeout:
        raise SkinRenderError("Skin render timed out.")
    if len(html.encode("utf-8", "ignore")) > MAX_OUTPUT:
        raise SkinRenderError("Skin render output too large.")
    return html


def _theme_ctx(request, template_name, django_ctx):
    ctx = theme_context.build(request, template_name, django_ctx)
    token = get_token(request)
    ctx["csrf_input"] = Markup(
        '<input type="hidden" name="csrfmiddlewaretoken" value="{}">'
    ).format(token)
    ctx["csrf_token"] = token
    return ctx


def render_sandboxed(request, template_name, django_ctx, skin):
    env = _env_for(skin)
    name = _template_name(template_name)
    try:
        template = env.get_template(name)
    except TemplateNotFound:
        try:
            template = env.get_template("not_found.jinja")
        except TemplateNotFound:
            return HttpResponse("This storefront skin is incomplete.", status=500)
    try:
        html = _render(template, _theme_ctx(request, template_name, django_ctx))
    except (SecurityError, TemplateError, SkinRenderError) as exc:
        log.warning("skin %s render failed on %s: %s", skin.slug, name, exc)
        return HttpResponse(
            "This storefront theme could not be displayed.", status=500
        )
    return HttpResponse(html)


def render_to_string_sandboxed(template_name, django_ctx, request, skin):
    env = _env_for(skin)
    name = _template_name(template_name)
    try:
        template = env.get_template(name)
        return _render(template, _theme_ctx(request, template_name, django_ctx))
    except (TemplateNotFound, SecurityError, TemplateError, SkinRenderError) as exc:
        log.warning("skin %s fragment %s failed: %s", skin.slug, name, exc)
        return ""
