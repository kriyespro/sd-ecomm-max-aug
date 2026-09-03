"""Size & Colour quick builder.

Apparel stores (see ``apps.projects.verticals``) enter sizes and colours as two
plain comma lists on the product editor. This turns those into the existing
``Attribute`` / ``AttributeValue`` / ``Variant`` rows — one variant per
size×colour combo, each with an optional price / sale price / stock override.

Stale combos are *deactivated*, never deleted: ``CartItem.variant`` is PROTECT,
so a variant that was ever added to a cart cannot be removed.
"""

from decimal import Decimal, InvalidOperation

from django.db import transaction

from apps.catalog.models import Attribute, AttributeValue, ProductKind, Variant

SIZE = "Size"
COLOR = "Color"
_AXES = (SIZE, COLOR)
_MAX_VALUES = 40  # per axis — guards against a paste blowing up the matrix
KEY_SEP = "|||"


def parse_list(raw):
    """Comma / newline separated -> ordered, de-duped, trimmed list."""
    out, seen = [], set()
    for chunk in (raw or "").replace("\n", ",").split(","):
        v = chunk.strip()
        if not v:
            continue
        low = v.lower()
        if low in seen:
            continue
        seen.add(low)
        out.append(v)
        if len(out) >= _MAX_VALUES:
            break
    return out


def _dec(val):
    if val in (None, "", "-"):
        return None
    try:
        d = Decimal(str(val))
    except (InvalidOperation, ValueError):
        return None
    return d if d >= 0 else None


def _int(val):
    try:
        return max(0, int(Decimal(str(val))))
    except (InvalidOperation, ValueError, TypeError):
        return 0


def _ensure_attr(project, name):
    attr, _ = Attribute.objects.get_or_create(
        project=project, name=name, defaults={"is_variant": True},
    )
    if not attr.is_variant:
        attr.is_variant = True
        attr.save(update_fields=["is_variant"])
    return attr


def _values_for(attr, names):
    """get_or_create every AttributeValue, returning ``{name: AttributeValue}``
    and fixing their display order to match ``names``."""
    out = {}
    for i, name in enumerate(names):
        av, _ = AttributeValue.objects.get_or_create(
            attribute=attr, value=name, defaults={"order": i},
        )
        if av.order != i:
            av.order = i
            av.save(update_fields=["order"])
        out[name] = av
    return out


def combos(sizes, colors):
    """Desired (size, color) pairs. Either axis may be empty (one-axis product)."""
    if sizes and colors:
        return [(s, c) for s in sizes for c in colors]
    if sizes:
        return [(s, "") for s in sizes]
    if colors:
        return [("", c) for c in colors]
    return []


def combo_key(size, color):
    return f"{size}{KEY_SEP}{color}"


def _all_axis_value_ids(project):
    """Every Size/Colour AttributeValue id this store has ever had — so a
    renamed or dropped option is still recognised as an axis value."""
    return set(
        AttributeValue.objects.filter(
            attribute__in=Attribute.objects.filter(project=project, name__in=_AXES)
        ).values_list("id", flat=True)
    )


@transaction.atomic
def apply_size_color(product, *, sizes, colors, matrix=None):
    """Reconcile ``product``'s variants to the given size/colour lists.

    ``matrix`` maps ``combo_key(size, color)`` -> ``{"price", "sale_price",
    "stock"}`` (any missing / blank -> inherit product price, stock 0).
    Returns ``(created, updated, deactivated)`` counts.
    """
    matrix = matrix or {}
    project = product.project
    sizes = list(sizes or [])
    colors = list(colors or [])

    existing = list(product.variants.prefetch_related("attribute_values"))
    all_axis_ids = _all_axis_value_ids(project)

    def axis_sig(variant):
        """Order-independent set of the axis AttributeValue ids on a variant."""
        return frozenset(
            av.id for av in variant.attribute_values.all() if av.id in all_axis_ids
        )

    if not sizes and not colors:
        # Owner cleared both lists — retire every axis variant.
        deactivated = 0
        for v in existing:
            if v.is_active and axis_sig(v):
                v.is_active = False
                v.save(update_fields=["is_active"])
                deactivated += 1
        return 0, 0, deactivated

    size_vals = _values_for(_ensure_attr(project, SIZE), sizes) if sizes else {}
    color_vals = _values_for(_ensure_attr(project, COLOR), colors) if colors else {}
    all_axis_ids |= {av.id for av in (*size_vals.values(), *color_vals.values())}

    want = combos(sizes, colors)
    want_pairs = {}  # frozenset(av ids) -> (size_str, color_str, [AttributeValue])
    for s, c in want:
        pair = [av for av in (size_vals.get(s), color_vals.get(c)) if av is not None]
        want_pairs[frozenset(av.id for av in pair)] = (s, c, pair)

    by_sig = {}
    for v in existing:
        sig = axis_sig(v)
        if sig:
            by_sig.setdefault(sig, v)

    created = updated = deactivated = 0

    for sig, (s, c, pair) in want_pairs.items():
        row = matrix.get(combo_key(s, c), {}) or {}
        price = _dec(row.get("price"))
        sale = _dec(row.get("sale_price"))
        stock = _int(row.get("stock"))
        label = " / ".join(x for x in (s, c) if x)

        v = by_sig.get(sig)
        if v is None:
            v = Variant.objects.create(
                product=product, name=label, price=price, sale_price=sale,
                stock=stock, is_active=True,
            )
            v.attribute_values.set(pair)
            created += 1
        else:
            v.name, v.price, v.sale_price, v.stock, v.is_active = (
                label, price, sale, stock, True,
            )
            v.save(update_fields=["name", "price", "sale_price", "stock", "is_active"])
            # keep any non-axis values, refresh the axis ones
            keep = [av for av in v.attribute_values.all() if av.id not in all_axis_ids]
            v.attribute_values.set(keep + pair)
            updated += 1

    for v in existing:
        sig = axis_sig(v)
        if v.is_active and sig and sig not in want_pairs:
            v.is_active = False
            v.save(update_fields=["is_active"])
            deactivated += 1

    if product.variants.filter(is_active=True).exists() and product.kind == ProductKind.SIMPLE:
        product.kind = ProductKind.VARIABLE
        product.save(update_fields=["kind"])

    return created, updated, deactivated


