"""Storefront inline editor: what is editable, who may edit, and the endpoints.

An owner / manager (or the store's DGC / a platform admin) opens the storefront
with ``?edit=1``. Templates stamp ``data-ed="<kind>:<pk>:<field>"`` markers on
editable text and images (``ed()`` Jinja global — empty outside edit mode) and
``static/shopfront/inline-edit.js`` turns those into click-to-edit controls
that POST here. Every save is validated against :data:`REGISTRY` (a strict
model/field whitelist, scoped to ``request.project``), written through the
normal model ``save()`` so signals bust the storefront caches and Page bodies
get sanitised, and logged to ``InlineEditLog`` so it can be undone.
"""

import json
import re
import time
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.utils.html import format_html, strip_tags
from django.views.decorators.http import require_POST

from apps.accounts.permissions import OWNER_MANAGER, has_store_role
from apps.catalog.models import Product
from apps.categories.models import Category
from apps.cms.homepage_sections import effective_order, section_keys_for_skin, sections_for_skin
from apps.cms.models import (
    Banner, BenefitItem, InlineEditLog, MenuItem, Page, StoreProfile, ThemeSettings,
)

from .runtime import get_active_skin, has_product_form, is_editing

SESSION_KEY = "sf_edit_mode"
DELEGATE_KEY = "sf_edit_delegate"
HANDOFF_SALT = "shopfront.inline-edit.handoff"
HANDOFF_MAX_AGE = 120          # seconds a hand-off link stays valid
DELEGATE_TTL = 8 * 3600        # seconds a delegated editor session lasts
MAX_IMAGE_BYTES = 8 * 1024 * 1024
LOG_KEEP = 500

TEXT, MULTILINE, EMAIL, LINK, COLOR, RICH, IMAGE, DECIMAL, SECTIONS = (
    "text", "multiline", "email", "link", "color", "rich", "image", "decimal", "sections",
)
# Types that are edited by a dedicated control, never as click-to-type text.
_NOT_INLINE_TEXT = {DECIMAL, SECTIONS}

# kind -> (model, {field: type}). The only things the editor may touch.
REGISTRY = {
    "banner": (Banner, {
        "heading": TEXT, "subheading": TEXT, "cta_label": TEXT, "cta_url": LINK,
        "image": IMAGE,
    }),
    "profile": (StoreProfile, {
        "tagline": TEXT, "address": MULTILINE, "support_phone": TEXT,
        "support_email": EMAIL, "copyright_text": TEXT, "logo": IMAGE,
    }),
    "page": (Page, {"title": TEXT, "excerpt": TEXT, "body": RICH}),
    "theme": (ThemeSettings, {"primary_color": COLOR, "homepage_sections": SECTIONS}),
    "benefit": (BenefitItem, {"icon": TEXT, "title": TEXT, "description": TEXT}),
    "menuitem": (MenuItem, {"label": TEXT}),
    "category": (Category, {"name": TEXT}),
    "product": (Product, {"title": TEXT, "price": DECIMAL, "sale_price": DECIMAL}),
}
# Models that have no ``project`` column: how to reach it.
PROJECT_LOOKUP = {"menuitem": "menu__project"}
# One row per store — the marker pk is ignored and the row is get_or_created.
SINGLETONS = {"profile", "theme"}

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_LINK_SCHEMES = {"http", "https", "mailto", "tel"}
_PLACEHOLDER = {
    "heading": "Add a heading", "subheading": "Add a subheading",
    "cta_label": "Button text", "tagline": "Add a tagline",
    "address": "Add your address", "support_phone": "Add a phone number",
    "support_email": "Add an email", "title": "Add a title",
    "excerpt": "Add a short summary", "body": "Write something…",
}


def can_edit_storefront(user, project) -> bool:
    """Owner / manager of the store, its DGC, or a platform admin. Staff-role
    members and shoppers never get the editor."""
    if not (user and user.is_authenticated and user.is_active and user.is_staff):
        return False
    if project is None:
        return False
    return has_store_role(user, project, OWNER_MANAGER)


def handoff_url(user, project):
    """Admin -> storefront link for a person whose login lives on another host
    (platform admin / DGC working from the platform domain). Carries a short-lived
    signed token that the store host trades for an *editor-only* session flag —
    it never logs anyone in, so it grants no Mission Control access there."""
    base = project.public_url
    if not base or not can_edit_storefront(user, project):
        return None
    token = signing.dumps({"u": user.pk, "p": project.pk}, salt=HANDOFF_SALT)
    return f"{base}?sd_edit={token}"


