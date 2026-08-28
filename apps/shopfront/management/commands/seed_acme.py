"""Seed the Acme Store with a full, image-backed catalogue for the shopfront.

Product + hero images are pulled once from Unsplash (free stock) and stored in
MEDIA_ROOT, so the storefront serves them locally afterwards. If a download
fails, it falls back to a Pillow-generated gradient tile. ``--refresh`` re-fetches
images for products that already have them. Idempotent.
"""

import colorsys
import hashlib
import io
import subprocess
from decimal import Decimal

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils.text import slugify
from PIL import Image, ImageDraw, ImageFont

from apps.catalog.models import Product, ProductImage
from apps.categories.models import Category
from apps.cms.models import Banner, Page, PublishStatus, ThemeSettings
from apps.coupons.models import Coupon, DiscountType
from apps.inventory import services as inv
from apps.inventory.models import Warehouse
from apps.payments.models import PaymentProviderConfig, Provider
from apps.projects.models import Domain, Project
from apps.reviews import services as reviews_svc
from apps.reviews.models import Review, ReviewStatus
from apps.shipping.models import RateType, ShippingMethod, ShippingZone

# --- catalogue --------------------------------------------------

CATALOGUE = {
    "Apparel": [
        ("Everyday Oxford Shirt", 2490, None, "Washed cotton oxford with a relaxed collar. Wears in beautifully.", True, True),
        ("Merino Crew Sweater", 4990, 3990, "Fine-gauge extra-fine merino. Warm without weight.", True, False),
        ("Linen Camp Shirt", 2790, None, "Breathable European linen, camp collar, boxy cut.", False, True),
        ("Heavyweight Tee — 3 Pack", 1990, 1590, "230gsm supima cotton. Structured, not stiff.", True, False),
        ("Tailored Chino", 2990, None, "Two-way stretch twill, clean tapered leg.", False, False),
        ("Selvedge Denim", 5490, None, "14oz Japanese selvedge, mid-rise straight.", True, False),
        ("Waffle Henley", 2290, 1790, "Cotton-modal waffle knit, three-button placket.", False, True),
        ("Cashmere Beanie", 2790, None, "Pure Mongolian cashmere, ribbed cuff.", False, False),
    ],
    "Footwear": [
        ("Leather Derby", 7990, None, "Goodyear-welted calf leather. Resoleable.", True, False),
        ("Suede Chukka", 6490, 4990, "Water-repellent suede, crepe sole.", True, False),
        ("Canvas Low-Top", 2990, None, "Organic cotton canvas, vulcanised sole.", False, True),
        ("Trail Runner", 5990, None, "Recycled knit upper, responsive foam midsole.", False, True),
    ],
    "Accessories": [
        ("Leather Card Holder", 1490, None, "Vegetable-tanned leather, three slots.", False, False),
        ("Woven Stretch Belt", 1290, 990, "Elastic weave, matte metal buckle.", False, False),
        ("Silk Grenadine Tie", 2190, None, "Hand-rolled Como silk, untipped.", False, False),
        ("Lambswool Scarf", 2490, None, "Brushed lambswool, generous 200cm drop.", True, False),
        ("Acetate Sunglasses", 3490, 2790, "Italian acetate, polarised lenses.", True, True),
    ],
    "Bags": [
        ("Weekender Duffel", 8990, None, "Waxed canvas and leather, 40L.", True, False),
        ("Structured Leather Tote", 9990, 7990, "Full-grain leather, laptop sleeve.", True, False),
        ("Roll-Top Backpack", 5490, None, "Ripstop nylon, 20L, weatherproof.", False, True),
        ("Travel Dopp Kit", 1990, None, "Wipe-clean lining, hanging hook.", False, False),
    ],
    "Home": [
        ("Stoneware Mug — Set of 2", 1690, None, "Reactive glaze, 350ml, dishwasher safe.", False, True),
        ("Linen Waffle Throw", 3990, 2990, "Stonewashed linen-cotton, 130×170cm.", True, False),
        ("Cedar & Amber Candle", 1890, None, "Coconut-soy wax, 55hr burn.", False, False),
        ("Lambswool Blanket", 6490, None, "Woven in a mill est. 1837. 150×200cm.", True, False),
    ],
}

