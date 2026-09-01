"""Validate and persist an uploaded skin bundle (zip).

The zip contains the converted Jinja2 skin — templates + ``assets/``. This runs
blunt static checks; the real gate is the mandatory platform-admin review before
the skin can render, plus the ``ImmutableSandboxedEnvironment`` at render time.
"""

import io
import os
import re
import zipfile

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils.text import slugify

from apps.shopfront.sandbox import missing_required

from .models import Skin, SkinAsset, SkinFile, SkinSource, SkinStatus

MAX_ZIP = 10 * 1024 * 1024
MAX_FILES = 250
MAX_TEMPLATE_BYTES = 400_000
MAX_ASSET_BYTES = 1_500_000
MAX_TOTAL_UNCOMPRESSED = 30 * 1024 * 1024   # decompression-bomb guard
MAX_COMPRESSION_RATIO = 120                  # per-file zip-bomb guard

# SVG can carry <script>/on*= handlers — strip them (media may be same-origin).
_SVG_SCRIPT = re.compile(r"<script[\s\S]*?</script\s*>", re.I)
_SVG_EVENT = re.compile(r"\son[a-z]+\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s>]+)", re.I)
_SVG_HREF_JS = re.compile(r"(href|xlink:href)\s*=\s*(?:\"|')?\s*javascript:[^\"'>]*", re.I)
_SVG_FOREIGN = re.compile(r"<foreignObject[\s\S]*?</foreignObject\s*>", re.I)

TEMPLATE_EXT = {".jinja"}
ASSET_EXT = {
    ".css", ".js", ".mjs", ".map", ".svg", ".png", ".jpg", ".jpeg", ".webp",
    ".gif", ".avif", ".ico", ".woff", ".woff2", ".ttf", ".otf", ".json", ".txt",
}

# Blunt denylist. Not the security boundary (the sandbox + mandatory human
# review are), just an early clear rejection of the obvious SSTI probes.
# Identifier checks only look *inside* Jinja delimiters, so plain-English copy
# ("gift wrapping on request") and CSS/JS tokens ("tailwind.config",
# "self-start") don't trip them.
_JINJA_SPAN = re.compile(r"\{\{.*?\}\}|\{%.*?%\}", re.DOTALL)
_STR_LITERAL = re.compile(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"")

_EXPR_BAD = re.compile(
    r"(?<![\w.])(?:self|config|request|settings|lipsum|cycler|namespace|joiner"
    r"|subclasses|globals|builtins|getattr|setattr|eval|exec|breakpoint)\b"
    r"|__[A-Za-z_]+__"
)

_RAW_BAD = re.compile(
    r"""
      \{%\s*import\b
    | \{%\s*from\b
    | \{%\s*(?:extends|include)\s+['"](?!base\.jinja|partials/|not_found\.jinja)
    | __import__ | __class__ | __subclasses__ | __mro__ | __globals__ | __builtins__
    """,
    re.VERBOSE,
)

_UNSAFE_SEG = {"", ".", ".."}


def _norm_path(name):
    name = name.replace("\\", "/").strip()
    if name.startswith("/") or ":" in name:
        raise ValidationError(f"Unsafe path in zip: {name!r}")
    parts = name.split("/")
    if any(seg in _UNSAFE_SEG for seg in parts[:-1]) or ".." in parts:
        raise ValidationError(f"Unsafe path in zip: {name!r}")
    return name.lstrip("./")


def _scan_template(path, text):
    m = _RAW_BAD.search(text)
    if m is None:
        for span in _JINJA_SPAN.finditer(text):
            code = _STR_LITERAL.sub("''", span.group(0))  # drop string data
            m = _EXPR_BAD.search(code)
            if m is not None:
                break
    if m is not None:
        raise ValidationError(
            f"{path}: contains a disallowed construct near {m.group(0)!r}. "
            "See THEME_GUIDE.md — skins may not touch Python internals or "
            "template names outside the skin."
        )


def _strip_wrapper_dir(names):
    tops = {n.split("/", 1)[0] for n in names if "/" in n}
    if len(tops) == 1 and all("/" in n for n in names):
        return next(iter(tops)) + "/"
    return ""