def size_color_of(product):
    """Current sizes, colours and per-combo overrides for the editor prefill.

    Returns ``(sizes, colors, rows)`` where ``rows`` is
    ``{combo_key: {"price", "sale_price", "stock"}}`` (strings, blank when unset).
    """
    project = product.project
    attrs = {
        a.name: a
        for a in Attribute.objects.filter(project=project, name__in=_AXES)
    }
    size_attr, color_attr = attrs.get(SIZE), attrs.get(COLOR)

    sizes, colors, rows = [], [], {}
    seen_s, seen_c = set(), set()

    for v in product.variants.filter(is_active=True).prefetch_related(
        "attribute_values__attribute"
    ):
        s = c = ""
        for av in v.attribute_values.all():
            if size_attr and av.attribute_id == size_attr.id:
                s = av.value
            elif color_attr and av.attribute_id == color_attr.id:
                c = av.value
        if not s and not c:
            continue
        if s and s not in seen_s:
            seen_s.add(s)
            sizes.append(s)
        if c and c not in seen_c:
            seen_c.add(c)
            colors.append(c)
        rows[combo_key(s, c)] = {
            "price": "" if v.price is None else f"{v.price:.2f}",
            "sale_price": "" if v.sale_price is None else f"{v.sale_price:.2f}",
            "stock": str(v.stock or 0),
        }
    return sizes, colors, rows


def matrix_from_post(post):
    """Pull ``combo_price[key]`` / ``combo_sale[key]`` / ``combo_stock[key]``
    fields out of a submitted product form."""
    out = {}
    for field, sub in (
        ("combo_price", "price"), ("combo_sale", "sale_price"), ("combo_stock", "stock"),
    ):
        prefix = field + "["
        for name in post:
            if name.startswith(prefix) and name.endswith("]"):
                key = name[len(prefix):-1]
                out.setdefault(key, {})[sub] = post.get(name)
    return out


def storefront_axes(variants):
    """Shape active variants for the product page picker.

    Returns ``None`` when the variants carry no Size/Color axis (caller falls
    back to the flat variant list). Otherwise::

        {"axes": [{"name": "Size", "values": ["S", "M"]}, ...],
         "map": {"S|||Red": {"pk", "price", "sale_price", "stock"}}}
    """
    axes_order, axes_values = [], {}
    vmap = {}
    any_axis = False

    for v in variants:
        s = c = ""
        for av in v.attribute_values.all():
            aname = av.attribute.name
            if aname == SIZE:
                s = av.value
            elif aname == COLOR:
                c = av.value
            else:
                continue
            any_axis = True
            axes_values.setdefault(aname, [])
            if aname not in axes_order:
                axes_order.append(aname)
            if av.value not in axes_values[aname]:
                axes_values[aname].append(av.value)
        vmap[combo_key(s, c)] = {
            "pk": v.pk,
            "price": str(v.effective_price),
            "in_stock": (v.stock or 0) > 0,
            "stock": v.stock or 0,
        }

    if not any_axis:
        return None

    ordered = [n for n in _AXES if n in axes_order]
    return {
        # "options" not "values" — Jinja resolves dict.values to the method.
        "axes": [{"name": n, "options": axes_values[n]} for n in ordered],
        "map": vmap,
        "sep": KEY_SEP,
    }