def accept_handoff(request, project, token) -> bool:
    try:
        data = signing.loads(token, salt=HANDOFF_SALT, max_age=HANDOFF_MAX_AGE)
        uid, pid = int(data["u"]), int(data["p"])
    except (signing.BadSignature, KeyError, TypeError, ValueError):
        return False
    if pid != project.pk:
        return False
    user = get_user_model().objects.filter(pk=uid, is_active=True).first()
    if user is None or not can_edit_storefront(user, project):
        return False
    request.session[DELEGATE_KEY] = {"u": uid, "p": pid, "t": time.time()}
    request.session[SESSION_KEY] = True
    return True


def editor_for(request, project):
    """The person allowed to edit ``project`` on this request: the logged-in
    user, or one delegated through a hand-off. ``None`` for everybody else.
    Never touches the session for a cookie-less (CDN-cacheable) visitor."""
    if project is None:
        return None
    cached = getattr(request, "_sf_editor", False)
    if cached is not False:
        return cached
    editor = None
    user = getattr(request, "user", None)
    if user is not None and can_edit_storefront(user, project):
        editor = user
    elif settings.SESSION_COOKIE_NAME in request.COOKIES:
        d = request.session.get(DELEGATE_KEY) or {}
        if d.get("p") == project.pk and time.time() - d.get("t", 0) < DELEGATE_TTL:
            u = get_user_model().objects.filter(pk=d.get("u"), is_active=True).first()
            if u is not None and can_edit_storefront(u, project):
                editor = u
    request._sf_editor = editor
    return editor


def _pk(obj):
    """Row id of a model instance, or of a serialised dict carrying ``id``."""
    if isinstance(obj, dict):
        return obj.get("id")
    return getattr(obj, "pk", None)


def ed(kind, obj=None, field="", placeholder=None):
    """Jinja global: the ``data-ed`` attributes for one editable value, or ``""``
    outside edit mode / for anything not whitelisted / when there is no row."""
    if not is_editing():
        return ""
    spec = REGISTRY.get(kind)
    if spec is None or field not in spec[1] or spec[1][field] in _NOT_INLINE_TEXT:
        return ""
    if kind == "product" and has_product_form():
        return ""   # the card's "Edit product" button handles it
    pk = 0 if kind in SINGLETONS else _pk(obj)
    if pk is None:
        return ""
    ph = placeholder if placeholder is not None else _PLACEHOLDER.get(field, "")
    return format_html(
        ' data-ed="{}:{}:{}" data-ed-t="{}" data-ed-ph="{}"', kind, pk, field, spec[1][field], ph,
    )


def ed_link(kind, obj=None, field=""):
    """``data-ed-link`` for the URL behind an editable label (an <a>'s href)."""
    if not is_editing():
        return ""
    spec = REGISTRY.get(kind)
    pk = _pk(obj)
    if spec is None or field not in spec[1] or pk is None or spec[1][field] != LINK:
        return ""
    return format_html(' data-ed-link="{}:{}:{}"', kind, pk, field)


def ed_nav(node):
    """Marker for a main-nav label: a CMS menu item, or — when the store has no
    menu and the nav falls back to its categories — the category name."""
    if isinstance(node, dict) and node.get("cat"):
        return ed("category", node, "name")
    return ed("menuitem", node, "label")


def ed_money(product):
    """Attributes that make a product's price block open the price popover."""
    if not is_editing() or has_product_form() or getattr(product, "pk", None) is None:
        return ""
    sale = getattr(product, "sale_price", None)
    return format_html(
        ' data-ed-money="{}" data-price="{}" data-sale="{}"',
        product.pk, product.price, "" if sale is None else sale,
    )


def ed_product(product):
    """Marks a product card so the editor can add an "Edit product" button that
    opens the full admin form. Only when that form is reachable on this host."""
    if not is_editing() or not has_product_form() or getattr(product, "pk", None) is None:
        return ""
    return format_html(' data-ed-product="{}"', product.pk)


def ed_section(key):
    """Wraps one reorderable homepage section so the editor can move it."""
    if not is_editing():
        return ""
    label = dict(sections_for_skin(get_active_skin())).get(key, key)
    return format_html(' data-ed-section="{}" data-ed-label="{}"', key, label)


# --- validation ---------------------------------------------------------

def _max_len(model, field):
    return getattr(model._meta.get_field(field), "max_length", None)


