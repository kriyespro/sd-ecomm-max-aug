"""Store backup / restore — download one store's storefront content as a
portable .zip and load it onto any other store.

WHAT'S INCLUDED: catalogue (categories, brands, product types, products,
product images, variants), CMS (pages, FAQs, banners, budget bands, Instagram
items, Shorts, content blocks, menus), and the theme + store profile.

WHAT'S NOT: orders, customers, carts, reviews, inventory levels, coupons,
shipping, payment credentials, domains, the subscription, the team — all of
that is a store's own live operation, never cloned.

Restore is destructive: the target store's content (same set as above) is
wiped first, then rebuilt from the archive. Platform-admin only.
"""

from __future__ import annotations

import io
import json
import logging
import posixpath
import zipfile
from datetime import datetime, timezone as dt_timezone

from django.apps import apps
from django.core.files.base import ContentFile
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction

logger = logging.getLogger(__name__)

FORMAT_VERSION = 1
MAX_PRODUCTS = 5000
MAX_ARCHIVE_UNCOMPRESSED = 600 * 1024 * 1024   # 600 MB
MAX_MANIFEST_BYTES = 64 * 1024 * 1024           # 64 MB

# Ordered by dependency: a row's FK targets are always created before it.
# ``fks``: {field: other-key | "SELF"} — remapped to the new ids on restore.
# ``files``: ImageField names — bytes travel in the zip under media/<token>.
# ``singleton``: exactly one row per project (update_or_create on restore).
# ``scope``: how to filter the queryset to one store when the model has no
#            ``project`` field (MenuItem lives under Menu).
_REGISTRY: list[dict] = [
    dict(key="category", model="categories.Category",
         fields=["name", "slug", "description", "is_active", "is_featured", "order",
                 "home_row", "seo_title", "seo_description", "seo_keywords"],
         fks={"parent": "SELF"}, files=["image", "banner", "icon"]),
    dict(key="brand", model="catalog.Brand",
         fields=["name", "slug", "description", "is_active"], files=["logo"]),
    dict(key="producttype", model="catalog.ProductType",
         fields=["name", "slug", "kind"]),
    dict(key="product", model="catalog.Product",
         fields=["kind", "title", "slug", "sku", "short_description", "description",
                 "price", "sale_price", "cost_price", "tax_class", "barcode", "hsn_sac",
                 "weight", "length", "width", "height", "status", "is_featured",
                 "is_new_arrival", "is_bestseller", "search_indexed",
                 "seo_title", "seo_description", "seo_keywords"],
         fks={"type": "producttype", "brand": "brand", "category": "category"}),
    dict(key="productimage", model="catalog.ProductImage",
         fields=["alt", "order", "is_primary"],
         fks={"product": "product"}, files=["image"],
         scope="product__project", no_project=True),
    dict(key="variant", model="catalog.Variant",
         fields=["name", "sku", "price", "sale_price", "cost_price", "stock", "is_active"],
         fks={"product": "product"},
         scope="product__project", no_project=True),
    dict(key="page", model="cms.Page",
         fields=["kind", "title", "slug", "excerpt", "body", "blocks", "status",
                 "show_in_sitemap", "template_key",
                 "seo_title", "seo_description", "seo_keywords"]),
    dict(key="faq", model="cms.FAQ",
         fields=["group", "question", "answer", "order", "is_active"]),
    dict(key="banner", model="cms.Banner",
         fields=["name", "placement", "heading", "subheading", "cta_label", "cta_url",
                 "priority", "is_active", "text_hidden"],
         fks={"category": "category"}, files=["image", "mobile_image"]),
    dict(key="budgetband", model="cms.BudgetBand",
         fields=["label", "min_price", "max_price", "order", "is_active"],
         files=["image"]),
    dict(key="benefititem", model="cms.BenefitItem",
         fields=["icon", "title", "description", "order", "is_active"]),
    dict(key="instagramitem", model="cms.InstagramItem",
         fields=["source_url", "caption", "link_url", "order", "is_active"],
         files=["image"]),
    dict(key="ugcvideo", model="cms.UGCVideo",
         fields=["youtube_url", "youtube_id", "caption", "link_url", "priority", "is_active"]),
    dict(key="contentblock", model="cms.ContentBlock",
         fields=["key", "name", "block_type", "data", "is_active"]),
    dict(key="menu", model="cms.Menu",
         fields=["name", "location", "is_active"]),
    dict(key="menuitem", model="cms.MenuItem",
         fields=["label", "link_type", "url", "open_in_new_tab", "order", "is_active"],
         fks={"menu": "menu", "parent": "SELF", "page": "page", "category": "category"},
         scope="menu__project", no_project=True),
    dict(key="storeprofile", model="cms.StoreProfile", singleton=True,
         fields=["tagline", "support_email", "support_phone", "whatsapp", "address",
                 "gstin", "instagram_url", "facebook_url", "youtube_url", "x_url",
                 "copyright_text", "show_payment_icons"],
         files=["logo"]),
    dict(key="themesettings", model="cms.ThemeSettings", singleton=True,
         fields=["primary_color", "secondary_color", "accent_color", "font_body",
                 "font_heading", "header_layout", "footer_layout", "button_style",
                 "product_card_style", "homepage_sections", "tokens", "custom_css",
                 "category_above_hero", "show_category_headings"]),
]