PAGES = [
    ("about", "About Acme", "<p>Acme makes considered everyday essentials — built to last, priced fairly.</p>"),
    ("contact", "Contact", "<p>help@acme.example · Mon–Fri, 9–6.</p>"),
    ("shipping_policy", "Shipping", "<p>Free shipping over ₹999. Standard 3–7 days. COD available.</p>"),
    ("return_policy", "Returns", "<p>30-day returns on unworn items with tags.</p>"),
    ("privacy", "Privacy", "<p>We keep only what we need. Demo content.</p>"),
    ("terms", "Terms", "<p>Standard terms of sale. Demo content.</p>"),
]


# Unsplash photo IDs per category (free to hotlink; we download once and store).
UNSPLASH = {
    "Apparel": ["1596755094514-f87e34085b2c", "1521572163474-6864f9cf17ab",
                "1620799140408-edc6dcb6d633", "1489987707025-afc232f7ea0f",
                "1434389677669-e08b4cac3105", "1618886983594-40e2adc81b48"],
    "Footwear": ["1549298916-b41d501d3772", "1560769629-975ec94e6a86",
                 "1595950653106-6c9ebd614d3a", "1543163521-1bf539c55dd2",
                 "1608231387042-66d1773070a5"],
    "Accessories": ["1524805444758-089113d48a6d", "1611591437281-460bfbe1220a",
                    "1509941943102-10c1b39a54f2", "1601924994987-69e26d50dc26",
                    "1600950207944-0d63e8edbc3f"],
    "Bags": ["1553062407-98eeb64c6a62", "1548036328-c9fa89d128fa",
             "1547949003-9792a18a2601", "1590874103328-eac38a683ce7",
             "1622560480605-d83c853bc5c3"],
    "Home": ["1556228578-8c89e6adf883", "1493666438817-866a91353ca9",
             "1519710164239-da123dc03ef4", "1584100936595-c0654b55a2e6",
             "1600585154340-be6161a56a0c"],
}
HERO_PHOTO = "1441984904996-e0b6ba687e04"


def _unsplash_url(photo_id, w, h):
    return (f"https://images.unsplash.com/photo-{photo_id}"
            f"?ixlib=rb-4.0.3&auto=format&fit=crop&w={w}&h={h}&q=80")


def _download(url, timeout=20):
    """Fetch bytes via curl (uses the system trust store — the framework Python
    build here has no CA bundle for urllib). Returns None on any failure.
    """
    try:
        proc = subprocess.run(
            ["curl", "-sSL", "--max-time", str(timeout),
             "-A", "Mozilla/5.0 (seed-acme)", url],
            capture_output=True, timeout=timeout + 5,
        )
        data = proc.stdout
        if proc.returncode != 0 or len(data) < 1024:
            return None
        Image.open(io.BytesIO(data)).verify()
        return data
    except Exception:  # noqa: BLE001 - best effort; fall back to a generated tile
        return None


def _font(size):
    for path in (
        "/System/Library/Fonts/Supplemental/Georgia.ttf",
        "/System/Library/Fonts/Supplemental/Times New Roman.ttf",
        "/Library/Fonts/Georgia.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
    ):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _tones(slug):
    h = int(hashlib.md5(slug.encode()).hexdigest(), 16) % 360 / 360
    light = tuple(int(c * 255) for c in colorsys.hls_to_rgb(h, 0.86, 0.30))
    deep = tuple(int(c * 255) for c in colorsys.hls_to_rgb(h, 0.62, 0.34))
    return light, deep


def _make_image(title, slug, variant=0):
    size = (1000, 1250)
    light, deep = _tones(slug + str(variant))
    mask = Image.linear_gradient("L").rotate(variant * 90, expand=True).resize(size)
    img = Image.composite(Image.new("RGB", size, deep), Image.new("RGB", size, light), mask)
    d = ImageDraw.Draw(img, "RGBA")

    cx, cy = size[0] // 2, int(size[1] * 0.44)
    r = 300
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(255, 255, 255, 90), width=3)
    d.ellipse([cx - r + 26, cy - r + 26, cx + r - 26, cy + r - 26], outline=(255, 255, 255, 45), width=2)

    initials = "".join(w[0] for w in title.split()[:2]).upper()
    f = _font(210)
    tb = d.textbbox((0, 0), initials, font=f)
    d.text((cx - (tb[2] - tb[0]) / 2, cy - (tb[3] - tb[1]) / 2 - tb[1]), initials,
           font=f, fill=(255, 255, 255, 235))

    cf = _font(38)
    caption = title.upper()
    cbb = d.textbbox((0, 0), caption, font=cf)
    d.text((cx - (cbb[2] - cbb[0]) / 2, int(size[1] * 0.82)), caption, font=cf, fill=(255, 255, 255, 210))
    d.rectangle([60, 60, size[0] - 60, size[1] - 60], outline=(255, 255, 255, 60), width=2)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=86)
    return buf.getvalue()