def _clean(model, field, ftype, raw, skin="default"):
    """Return the cleaned value or raise ``ValidationError``."""
    if ftype == SECTIONS:
        try:
            keys = raw if isinstance(raw, list) else json.loads(raw or "[]")
        except ValueError:
            raise ValidationError("Bad section order.")
        valid = set(section_keys_for_skin(skin))
        if not isinstance(keys, list) or not all(isinstance(k, str) and k in valid for k in keys):
            raise ValidationError("Unknown homepage section.")
        return json.dumps(effective_order(skin, keys))
    value = "" if raw is None else str(raw)
    mf = model._meta.get_field(field)
    if ftype == DECIMAL:
        value = value.strip().replace(",", "")
        if value:
            try:
                dec = Decimal(value)
            except InvalidOperation:
                raise ValidationError("Enter a number.")
            if not dec.is_finite() or dec < 0:
                raise ValidationError("Enter a number, zero or more.")
            if dec >= Decimal(10) ** (mf.max_digits - mf.decimal_places):
                raise ValidationError("That number is too large.")
            value = str(dec.quantize(Decimal(1).scaleb(-mf.decimal_places)))
        if not value and not mf.null:
            raise ValidationError("This can't be empty.")
        return value
    if ftype == RICH:
        value = value.strip()
        if len(value) > 100_000:
            raise ValidationError("That is too long.")
    elif ftype == COLOR:
        value = value.strip()
        if not _HEX.match(value):
            raise ValidationError("Use a colour like #b08d57.")
        value = value.lower()
    elif ftype == LINK:
        value = value.strip()
        if value:
            if value.startswith("//"):
                raise ValidationError("Enter a full link starting with https:// or /.")
            if not value.startswith("/"):
                if urlparse(value).scheme.lower() not in _LINK_SCHEMES:
                    raise ValidationError("Enter a full link starting with https://")
    else:  # text / multiline / email
        value = strip_tags(value.replace("\r\n", "\n").replace("\r", "\n"))
        if ftype == MULTILINE:
            value = "\n".join(line.strip() for line in value.split("\n")).strip()
        else:
            value = re.sub(r"\s+", " ", value).strip()
        if ftype == EMAIL and value:
            validate_email(value)
    limit = _max_len(model, field)
    if limit and len(value) > limit:
        raise ValidationError(f"Keep it under {limit} characters.")
    if not value and not mf.blank:
        raise ValidationError("This can't be empty.")
    return value


def _verify_image(upload):
    """Raise ``ValidationError`` unless ``upload`` is a real JPEG/PNG/WebP/GIF."""
    if upload.size > MAX_IMAGE_BYTES:
        raise ValidationError("Image is too large (8 MB max).")
    try:
        from PIL import Image

        upload.seek(0)
        with Image.open(upload) as im:
            fmt = (im.format or "").upper()
            im.verify()
    except Exception as exc:  # noqa: BLE001 — anything unreadable is "not an image"
        raise ValidationError("That file isn't a usable image.") from exc
    finally:
        upload.seek(0)
    if fmt not in {"JPEG", "PNG", "WEBP", "GIF"}:
        raise ValidationError("Use a JPG, PNG, WebP or GIF image.")


# --- row access ---------------------------------------------------------

def _project(request):
    return getattr(request, "project", None) or None


def _guard(request):
    """The project to edit, or a JSON error response."""
    project = _project(request)
    editor = editor_for(request, project)
    if editor is None:
        return None, JsonResponse({"ok": False, "error": "You can't edit this store."}, status=403)
    request._sf_editor = editor
    return project, None


def _target(project, kind, pk, field, ftype_wanted=None):
    """``(obj, model, ftype)`` for a whitelisted kind/field, else ``None``."""
    spec = REGISTRY.get(kind)
    if spec is None or field not in spec[1]:
        return None
    model, fields = spec
    ftype = fields[field]
    if ftype_wanted == IMAGE and ftype != IMAGE:
        return None
    if ftype_wanted is None and ftype == IMAGE:
        return None
    if kind in SINGLETONS:
        obj, _ = model.objects.get_or_create(project=project)
    else:
        scope = PROJECT_LOOKUP.get(kind, "project")
        obj = model.objects.filter(**{scope: project}, pk=pk).first()
    if obj is None:
        return None
    return obj, model, ftype


def _current(obj, field, ftype):
    val = getattr(obj, field)
    if ftype == IMAGE:
        return val.name or ""
    if ftype == SECTIONS:
        return json.dumps(list(val or []))
    if ftype == DECIMAL:
        return "" if val is None else str(val)
    return val or ""


def _assign(obj, field, ftype, value):
    """Set ``field`` from its stored/log string form."""
    if ftype == SECTIONS:
        value = json.loads(value or "[]")
    elif ftype == DECIMAL:
        value = Decimal(value) if value else None
    setattr(obj, field, value)