class BackupError(Exception):
    pass


def _model(entry):
    return apps.get_model(entry["model"])


def _queryset(entry, project):
    Model = _model(entry)
    scope = entry.get("scope", "project")
    return Model.objects.filter(**{scope: project})


# --- dump ---------------------------------------------------------------

def _dump_store_into(zf: zipfile.ZipFile, project, prefix: str = "") -> dict:
    """Write ``{prefix}manifest.json`` + ``{prefix}media/...`` into an open zip.
    Returns per-model counts."""
    manifest = {
        "format": FORMAT_VERSION,
        "source_project": project.name,
        "created_at": datetime.now(dt_timezone.utc).isoformat(),
        "data": {},
    }
    counts = {}
    for entry in _REGISTRY:
        rows = []
        for obj in _queryset(entry, project).order_by("pk").iterator():
            row = {"_id": obj.pk}
            for f in entry["fields"]:
                row[f] = getattr(obj, f)
            for fk in entry.get("fks", {}):
                row[fk] = getattr(obj, f"{fk}_id")
            for name in entry.get("files", []):
                ff = getattr(obj, name)
                if not ff:
                    continue
                token = f"{entry['key']}/{obj.pk}/{name}/{posixpath.basename(ff.name)}"
                try:
                    ff.open("rb")
                    zf.writestr(f"{prefix}media/{token}", ff.read())
                    row[f"__file__{name}"] = token
                except Exception as exc:  # noqa: BLE001
                    logger.warning("backup: could not read %s: %s", ff.name, exc)
                finally:
                    try:
                        ff.close()
                    except Exception:  # noqa: BLE001
                        pass
            rows.append(row)
        manifest["data"][entry["key"]] = rows
        counts[entry["key"]] = len(rows)
    zf.writestr(f"{prefix}manifest.json", json.dumps(manifest, cls=DjangoJSONEncoder))
    return counts


def dump_store(project) -> bytes:
    """Serialise ``project``'s storefront content to a .zip byte string."""
    from apps.catalog.models import Product

    n_products = Product.objects.filter(project=project).count()
    if n_products > MAX_PRODUCTS:
        raise BackupError(
            f"This store has {n_products} products — over the {MAX_PRODUCTS} backup limit."
        )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        _dump_store_into(zf, project, "")
    return buf.getvalue()


_STORE_META_FIELDS = ("name", "slug", "currency", "country", "state", "timezone",
                      "primary_domain", "is_b2b_seller")


def dump_platform() -> bytes:
    """Every store's content in one archive: ``platform.json`` plus a
    ``stores/<id>/`` folder per store (each = a normal store backup)."""
    from apps.projects.models import Project

    buf = io.BytesIO()
    index = {
        "format": FORMAT_VERSION,
        "kind": "platform",
        "created_at": datetime.now(dt_timezone.utc).isoformat(),
        "stores": [],
    }
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for project in Project.objects.all().order_by("pk").iterator():
            meta = {"id": project.pk}
            for f in _STORE_META_FIELDS:
                meta[f] = getattr(project, f, None)
            meta["feature_flags"] = project.feature_flags or {}
            skin = getattr(getattr(project, "theme_settings", None), "skin", None)
            meta["skin"] = skin.slug if skin is not None else ""
            index["stores"].append(meta)
            _dump_store_into(zf, project, f"stores/{project.pk}/")
        zf.writestr("platform.json", json.dumps(index, cls=DjangoJSONEncoder))
    return buf.getvalue()


