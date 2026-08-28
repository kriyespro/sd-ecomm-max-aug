"""Storefront render helpers.

Every shopfront view renders through these. When the active skin is a sandboxed
upload (``request.skin_obj.is_sandboxed``) the response is produced by the
restricted sandbox with a curated context; otherwise it is a normal Django /
Jinja render (built-in skin folders, resolved by the skin-aware environment).
"""

from django.shortcuts import render as _django_render
from django.template.loader import render_to_string as _django_r2s


def _sandbox_skin(request):
    skin = getattr(request, "skin_obj", None)
    if skin is not None and getattr(skin, "is_sandboxed", False):
        return skin
    return None


def render(request, template_name, context=None, **kwargs):
    context = context or {}
    skin = _sandbox_skin(request)
    if skin is not None:
        from .sandbox import render_sandboxed

        return render_sandboxed(request, template_name, context, skin)
    return _django_render(request, template_name, context, **kwargs)


def render_to_string(template_name, context=None, request=None):
    skin = _sandbox_skin(request) if request is not None else None
    if skin is not None:
        from .sandbox import render_to_string_sandboxed

        return render_to_string_sandboxed(template_name, context, request, skin)
    return _django_r2s(template_name, context, request)
