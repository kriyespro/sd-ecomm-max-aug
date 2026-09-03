"""Store vertical — the one thing the owner picks during onboarding.

Kept in ``project.feature_flags["vertical"]`` (JSON, no migration) alongside the
``onboarded`` gate. Apparel verticals unlock the Size & Color option builder on
the product editor.
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


def vertical_of(project):
    return ((getattr(project, "feature_flags", None) or {}).get("vertical") or "").strip().lower()


def vertical_label(project):
    return _LABELS.get(vertical_of(project), "")


def wants_size_color(project):
    """Does this store's vertical use size/colour variants?"""
    return vertical_of(project) in APPAREL


def is_onboarded(project):
    return bool((getattr(project, "feature_flags", None) or {}).get("onboarded"))
