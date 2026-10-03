"""Per-request storefront skin, held in a context variable.

Leaf module — imports only the stdlib — so ``config.jinja2`` can read it at
template-load time without an app-registry dependency. The value is set by
``StorefrontSkinMiddleware`` and consulted by the skin-aware Jinja environment.
"""

import contextvars
from contextlib import contextmanager

DEFAULT_SKIN = "default"

_active_skin: "contextvars.ContextVar[str]" = contextvars.ContextVar(
    "shopfront_active_skin", default=DEFAULT_SKIN
)


def get_active_skin() -> str:
    try:
        return _active_skin.get() or DEFAULT_SKIN
    except LookupError:
        return DEFAULT_SKIN


def set_active_skin(slug):
    return _active_skin.set(slug or DEFAULT_SKIN)


def reset_active_skin(token) -> None:
    try:
        _active_skin.reset(token)
    except (ValueError, LookupError):
        _active_skin.set(DEFAULT_SKIN)


@contextmanager
def use_skin(slug):
    token = set_active_skin(slug)
    try:
        yield
    finally:
        reset_active_skin(token)


# Inline editor ("edit mode"): while true, templates stamp ``data-ed`` markers
# on editable text/images via the ``ed()`` Jinja global. Only the owner-facing
# ``InlineEditMiddleware`` ever sets it, and such responses are never cached.
_edit_mode: "contextvars.ContextVar[bool]" = contextvars.ContextVar(
    "shopfront_edit_mode", default=False
)


def is_editing() -> bool:
    try:
        return bool(_edit_mode.get())
    except LookupError:
        return False


@contextmanager
def use_edit_mode(on):
    token = _edit_mode.set(bool(on))
    try:
        yield
    finally:
        try:
            _edit_mode.reset(token)
        except (ValueError, LookupError):
            _edit_mode.set(False)


# Edit mode where the logged-in user can open Mission Control on this host: the
# product cards then get an "Edit product" button that opens the full admin form
# (and returns here) instead of inline title/price editing.
_product_form: "contextvars.ContextVar[bool]" = contextvars.ContextVar(
    "shopfront_edit_product_form", default=False
)


def has_product_form() -> bool:
    try:
        return bool(_product_form.get())
    except LookupError:
        return False


@contextmanager
def use_product_form(on):
    token = _product_form.set(bool(on))
    try:
        yield
    finally:
        try:
            _product_form.reset(token)
        except (ValueError, LookupError):
            _product_form.set(False)
