"""Starter (demo) content for a brand-new store.

Called from :func:`apps.control.store_services.create_store` so a store's
storefront reads as finished the moment it exists: real, editable rows —
categories, products, banners, pages, a store profile, a few reviews — with
every *text* field filled and every *image* field deliberately left blank so
the skins render a sized "1600×900" placeholder telling the owner exactly what
to upload where.

Idempotent: the PKs it creates are recorded on ``project.feature_flags`` so
:func:`remove_starter_content` can take it all back out again (the "Remove demo
content" action in Mission Control) without touching anything the owner added.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from django.db import transaction

logger = logging.getLogger(__name__)

_FLAG = "demo_seeded"
_REF = "demo_content"

# --- content ---------------------------------------------------------------

CATEGORIES = [
    ("New Arrivals", "The latest additions — restocked every week."),
    ("Home & Living", "Considered pieces for a calmer, warmer home."),
    ("Kitchen & Dining", "Tools and tableware made to be used every day."),
    ("Outdoor", "Built for the balcony, the garden and the trail."),
    ("Gifting", "Ready-to-give favourites for every occasion."),
    ("Sale", "Last-chance pricing on a rotating edit."),
]

# title, price, sale_price, category, short description, long description,
# is_featured, is_new_arrival
PRODUCTS = [
    ("Stoneware Dinner Plate", 690, None, "Kitchen & Dining",
     "Reactive-glaze stoneware, 27 cm. Sold individually.",
     "<p>Each plate is dipped by hand, so no two glazes break exactly the "
     "same way. Chip-resistant stoneware, safe for the dishwasher, microwave "
     "and oven to 220°C.</p><ul><li>27 cm diameter</li><li>Stacks flat</li>"
     "<li>Lead-free reactive glaze</li></ul>", True, False),
    ("Linen Table Napkin — Set of 4", 1290, 990, "Kitchen & Dining",
     "Stonewashed European flax, 45 × 45 cm.",
     "<p>Pure flax linen, garment-washed for a soft, lived-in hand from the "
     "first use. Generously cut with a mitred hem. Gets better every wash.</p>",
     False, False),
    ("Ribbed Glass Tumbler", 390, None, "Kitchen & Dining",
     "Pressed soda-lime glass, 300 ml. Stackable.",
     "<p>An everyday tumbler with a vertical rib that catches the light and "
     "hides water marks. Balanced weight, comfortable in the hand.</p>",
     False, True),
    ("Cotton Waffle Throw", 2490, None, "Home & Living",
     "Yarn-dyed cotton waffle, 130 × 170 cm.",
     "<p>A lightweight throw for the sofa or the foot of the bed. Open waffle "
     "weave in long-staple cotton with a hand-knotted fringe.</p>", True, False),
    ("Terracotta Plant Pot", 850, None, "Outdoor",
     "Unglazed terracotta with drainage hole and saucer. 16 cm.",
     "<p>Breathable unglazed clay that wicks away excess water and keeps roots "
     "healthy. Develops a natural patina outdoors. Matching saucer included.</p>",
     False, True),
    ("Beeswax Candle — Amber & Moss", 1190, None, "Gifting",
     "Blended soy-and-beeswax, ~45 hour burn.",
     "<p>Hand-poured in small batches with a cotton wick and a warm, woody "
     "scent — amber, oakmoss and a little black pepper. Reusable glass vessel.</p>",
     True, False),
    ("Merino Lambswool Scarf", 3290, 2490, "Gifting",
     "Woven lambswool, 30 × 190 cm. Comes gift-boxed.",
     "<p>Soft, warm and light, woven on traditional looms and finished with a "
     "twisted fringe. Arrives in a recyclable gift box.</p>", False, False),
    ("Solid Oak Serving Board", 1690, None, "Home & Living",
     "Oiled European oak, 40 × 20 cm. Each one unique.",
     "<p>Cut from a single piece of oak and finished with food-safe oil. Use "
     "it for bread and cheese, or as a trivet. Re-oil once or twice a year.</p>",
     False, False),
]

# key -> (name, placement, heading, subheading, cta_label, cta_url, category)
BANNERS = [
    ("hero", "hero", "New season, warmer home",
     "Considered homeware, made to be used",
     "Shop new arrivals", "/shop/?sort=new", None),
    ("promo1", "promo", "The table edit",
     "Plates, linen and glass that go with everything",
     "Shop kitchen & dining", "/shop/?category=kitchen-dining", None),
    ("promo2", "promo", "Up to 30% off in the Sale",
     "A rotating edit — while stock lasts",
     "Shop the Sale", "/shop/?category=sale", None),
    ("announcement", "announcement", "Free shipping over ₹999 · Cash on delivery across India",
     "", "", "", None),
    ("popup", "popup", "Get 10% off your first order",
     "Join the list for restocks and members-only drops.",
     "Sign me up", "/shop/", None),
    ("cat-living", "category", "Home & Living",
     "A calmer, warmer home", "", "", "Home & Living"),
]

# kind -> (title, body html)
PAGES = [
    ("about", "About us",
     "<p>We started with a simple idea: everyday objects should be well made, "
     "honestly priced and pleasant to live with. Everything we sell is chosen "
     "with that test in mind.</p><p>Replace this text with your own story from "
     "<strong>Storefront → Pages</strong>.</p>"),
    ("shipping_policy", "Shipping",
     "<p>Orders are dispatched within 24–48 hours. Standard delivery takes "
     "3–6 working days across India. Free over ₹999; a flat ₹79 below that.</p>"
     "<p>Cash on delivery is available nationwide.</p>"),
    ("return_policy", "Returns & exchanges",
     "<p>Not right? Return any unused item in its original packaging within "
     "7 days for a full refund or exchange. Start a return from your account "
     "or write to us.</p>"),
    ("contact", "Contact",
     "<p>We reply within one working day.</p><p>Email: hello@example.com<br>"
     "Phone / WhatsApp: +91 90000 00000<br>Hours: Mon–Sat, 10am–6pm IST</p>"),
    ("faq", "FAQ",
     "<p><strong>Do you ship outside India?</strong><br>Not yet — India only "
     "for now.</p><p><strong>Can I change my order?</strong><br>Yes, if it "
     "hasn't shipped. Contact us as soon as you can.</p>"),
]

# product index (into PRODUCTS) -> (author, rating, title, body)
REVIEWS = [
    (0, 5, "Exactly as pictured", "Lovely weight and the glaze is beautiful. "
     "Bought four, will buy more."),
    (3, 5, "So soft", "Warmer than it looks and not bulky. Lives on the sofa now."),
    (5, 4, "Great scent, not overpowering", "Fills the room without being sweet. "
     "Burn time was as described."),
    (1, 5, "Better every wash", "Started soft, now softer. The colour is a "
     "perfect muted grey."),
]

STORE_PROFILE = {
    "tagline": "Well-made things for everyday life",
    "support_email": "hello@example.com",
    "support_phone": "+91 90000 00000",
    "whatsapp": "+91 90000 00000",
    "address": "Demo Lane 12, Bengaluru, Karnataka 560001",
    "instagram_url": "https://instagram.com/",
    "facebook_url": "https://facebook.com/",
    "copyright_text": "",
}


# --- api ------------------------------------------------------------------

def is_seeded(project) -> bool:
    return bool(project.feature_flags.get(_FLAG))


@transaction.atomic
def seed_starter_content(project, *, force: bool = False) -> dict:
    """Create the demo rows for ``project``. No-op if already seeded (unless
    ``force``). Returns the map of created PKs."""
    from apps.categories.models import Category
    from apps.catalog.models import Product
    from apps.cms.models import Banner, Page, PublishStatus, StoreProfile, ThemeSettings
    from apps.reviews.models import Review, ReviewStatus
    from apps.reviews.services import refresh_product_rating

    if is_seeded(project) and not force:
        return project.feature_flags.get(_REF, {})

    ref: dict[str, list[int]] = {
        "categories": [], "products": [], "banners": [], "pages": [], "reviews": [],
    }

    cats: dict[str, Category] = {}
    for order, (name, desc) in enumerate(CATEGORIES):
        cat, _ = Category.objects.get_or_create(
            project=project, name=name,
            defaults={"description": desc, "order": order, "is_active": True},
        )
        cats[name] = cat
        ref["categories"].append(cat.pk)

    prods: list[Product] = []
    for (title, price, sale, cat_name, short, body, feat, new) in PRODUCTS:
        p = Product.objects.create(
            project=project, title=title,
            price=Decimal(str(price)),
            sale_price=Decimal(str(sale)) if sale is not None else None,
            category=cats.get(cat_name),
            status="active", search_indexed=True,
            short_description=short, description=body,
            is_featured=feat, is_new_arrival=new,
        )
        prods.append(p)
        ref["products"].append(p.pk)

    for (name, placement, heading, subheading, cta_label, cta_url, cat_name) in BANNERS:
        b = Banner.objects.create(
            project=project, name=f"{name} (demo)", placement=placement,
            heading=heading, subheading=subheading,
            cta_label=cta_label, cta_url=cta_url,
            category=cats.get(cat_name) if cat_name else None,
            is_active=True,
            priority=90 if placement in ("hero", "announcement", "popup") else 100,
        )
        ref["banners"].append(b.pk)

    for kind, title, body in PAGES:
        pg, created = Page.objects.get_or_create(
            project=project, kind=kind,
            defaults={"title": title, "body": body,
                      "status": PublishStatus.PUBLISHED},
        )
        if created:
            ref["pages"].append(pg.pk)

    StoreProfile.objects.update_or_create(project=project, defaults=STORE_PROFILE)
    ThemeSettings.objects.get_or_create(project=project)

    for idx, rating, r_title, r_body in REVIEWS:
        if idx >= len(prods):
            continue
        r = Review.objects.create(
            project=project, product=prods[idx],
            author_name="Verified buyer", author_email="buyer@example.com",
            rating=rating, title=r_title, body=r_body,
            status=ReviewStatus.APPROVED, is_verified_purchase=True,
        )
        ref["reviews"].append(r.pk)
    for p in {prods[i[0]] for i in REVIEWS if i[0] < len(prods)}:
        refresh_product_rating(p)

    project.feature_flags[_FLAG] = True
    project.feature_flags[_REF] = ref
    project.save(update_fields=["feature_flags"])
    _bust(project)
    logger.info("starter content seeded for project %s", project.pk)
    return ref


@transaction.atomic
def remove_starter_content(project) -> None:
    """Delete exactly the rows :func:`seed_starter_content` created, then clear
    the flag. Rows the owner added are untouched."""
    from apps.categories.models import Category
    from apps.catalog.models import Product
    from apps.cms.models import Banner, Page
    from apps.reviews.models import Review

    ref = project.feature_flags.get(_REF) or {}
    Review.objects.filter(project=project, pk__in=ref.get("reviews", [])).delete()
    Product.objects.filter(project=project, pk__in=ref.get("products", [])).delete()
    Banner.objects.filter(project=project, pk__in=ref.get("banners", [])).delete()
    Page.objects.filter(project=project, pk__in=ref.get("pages", [])).delete()
    Category.objects.filter(project=project, pk__in=ref.get("categories", [])).delete()

    project.feature_flags.pop(_FLAG, None)
    project.feature_flags.pop(_REF, None)
    project.save(update_fields=["feature_flags"])
    _bust(project)
    logger.info("starter content removed for project %s", project.pk)


@transaction.atomic
def wipe_storefront_content(project) -> dict:
    """Delete this store's catalogue + CMS content so a clean demo set can take
    its place. Destructive — used only behind a type-"DELETE" confirmation.

    Removes: products (+ images / inventory / wishlist entries), categories,
    brands, banners, pages, FAQs, menus, content blocks, reviews, and every
    shopping cart (cart lines PROTECT products). Leaves orders, customers,
    coupons, shipping, payments, domains, team and theme untouched — order
    lines keep their snapshot and just lose the product link.
    """
    from apps.cart.models import Cart
    from apps.catalog.models import Brand, Product
    from apps.categories.models import Category
    from apps.cms.models import Banner, ContentBlock, FAQ, Menu, Page
    from apps.reviews.models import Review

    counts = {}
    # carts first — CartItem.product is PROTECT
    counts["carts"] = Cart.objects.filter(project=project).delete()[0]
    for key, model in (
        ("reviews", Review), ("products", Product), ("brands", Brand),
        ("categories", Category), ("banners", Banner), ("pages", Page),
        ("faqs", FAQ), ("menus", Menu), ("blocks", ContentBlock),
    ):
        counts[key] = model.objects.filter(project=project).delete()[0]
    project.feature_flags.pop(_FLAG, None)
    project.feature_flags.pop(_REF, None)
    project.save(update_fields=["feature_flags"])
    _bust(project)
    logger.warning("storefront content wiped for project %s: %s", project.pk, counts)
    return counts


@transaction.atomic
def reset_and_seed(project) -> dict:
    """Wipe the storefront, then lay down a fresh demo set — atomically, so a
    seeding failure can't leave the store empty. The "import demo content"
    action offered to existing stores."""
    wipe_storefront_content(project)
    return seed_starter_content(project, force=True)


def _bust(project) -> None:
    try:
        from apps.core.store_resolver import bust_project_chrome

        bust_project_chrome(project.pk)
    except Exception:  # noqa: BLE001
        pass