# --- restore -----------------------------------------------------------

_ALLOWED_ROOTS = ("manifest.json", "platform.json", "media/", "stores/")


def _safe_zip(zf: zipfile.ZipFile) -> None:
    total = 0
    for zi in zf.infolist():
        name = zi.filename
        if name.startswith("/") or ".." in name.split("/") or "\\" in name:
            raise BackupError("Archive contains an unsafe path.")
        if not (name in _ALLOWED_ROOTS or name.startswith(("media/", "stores/"))):
            raise BackupError(f"Unexpected file in archive: {name}")
        total += zi.file_size
        if name.endswith("manifest.json") and zi.file_size > MAX_MANIFEST_BYTES:
            raise BackupError("Archive manifest is too large.")
    if total > MAX_ARCHIVE_UNCOMPRESSED:
        raise BackupError("Archive is too large to restore.")


def _wipe(project) -> None:
    from apps.control.starter_content import wipe_storefront_content

    wipe_storefront_content(project)
    # Not covered by wipe_storefront_content:
    apps.get_model("catalog.ProductType").objects.filter(project=project).delete()


def _restore_store_from(zf: zipfile.ZipFile, project, prefix: str, archive: bytes,
                        actor=None) -> dict:
    """Core restore: wipe ``project`` and rebuild from ``{prefix}manifest.json``
    inside the already-open, already-safety-checked ``zf``. Not atomic itself —
    the caller wraps it (per-store for a platform restore)."""
    from apps.core.services import record_audit
    from apps.core.models import AuditLog

    try:
        manifest = json.loads(zf.read(f"{prefix}manifest.json").decode("utf-8"))
    except KeyError as exc:
        raise BackupError("Archive is missing a store manifest.") from exc
    except (ValueError, UnicodeDecodeError) as exc:
        raise BackupError("Archive manifest is corrupt.") from exc
    if manifest.get("format") != FORMAT_VERSION:
        raise BackupError("Unsupported backup format version.")
    data = manifest.get("data", {})
    if len(data.get("product", [])) > MAX_PRODUCTS:
        raise BackupError("Backup has too many products to restore.")

    _wipe(project)

    idmap: dict[str, dict[int, int]] = {}
    self_links: list[tuple] = []   # (Model, new_pk, key, fk_name, old_target_id)
    pending_files: list[tuple] = []  # (Model, new_pk, field, token)
    counts: dict[str, int] = {}

    for entry in _REGISTRY:
        Model = _model(entry)
        key = entry["key"]
        idmap[key] = {}
        rows = data.get(key, [])

        if entry.get("singleton"):
            row = rows[0] if rows else None
            if row is None:
                continue
            defaults = {f: row.get(f) for f in entry["fields"]}
            obj, _ = Model.objects.update_or_create(project=project, defaults=defaults)
            for name in entry.get("files", []):
                tok = row.get(f"__file__{name}")
                if tok:
                    pending_files.append((Model, obj.pk, name, tok))
            counts[key] = 1
            continue

        for row in rows:
            kwargs = {} if entry.get("no_project") else {"project": project}
            for f in entry["fields"]:
                kwargs[f] = row.get(f)
            deferred_self = None
            for fk, target in entry.get("fks", {}).items():
                old = row.get(fk)
                if old is None:
                    kwargs[f"{fk}_id"] = None
                    continue
                if target == "SELF":
                    deferred_self = (fk, old)
                    kwargs[f"{fk}_id"] = None
                else:
                    kwargs[f"{fk}_id"] = idmap.get(target, {}).get(old)
            obj = Model(**kwargs)
            obj.save()
            idmap[key][row["_id"]] = obj.pk
            if deferred_self:
                self_links.append((Model, obj.pk, key, deferred_self[0], deferred_self[1]))
            for name in entry.get("files", []):
                tok = row.get(f"__file__{name}")
                if tok:
                    pending_files.append((Model, obj.pk, name, tok))
        counts[key] = len(rows)

    for Model, new_pk, key, fk, old_target in self_links:
        new_target = idmap[key].get(old_target)
        if new_target:
            Model.objects.filter(pk=new_pk).update(**{f"{fk}_id": new_target})

    def _load_media():
        try:
            zf2 = zipfile.ZipFile(io.BytesIO(archive))
        except zipfile.BadZipFile:
            return
        for Model, new_pk, field, token in pending_files:
            try:
                raw = zf2.read(f"{prefix}media/{token}")
            except KeyError:
                continue
            obj = Model.objects.filter(pk=new_pk).first()
            if obj is None:
                continue
            try:
                getattr(obj, field).save(posixpath.basename(token), ContentFile(raw), save=True)
            except Exception as exc:  # noqa: BLE001 — a bad image must not fail the restore
                logger.warning("restore: media for %s#%s.%s failed: %s",
                               Model.__name__, new_pk, field, exc)

    transaction.on_commit(_load_media)

    try:
        from apps.core.store_resolver import bust_project_chrome

        transaction.on_commit(lambda: bust_project_chrome(project.pk))
    except Exception:  # noqa: BLE001
        pass

    if actor is not None:
        record_audit(actor=actor, project=project, action=AuditLog.Action.UPDATE,
                     target=project, changes={"restored_from": manifest.get("source_project")})

    logger.warning("store %s restored from backup (%s): %s",
                   project.pk, manifest.get("source_project"), counts)
    return counts


