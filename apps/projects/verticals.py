"""Store vertical — the one thing the owner picks during onboarding.

Kept in ``project.feature_flags["vertical"]`` (JSON, no migration) alongside the
``onboarded`` gate. Apparel verticals unlock the Size & Color option builder on
the product editor; jewellery gets the same builder relabelled to ring/bangle
size, with the Colour half of it hidden (it's a one-axis product).
"""

VERTICALS = [
    ("fashion", "Fashion & accessories"),
    ("jewellery", "Jewellery"),
    ("clothing", "Clothing"),
    ("fmcg", "FMCG / grocery"),
]

_LABELS = dict(VERTICALS)

# Verticals that get the Size & Color quick builder + storefront pickers.
APPAREL = {"clothing", "fashion"}

# Jewellery gets the same builder (see apps.catalog.variants — it already
# handles a size-only product with no colour axis), just relabelled to ring/
# bangle size on the product form and without the Colour field.
JEWELLERY = {"jewellery"}

SIZE_BUILDER_VERTICALS = APPAREL | JEWELLERY


def vertical_of(project):
    return ((getattr(project, "feature_flags", None) or {}).get("vertical") or "").strip().lower()


def vertical_label(project):
    return _LABELS.get(vertical_of(project), "")


def wants_size_color(project):
    """Does this store's vertical use the Size & Colour / ring-bangle-size
    quick builder?"""
    return vertical_of(project) in SIZE_BUILDER_VERTICALS


def wants_jewellery_sizes(project):
    """Jewellery variant: same builder, ring/bangle-size labelling, no
    Colour field."""
    return vertical_of(project) in JEWELLERY


def is_onboarded(project):
    return bool((getattr(project, "feature_flags", None) or {}).get("onboarded"))
