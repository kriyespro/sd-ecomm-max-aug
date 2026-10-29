"""Per-skin catalogue of the home page's editable heading texts.

``ThemeSettings.section_titles`` stores only the merchant's overrides — a
plain ``{key: text}`` dict. Any key absent from it renders that heading's own
hardcoded default text (listed here, matching the literal text each skin's
``home.jinja`` already renders), so an untouched store's headings are
unchanged. Distinct from ``apps.cms.homepage_sections``, which only covers
the subset of sections a merchant can reorder — every heading a skin renders
on the home page gets a title key here, reorderable or not (e.g. ornza's
fixed "Best sellers").
"""

SKIN_TITLES = {
    "default": [
        ("categories", "Shop by category"),
        ("featured", "Featured"),
        ("new_arrivals", "New arrivals"),
        ("testimonials", "What people say"),
    ],
    "botanica2": [
        ("categories", "Shop by category"),
        ("featured_eyebrow", "Best sellers"),
        ("featured", "Loved by the community"),
        ("benefits", "Why it works"),
        ("new_arrivals", "New arrivals"),
        ("testimonials", "What people say"),
        ("newsletter_eyebrow", "Join the ritual"),
        ("newsletter", "Get 10% off your first order"),
    ],
    "botanica3": [
        ("categories", "Shop by category"),
        ("categories_top", "Browse the shop"),
        ("budget_bands", "Shop by budget"),
        ("featured_eyebrow", "Best sellers"),
        ("featured", "Loved by the community"),
        ("benefits", "Why it works"),
        ("new_arrivals", "New arrivals"),
        ("testimonials", "What people say"),
        ("newsletter_eyebrow", "Join the ritual"),
        ("newsletter", "Get 10% off your first order"),
        ("instagram", "From our Instagram"),
    ],
    "ornza": [
        ("categories", "Shop by category"),
        ("new_arrivals", "New arrivals"),
        ("bestsellers", "Best sellers"),
        ("testimonials", "What our customers say"),
    ],
    "zyvira": [
        ("categories", "Shop by category"),
        ("categories_top", "Browse the shop"),
        ("budget_bands", "Shop by budget"),
        ("featured", "New Arrivals - Easy to fall for"),
        ("featured_sub", "Meet the newest pieces waiting to become your next favourite."),
        ("benefits", "Why it works"),
        ("new_arrivals", "New arrivals"),
        ("new_arrivals_sub", "Fresh in — before everyone else sees them."),
        ("testimonials", "What people say"),
        ("newsletter_eyebrow", "Join the Circle"),
        ("newsletter", "Get 10% off your first order"),
        ("instagram", "From our Instagram"),
    ],
    "vireej2-0": [
        ("categories", "Shop Jewellery by Category"),
        ("featured", "New Arrivals — Easy to Fall For"),
        ("featured_sub", "Meet the newest pieces waiting to become your next favourite."),
        ("budget_bands", "Little Luxuries Under ₹499"),
        ("benefits", "What Makes Special"),
        ("new_arrivals", "Trending Glow Picks"),
        ("new_arrivals_sub", "The pieces everyone keeps adding to bag."),
        ("testimonials", "What People Say"),
        ("instagram", "The Edit"),
    ],
}


def titles_for_skin(slug):
    """[(key, default_text), ...] for ``slug`` — every other skin falls back
    to "default" (they render default's own templates)."""
    return SKIN_TITLES.get(slug, SKIN_TITLES["default"])