def parse_bundle(fileobj):
    """Return ``(templates: {path: text}, assets: {path: bytes}, meta: dict)``."""
    data = fileobj.read()
    if len(data) > MAX_ZIP:
        raise ValidationError("Bundle is over 2 MB.")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ValidationError("Not a valid zip file.")

    infos = [i for i in zf.infolist() if not i.is_dir()]
    if len(infos) > MAX_FILES:
        raise ValidationError(f"Too many files ({len(infos)} > {MAX_FILES}).")

    # Decompression-bomb guards — checked against the zip header, before any read.
    total_uncompressed = sum(i.file_size for i in infos)
    if total_uncompressed > MAX_TOTAL_UNCOMPRESSED:
        raise ValidationError("Bundle expands to too much data.")
    for i in infos:
        if i.file_size > MAX_ASSET_BYTES:
            raise ValidationError(f"{i.filename}: file is too large.")
        if i.compress_size and i.file_size / i.compress_size > MAX_COMPRESSION_RATIO:
            raise ValidationError(f"{i.filename}: suspicious compression ratio.")

    names = [i.filename for i in infos]
    prefix = _strip_wrapper_dir(names)

    templates, assets, meta = {}, {}, {}
    for info in infos:
        rel = _norm_path(info.filename[len(prefix):] if prefix and info.filename.startswith(prefix) else info.filename)
        if not rel or rel.startswith(("__MACOSX/", ".")):
            continue
        ext = os.path.splitext(rel)[1].lower()
        raw = zf.read(info)

        if rel == "theme.json":
            import json
            try:
                meta = json.loads(raw.decode("utf-8"))
            except Exception:  # noqa: BLE001
                meta = {}
            continue

        if ext in TEMPLATE_EXT:
            if len(raw) > MAX_TEMPLATE_BYTES:
                raise ValidationError(f"{rel} is too large.")
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                raise ValidationError(f"{rel} is not valid UTF-8.")
            _scan_template(rel, text)
            templates[rel] = text
        elif ext in ASSET_EXT:
            if len(raw) > MAX_ASSET_BYTES:
                raise ValidationError(f"Asset {rel} is too large.")
            if ext == ".svg":
                raw = _sanitise_svg(raw)
            key = rel.split("assets/", 1)[-1] if "assets/" in rel else rel
            assets[key] = raw
        # anything else: ignored

    if not templates:
        raise ValidationError("No .jinja templates found in the bundle.")

    gaps = missing_required(templates)
    if gaps:
        raise ValidationError("Missing required templates: " + ", ".join(gaps))

    _compile_check(templates)
    return templates, assets, meta


def _sanitise_svg(raw: bytes) -> bytes:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw
    for pat in (_SVG_SCRIPT, _SVG_FOREIGN, _SVG_EVENT, _SVG_HREF_JS):
        text = pat.sub("", text)
    return text.encode("utf-8")


def _compile_check(templates):
    from jinja2 import TemplateAssertionError, TemplateSyntaxError
    from jinja2.loaders import DictLoader
    from jinja2.sandbox import ImmutableSandboxedEnvironment

    env = ImmutableSandboxedEnvironment(
        loader=DictLoader(templates), autoescape=True, auto_reload=False
    )
    env.globals.update({"url": lambda *a, **k: "", "asset": lambda *a, **k: ""})
    env.filters["money"] = lambda v, *a, **k: v
    env.filters["date"] = lambda v, *a, **k: v
    for path in templates:
        try:
            env.get_template(path)
        except (TemplateSyntaxError, TemplateAssertionError) as exc:
            raise ValidationError(f"{path}: template error — {exc.message}")


_CT = {
    ".css": "text/css", ".js": "text/javascript", ".mjs": "text/javascript",
    ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif",
    ".woff2": "font/woff2", ".woff": "font/woff", ".ttf": "font/ttf",
    ".ico": "image/x-icon", ".json": "application/json",
}


@transaction.atomic
def create_skin_from_upload(*, project, user, fileobj, label):
    templates, assets, meta = parse_bundle(fileobj)

    label = (label or meta.get("name") or "Uploaded skin").strip()[:120]
    base = f"{project.slug}-{slugify(label)}"[:52] or f"store{project.id}-skin"
    slug, n = base, 2
    while Skin.objects.filter(slug=slug).exists():
        slug = f"{base}-{n}"
        n += 1

    skin = Skin.objects.create(
        slug=slug, label=label,
        description=meta.get("description", "")[:2000],
        source=SkinSource.UPLOAD, is_sandboxed=True,
        status=SkinStatus.PENDING, is_active=True, is_default=False,
        project=project, uploaded_by=user,
        version=str(meta.get("version", ""))[:20],
        author=str(meta.get("author", ""))[:120],
    )
    SkinFile.objects.bulk_create(
        [SkinFile(skin=skin, path=p, content=c) for p, c in templates.items()]
    )
    for path, raw in assets.items():
        ext = os.path.splitext(path)[1].lower()
        SkinAsset.objects.create(
            skin=skin, path=path, content_type=_CT.get(ext, ""),
            file=ContentFile(raw, name=os.path.basename(path)),
        )
    return skin