class Command(BaseCommand):
    help = "Seed the Acme Store with an image-backed catalogue for /app/."

    def add_arguments(self, parser):
        parser.add_argument("--refresh", action="store_true",
                            help="Re-fetch images even for products that already have them.")
        parser.add_argument("--domain", default="acme.localhost",
                            help="Primary host for the store (e.g. acme.mnxstore.com).")

    @transaction.atomic
    def handle(self, *args, **options):
        self.refresh = options.get("refresh", False)
        host = options["domain"].strip().lower()
        project = Project.objects.filter(name="Acme Store").first()
        if project is None:
            project = Project.objects.create(
                name="Acme Store", currency="INR", country="IN", status="active",
                primary_domain=host,
            )
        elif project.primary_domain != host:
            project.primary_domain = host
            project.save(update_fields=["primary_domain"])
        Domain.objects.filter(project=project).update(is_primary=False)
        Domain.objects.update_or_create(
            project=project, host=host,
            defaults={"is_primary": True, "is_verified": True},
        )

        theme, _ = ThemeSettings.objects.get_or_create(project=project)
        theme.primary_color, theme.secondary_color, theme.accent_color = "#3f5138", "#f6f4ef", "#7b6a4f"
        theme.font_heading, theme.font_body = "Cormorant Garamond", "Inter"
        theme.save()

        warehouse, _ = Warehouse.objects.get_or_create(
            project=project, code="main", defaults={"name": "Central Warehouse", "is_default": True},
        )

        created = updated = images = self._real = self._fallback = 0
        for cat_name, rows in CATALOGUE.items():
            category, _ = Category.objects.get_or_create(project=project, name=cat_name)
            photo_ids = UNSPLASH.get(cat_name, [])
            for idx, (title, price, sale, desc, featured, new) in enumerate(rows):
                slug = slugify(title)
                product, was_created = Product.objects.get_or_create(
                    project=project, slug=slug,
                    defaults={
                        "title": title, "status": "active", "search_indexed": True,
                        "kind": "simple", "price": Decimal(price), "category": category,
                        "sale_price": Decimal(sale) if sale else None,
                        "short_description": desc,
                        "description": f"{desc}\n\nEthically made. Free shipping over ₹999. 30-day returns.",
                        "is_featured": featured, "is_new_arrival": new,
                    },
                )
                if not was_created:
                    product.title, product.price, product.category = title, Decimal(price), category
                    product.sale_price = Decimal(sale) if sale else None
                    product.short_description = desc
                    product.is_featured, product.is_new_arrival = featured, new
                    product.status = "active"
                    product.save()
                    updated += 1
                else:
                    created += 1

                if self.refresh and product.images.exists():
                    for pi in product.images.all():
                        pi.image.delete(save=False)
                    product.images.all().delete()

                if not product.images.exists():
                    n_imgs = 2 if featured else 1
                    for v in range(n_imgs):
                        data = None
                        if photo_ids:
                            pid = photo_ids[(idx + v) % len(photo_ids)]
                            data = _download(_unsplash_url(pid, 1000, 1250))
                        if data:
                            self._real += 1
                        else:
                            data = _make_image(title, slug, v)
                            self._fallback += 1
                        pi = ProductImage(product=product, alt=title, order=v, is_primary=(v == 0))
                        pi.image.save(f"{slug}-{v}.jpg", ContentFile(data), save=True)
                        images += 1

                item = inv.get_or_create_item(warehouse=warehouse, product=product)
                if item.quantity == 0:
                    inv.receive_stock(item=item, quantity=80, note="Acme seed")

        self._hero(project)
        self._shipping(project)
        self._payments(project)
        self._coupons(project)
        self._reviews(project)
        for kind, title, body in PAGES:
            Page.objects.get_or_create(
                project=project, kind=kind,
                defaults={"title": title, "slug": slugify(title), "body": body,
                          "status": PublishStatus.PUBLISHED},
            )

        self.stdout.write(self.style.SUCCESS(
            f"Acme catalogue ready — {created} created, {updated} updated, "
            f"{images} images ({self._real} from Unsplash, {self._fallback} generated fallback)."
        ))
        self.stdout.write(f"Open: http://{host}/app/  (or your mapped port)")

    def _hero(self, project):
        banner, _ = Banner.objects.get_or_create(
            project=project, placement="hero", name="Homepage hero",
            defaults={"is_active": True},
        )
        banner.heading = "Considered essentials, made to last."
        banner.subheading = "New season"
        banner.cta_label = "Shop the collection"
        banner.cta_url = "/app/shop/"
        banner.is_active = True
        if self.refresh or not banner.image:
            data = _download(_unsplash_url(HERO_PHOTO, 1800, 1400))
            if data:
                banner.image.save("acme-hero.jpg", ContentFile(data), save=False)
        banner.save()

    def _shipping(self, project):
        zone, _ = ShippingZone.objects.get_or_create(
            project=project, name="India",
            defaults={"countries": ["IN"], "is_active": True, "priority": 10},
        )
        ShippingMethod.objects.get_or_create(
            project=project, zone=zone, name="Standard",
            defaults={"rate_type": RateType.FLAT, "base_rate": Decimal("99"),
                      "free_over": Decimal("999"), "cod_available": True, "cod_fee": Decimal("0"),
                      "min_days": 3, "max_days": 7},
        )
        ShippingMethod.objects.get_or_create(
            project=project, zone=zone, name="Express",
            defaults={"rate_type": RateType.FLAT, "base_rate": Decimal("249"),
                      "cod_available": False, "min_days": 1, "max_days": 2, "priority": 5},
        )

    def _payments(self, project):
        for provider, priority in ((Provider.COD, 10), (Provider.MANUAL, 20)):
            PaymentProviderConfig.objects.get_or_create(
                project=project, provider=provider,
                defaults={"is_enabled": True, "is_test_mode": True, "priority": priority},
            )

    def _reviews(self, project):
        seeds = [
            ("everyday-oxford-shirt", "Priya S", 5, "Perfect weight", "Softer than I expected and the fit is spot on. Bought a second."),
            ("everyday-oxford-shirt", "Aman K", 4, "Good but runs slim", "Great shirt, size up if you're between sizes."),
            ("merino-crew-sweater", "Rhea M", 5, "Worth every rupee", "No itch at all, holds shape after washing. Lovely colour."),
            ("leather-derby", "Vikram N", 5, "Built to last", "Proper welted construction. Comfortable straight out of the box."),
            ("suede-chukka", "Sana R", 4, "Lovely suede", "Colour is richer in person. Needed a protectant spray."),
            ("structured-leather-tote", "Isha P", 5, "Everyday carry sorted", "Fits a 14\" laptop and then some. Leather smells amazing."),
            ("selvedge-denim", "Kabir T", 5, "Fades beautifully", "Six weeks in and the whiskering is coming through nicely."),
            ("lambswool-scarf", "Meera D", 5, "So soft", "Big enough to actually wrap twice. Gift-worthy."),
            ("weekender-duffel", "Rohan V", 4, "Great for short trips", "Waxed canvas is tough. Wish it had a shoe pocket."),
            ("stoneware-mug-set-of-2", "Anjali B", 5, "Beautiful glaze", "Every mug is slightly different. Feels handmade."),
        ]
        for slug, name, rating, title, body in seeds:
            product = Product.objects.filter(project=project, slug=slug).first()
            if product is None:
                continue
            email = f"{name.split()[0].lower()}@example.com"
            review, created = Review.objects.get_or_create(
                project=project, product=product, author_email=email,
                defaults={"author_name": name, "rating": rating, "title": title,
                          "body": body, "status": ReviewStatus.APPROVED,
                          "is_verified_purchase": True},
            )
            if not created and review.status != ReviewStatus.APPROVED:
                review.status = ReviewStatus.APPROVED
                review.save(update_fields=["status"])
        for product in Product.objects.filter(project=project, reviews__isnull=False).distinct():
            reviews_svc.refresh_product_rating(product)

    def _coupons(self, project):
        for spec in (
            {"code": "WELCOME10", "discount_type": DiscountType.PERCENT, "value": Decimal("10"),
             "max_discount": Decimal("800")},
            {"code": "FLAT300", "discount_type": DiscountType.FIXED, "value": Decimal("300"),
             "min_order_amount": Decimal("2500")},
            {"code": "FREESHIP", "discount_type": DiscountType.FREE_SHIPPING, "value": Decimal("0")},
        ):
            code = spec.pop("code")
            Coupon.objects.get_or_create(project=project, code=code,
                                         defaults={**spec, "is_active": True})