@transaction.atomic
def restore_store(project, archive: bytes, *, actor=None) -> dict:
    """Wipe ``project``'s content, then rebuild it from a :func:`dump_store` zip."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(archive))
    except zipfile.BadZipFile as exc:
        raise BackupError("That file is not a valid .zip archive.") from exc
    _safe_zip(zf)
    if "manifest.json" not in zf.namelist():
        if "platform.json" in zf.namelist():
            raise BackupError("That's a full-platform backup — restore it from the platform dashboard.")
        raise BackupError("Archive has no manifest.json — not a store backup.")
    return _restore_store_from(zf, project, "", archive, actor)


def restore_platform(archive: bytes, *, actor=None) -> dict:
    """Restore EVERY store from a :func:`dump_platform` archive. Stores are
    matched to existing ones by slug; a missing store is created. Each store is
    its own transaction — one failure doesn't abort the rest. Returns
    ``{slug: counts | {"error": msg}}``."""
    from apps.projects.models import Project

    try:
        zf = zipfile.ZipFile(io.BytesIO(archive))
    except zipfile.BadZipFile as exc:
        raise BackupError("That file is not a valid .zip archive.") from exc
    _safe_zip(zf)
    try:
        index = json.loads(zf.read("platform.json").decode("utf-8"))
    except KeyError as exc:
        raise BackupError("Not a full-platform backup (no platform.json).") from exc
    except (ValueError, UnicodeDecodeError) as exc:
        raise BackupError("platform.json is corrupt.") from exc
    if index.get("format") != FORMAT_VERSION or index.get("kind") != "platform":
        raise BackupError("Unsupported platform backup.")

    report: dict[str, object] = {}
    for meta in index.get("stores", []):
        slug = (meta.get("slug") or "").strip()
        sid = meta.get("id")
        label = slug or f"store-{sid}"
        if not sid:
            report[label] = {"error": "no store id in backup"}
            continue
        try:
            with transaction.atomic():
                project = Project.objects.filter(slug=slug).first() if slug else None
                if project is None:
                    project = Project.objects.create(
                        name=meta.get("name") or label,
                        slug=slug or None,
                        currency=meta.get("currency") or "INR",
                        country=meta.get("country") or "IN",
                        status=Project.Status.ACTIVE,
                    )
                counts = _restore_store_from(zf, project, f"stores/{sid}/", archive, actor)
            report[label] = counts
        except Exception as exc:  # noqa: BLE001 — record and carry on
            logger.exception("platform restore: store %s failed", label)
            report[label] = {"error": str(exc)}
    logger.warning("platform restore finished: %s stores", len(report))
    return report
