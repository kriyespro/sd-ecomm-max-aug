"""Resolve which storefront skin a store renders with.

Precedence:
1. ``ThemeSettings.skin`` — the store owner's choice — if it is live (active +
   approved) and permitted (shared, or owned by this store, or explicitly in
   ``Project.allowed_skins``).
2. The ``Skin`` row flagged ``is_default``.
3. ``None`` — the built-in ``"default"`` template folder.
"""

from django.db.models import Q

from .models import Skin, SkinStatus, ThemeSettings

DEFAULT_SLUG = "default"


def _live():
    return Skin.objects.filter(is_active=True, status=SkinStatus.APPROVED)


def default_skin():
    return _live().filter(is_default=True).first()


def skin_is_allowed(project, skin) -> bool:
    if skin is None or not skin.is_live:
        return False
    if skin.project_id and skin.project_id != project.id:
        return False  # someone else's private upload
    if skin.project_id == project.id:
        return True  # this store's own upload — always selectable
    allowed_ids = list(project.allowed_skins.values_list("id", flat=True))
    return not allowed_ids or skin.id in allowed_ids


def allowed_skins_for(project):
    """Skins this store's owner may choose on the Theme screen."""
    qs = _live().filter(Q(project__isnull=True) | Q(project=project))
    allowed_ids = list(project.allowed_skins.values_list("id", flat=True))
    if allowed_ids:
        qs = qs.filter(Q(id__in=allowed_ids) | Q(project=project))
    return qs.order_by("label")


def skin_for_project(project):
    """Return the ``Skin`` instance to render with, or ``None`` for the built-in."""
    if project is None:
        return None
    ts = (
        ThemeSettings.objects.filter(project=project)
        .select_related("skin", "skin__project")
        .first()
    )
    if ts is not None and ts.skin_id and skin_is_allowed(project, ts.skin):
        return ts.skin
    return default_skin()


def skin_slug_for_project(project) -> str:
    skin = skin_for_project(project)
    return skin.slug if skin is not None else DEFAULT_SLUG
