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
