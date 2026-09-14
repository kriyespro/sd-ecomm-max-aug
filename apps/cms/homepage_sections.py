"""Per-skin homepage section catalogue + effective-order resolution.

Hero (page-top banner) and Popup (a modal, not a section in the document
flow) are never reorderable. Neither is chrome with no merchant-editable
content behind it (trust strips, value-prop rows, ornza's brand-statement
callout) — those stay exactly where each skin's own template puts them.
Everything else a skin actually renders is listed below, in that skin's
current default order; ``ThemeSettings.homepage_sections`` (a plain list of
these keys) overrides it. An empty list — every store's starting state —
falls back to this canonical order, so an untouched store renders exactly
as it did before this existed.

botanica3's "promo" key only relocates its *first* live promo banner; any
additional ones stay pinned just above the footer (unchanged, pre-existing
behaviour) — reordering a whole skin's layout around multiple banners
scattered mid-page and near the footer isn't a sane single "move".

ornza's own narrative is more fixed by design (brand-statement callout
wedged between "Best sellers" and testimonials) — only "Shop by category"
and "New arrivals" are reorderable there; everything after stays put.
"""

SKIN_SECTIONS = {
    "default": [
        ("promo", "Promo banner"),
        ("categories", "Shop by category"),
        ("featured", "Featured"),
        ("new_arrivals", "New arrivals"),
        ("testimonials", "Testimonials"),
    ],
    "botanica2": [
        ("categories", "Shop by category"),
        ("promo", "Promo banner"),
        ("featured", "Featured"),
        ("benefits", "Why it works"),
        ("new_arrivals", "New arrivals"),
        ("testimonials", "Testimonials"),
        ("newsletter", "Newsletter"),
    ],
    "botanica3": [
        ("categories", "Shop by category"),
        ("budget_bands", "Shop by budget"),
        ("promo", "Promo banner (first one — extra banners stay near the footer)"),
        ("featured", "Featured"),
        ("benefits", "Why it works"),
        ("new_arrivals", "New arrivals"),
        ("testimonials", "Testimonials"),
        ("newsletter", "Newsletter"),
        ("instagram", "Instagram feed"),
    ],
    "ornza": [
        ("categories", "Shop by category"),
        ("new_arrivals", "New arrivals"),
    ],
}


def sections_for_skin(slug):
    """[(key, label), ...] in canonical order for ``slug`` — every other
    skin falls back to "default" (they render default's own templates)."""
    return SKIN_SECTIONS.get(slug, SKIN_SECTIONS["default"])


def section_keys_for_skin(slug):
    return [key for key, _label in sections_for_skin(slug)]


def effective_order(slug, saved):
    """The order a home page actually renders in: ``saved`` (a store's
    ``ThemeSettings.homepage_sections``) filtered to keys this skin still
    has, in the saved order, then any of the skin's own sections missing
    from ``saved`` — new since the store last saved, or never touched —
    appended in canonical order. Never raises on a stale/foreign/empty list."""
    valid = set(section_keys_for_skin(slug))
    ordered = [key for key in (saved or []) if key in valid]
    ordered += [key for key in section_keys_for_skin(slug) if key not in ordered]
    return ordered


def move(slug, saved, key, direction):
    """``effective_order`` with ``key`` swapped one place toward
    ``direction`` ("up" or "down"). A no-op at either end, or for an
    unknown key/direction."""
    order = effective_order(slug, saved)
    if key not in order:
        return order
    idx = order.index(key)
    step = -1 if direction == "up" else 1 if direction == "down" else 0
    swap_idx = idx + step
    if step and 0 <= swap_idx < len(order):
        order[idx], order[swap_idx] = order[swap_idx], order[idx]
    return order