def _payload(obj, kind, field, ftype, log=None):
    val = getattr(obj, field)
    if ftype == IMAGE:
        value = val.url if val else ""
    elif ftype in (SECTIONS, DECIMAL):
        value = _current(obj, field, ftype)
    else:
        value = val
    out = {
        "ok": True, "kind": kind, "pk": 0 if kind in SINGLETONS else obj.pk,
        "field": field, "type": ftype, "value": value,
    }
    if log is not None:
        out["log"] = log.pk
    return out


def _record(project, user, kind, obj, field, old, new):
    log = InlineEditLog.objects.create(
        project=project, user=user, kind=kind,
        object_id=0 if kind in SINGLETONS else obj.pk,
        field=field, old_value=old, new_value=new,
    )
    stale = list(
        InlineEditLog.objects.filter(project=project).order_by("-id")
        .values_list("pk", flat=True)[LOG_KEEP:]
    )
    if stale:
        InlineEditLog.objects.filter(pk__in=stale).delete()
    return log


def _body(request):
    if request.content_type == "application/json":
        try:
            data = json.loads(request.body or b"{}")
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}
    return request.POST


def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# --- endpoints ----------------------------------------------------------

@require_POST
def save_view(request):
    project, err = _guard(request)
    if err:
        return err
    data = _body(request)
    kind, field = str(data.get("kind", "")), str(data.get("field", ""))
    found = _target(project, kind, _int(data.get("pk")), field)
    if found is None:
        return JsonResponse({"ok": False, "error": "That can't be edited here."}, status=400)
    obj, model, ftype = found
    try:
        value = _clean(model, field, ftype, data.get("value"), getattr(request, "skin_slug", "default"))
    except ValidationError as exc:
        return JsonResponse({"ok": False, "error": exc.messages[0]}, status=400)
    old = _current(obj, field, ftype)
    if value == old:
        return JsonResponse(_payload(obj, kind, field, ftype))
    with transaction.atomic():
        _assign(obj, field, ftype, value)
        obj.save()  # .save(), not .update(): signals bust the storefront chrome cache
        # Rich text is sanitised on save — log + return what was really stored.
        stored = _current(obj, field, ftype)
        log = _record(project, request._sf_editor, kind, obj, field, old, stored)
    return JsonResponse(_payload(obj, kind, field, ftype, log))


@require_POST
def image_view(request):
    project, err = _guard(request)
    if err:
        return err
    kind, field = request.POST.get("kind", ""), request.POST.get("field", "")
    found = _target(project, kind, _int(request.POST.get("pk")), field, IMAGE)
    upload = request.FILES.get("file")
    if found is None or upload is None:
        return JsonResponse({"ok": False, "error": "Choose an image to upload."}, status=400)
    obj, _model, ftype = found
    try:
        _verify_image(upload)
    except ValidationError as exc:
        return JsonResponse({"ok": False, "error": exc.messages[0]}, status=400)
    old = _current(obj, field, ftype)
    with transaction.atomic():
        getattr(obj, field).save(upload.name, upload, save=False)
        obj.save()  # model save() shrinks banner / logo images to WebP
        log = _record(project, request._sf_editor, kind, obj, field, old, _current(obj, field, ftype))
    return JsonResponse(_payload(obj, kind, field, ftype, log))


def _apply_log(request, *, undo):
    project, err = _guard(request)
    if err:
        return err
    log = InlineEditLog.objects.filter(
        project=project, pk=_int(_body(request).get("id")),
    ).first()
    if log is None or (log.undone_at is not None) == undo:
        return JsonResponse({"ok": False, "error": "Nothing to " + ("undo." if undo else "redo.")}, status=400)
    found = _target(project, log.kind, log.object_id, log.field, None) \
        or _target(project, log.kind, log.object_id, log.field, IMAGE)
    if found is None:
        return JsonResponse({"ok": False, "error": "That item no longer exists."}, status=404)
    obj, _model, ftype = found
    target = log.old_value if undo else log.new_value
    with transaction.atomic():
        _assign(obj, log.field, ftype, target)
        obj.save()
        log.undone_at = timezone.now() if undo else None
        log.save(update_fields=["undone_at", "updated_at"])
    return JsonResponse(_payload(obj, log.kind, log.field, ftype, log))


@require_POST
def undo_view(request):
    return _apply_log(request, undo=True)


@require_POST
def redo_view(request):
    return _apply_log(request, undo=False)
