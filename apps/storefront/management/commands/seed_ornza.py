"""Seed a Project whose catalog matches the ORNZA static skin (html-ornza/).

Sources:
- products-data.js       -> the 36 PDP / category products (slug = ORNZA key)
- *.html .prod-add-cart  -> homepage "featured" products (slug = slugify(name))
- hampers.html           -> gift hamper products

Creates the ORNZA store + domain + catalog + inventory + shipping + COD payments
+ CMS pages + theme. Idempotent.
"""

import re
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils.text import slugify

from apps.catalog.models import Product
from apps.categories.models import Category
from apps.cms.models import Page, PublishStatus, ThemeSettings
from apps.inventory import services as inv
from apps.inventory.models import Warehouse
from apps.coupons.models import Coupon, DiscountType
from apps.payments.models import PaymentProviderConfig, Provider
from apps.projects.models import Domain, Project
from apps.shipping.models import RateType, ShippingMethod, ShippingZone

ENTRY_RE = re.compile(
    r'"(?P<id>[a-z0-9-]+)":\s*\{\s*'
    r'title:\s*"(?P<title>[^"]*)",\s*'
    r'desc:\s*"(?P<desc>[^"]*)",\s*'
    r'material:\s*"(?P<material>[^"]*)",\s*'
    r'stones:\s*"(?P<stones>[^"]*)",\s*'
    r'image:\s*"(?P<image>[^"]*)"',
    re.S,
)
PRICE_RE = re.compile(r'"(?P<id>[a-z0-9-]+)":\s*"₹(?P<price>[\d,]+)"')
FEATURED_RE = re.compile(
    r'class="prod-add-cart"[^>]*data-name="([^"]+)"[^>]*data-price="₹([\d,]+)"[^>]*data-img="([^"]+)"'
)
HAMPER_NAME_RE = re.compile(r'hamper-card-name">([^<]+)</div>')
HAMPER_PRICE_RE = re.compile(r'hamper-price"[^>]*>(?:Starting\s*)?₹([\d,]+)')

PREFIX_CATEGORY = {
    "ring": "Rings", "diamond": "Diamonds", "necklace": "Necklaces",
    "bangle": "Bangles", "earring": "Earrings", "bracelet": "Bracelets",
}
IMG_CATEGORY = {
    "earrings": "Earrings", "necklaces": "Necklaces", "rings": "Rings",
    "bracelets": "Bracelets", "bangles": "Bangles", "diamonds": "Diamonds",
}
PAGES = [
    ("about", "About ORNZA", "<p>ORNZA — India's premium artificial jewellery brand, crafted in Surat.</p>"),
    ("contact", "Contact", "<p>Reach us at hello@ornza.example · Surat, Gujarat.</p>"),
    ("privacy", "Privacy Policy", "<p>We respect your privacy. Demo content.</p>"),
    ("terms", "Terms of Service", "<p>Standard terms apply. Demo content.</p>"),
    ("shipping_policy", "Shipping Info", "<p>Free shipping on orders above ₹999. COD available across India.</p>"),
    ("return_policy", "Return Policy", "<p>7-day easy returns on unworn items.</p>"),
]


def _money(text):
    return Decimal((text or "0").replace(",", "") or "0")


class Command(BaseCommand):
    help = "Seed the ORNZA store to back the /shop/ static skin."

    @transaction.atomic
    def handle(self, *args, **options):
        root = Path(settings.BASE_DIR) / "html-ornza"
        data_js = root / "products-data.js"
        if not data_js.exists():
            self.stderr.write("html-ornza/products-data.js not found.")
            return

        project = Project.objects.filter(name="ORNZA").first()
        if project is None:
            project = Project.objects.create(
                name="ORNZA", currency="INR", country="IN", status="active",
                primary_domain="ornza.localhost",
            )
        Domain.objects.get_or_create(
            project=project, host="ornza.localhost",
            defaults={"is_primary": True, "is_verified": True},
        )

        theme, _ = ThemeSettings.objects.get_or_create(project=project)
        theme.primary_color, theme.secondary_color, theme.accent_color = "#C9A55A", "#1A1507", "#A8833A"
        theme.font_heading, theme.font_body = "Playfair Display", "Poppins"
        theme.save()

        self._categories = {}
        warehouse, _ = Warehouse.objects.get_or_create(
            project=project, code="main", defaults={"name": "Surat Warehouse", "is_default": True},
        )
        self.project, self.warehouse = project, warehouse
        self.created = self.updated = 0

        # 1. products-data.js
        text = data_js.read_text(encoding="utf-8-sig")
        prices = {m["id"]: m["price"] for m in PRICE_RE.finditer(text)}
        for m in ENTRY_RE.finditer(text):
            prefix = m["id"].split("-")[0]
            self._upsert(
                slug=m["id"], title=m["title"], price=_money(prices.get(m["id"])),
                category=self._category(PREFIX_CATEGORY.get(prefix, "Jewellery")),
                short=m["desc"][:300],
                description=f'{m["desc"]}\n\nMaterial: {m["material"]}\nStones: {m["stones"]}',
                featured=prefix in {"ring", "necklace"},
            )

        # 2. homepage .prod-add-cart featured products
        seen = set()
        for html in root.glob("*.html"):
            body = html.read_text(encoding="utf-8-sig")
            for name, price, img in FEATURED_RE.findall(body):
                if name in seen:
                    continue
                seen.add(name)
                key = img.rsplit("/", 1)[-1].split(".")[0]
                self._upsert(
                    slug=slugify(name), title=name, price=_money(price),
                    category=self._category(IMG_CATEGORY.get(key, "Jewellery")),
                    short="", description="", featured=True,
                )

        # 3. hampers.html
        hampers = root / "hampers.html"
        if hampers.exists():
            body = hampers.read_text(encoding="utf-8-sig")
            names = HAMPER_NAME_RE.findall(body)
            prices = HAMPER_PRICE_RE.findall(body)
            cat = self._category("Hampers")
            for name, price in zip(names, prices):
                self._upsert(slug=slugify(name), title=name, price=_money(price),
                             category=cat, short="Curated gift hamper.", description="",
                             featured=False)

        self._shipping()
        self._payments()
        self._coupons()
        for kind, title, body in PAGES:
            Page.objects.get_or_create(
                project=project, kind=kind,
                defaults={"title": title, "slug": slugify(title), "body": body,
                          "status": PublishStatus.PUBLISHED},
            )

        self.stdout.write(self.style.SUCCESS(
            f"ORNZA store ready — project #{project.pk}, host ornza.localhost, "
            f"{self.created} products created, {self.updated} updated."
        ))
        self.stdout.write("Add to /etc/hosts:  127.0.0.1  ornza.localhost")
        self.stdout.write("Then open:          http://ornza.localhost:8000/shop/")

    # --- helpers ---

    def _category(self, name):
        if name not in self._categories:
            self._categories[name] = Category.objects.get_or_create(project=self.project, name=name)[0]
        return self._categories[name]

    def _upsert(self, *, slug, title, price, category, short, description, featured):
        product, was_created = Product.objects.get_or_create(
            project=self.project, slug=slug,
            defaults={
                "title": title, "status": "active", "search_indexed": True, "kind": "simple",
                "price": price, "category": category, "short_description": short,
                "description": description, "is_featured": featured,
            },
        )
        if not was_created:
            product.title, product.price, product.category = title, price, category
            product.status = "active"
            product.save()
            self.updated += 1
        else:
            self.created += 1
        item = inv.get_or_create_item(warehouse=self.warehouse, product=product)
        if item.quantity == 0:
            inv.receive_stock(item=item, quantity=100, note="ORNZA seed")

    def _shipping(self):
        zone, _ = ShippingZone.objects.get_or_create(
            project=self.project, name="India",
            defaults={"countries": ["IN"], "is_active": True, "priority": 10},
        )
        ShippingMethod.objects.get_or_create(
            project=self.project, zone=zone, name="Standard",
            defaults={"rate_type": RateType.FLAT, "base_rate": Decimal("79"),
                      "free_over": Decimal("999"), "cod_available": True, "cod_fee": Decimal("0"),
                      "min_days": 3, "max_days": 7},
        )
        ShippingMethod.objects.get_or_create(
            project=self.project, zone=zone, name="Express",
            defaults={"rate_type": RateType.FLAT, "base_rate": Decimal("199"),
                      "cod_available": False, "min_days": 1, "max_days": 2, "priority": 5},
        )

    def _payments(self):
        for provider, priority in ((Provider.COD, 10), (Provider.MANUAL, 20)):
            PaymentProviderConfig.objects.get_or_create(
                project=self.project, provider=provider,
                defaults={"is_enabled": True, "is_test_mode": True, "priority": priority},
            )

    def _coupons(self):
        specs = [
            {"code": "WELCOME10", "discount_type": DiscountType.PERCENT, "value": Decimal("10"),
             "max_discount": Decimal("500"), "min_order_amount": Decimal("0")},
            {"code": "FLAT200", "discount_type": DiscountType.FIXED, "value": Decimal("200"),
             "min_order_amount": Decimal("1500")},
            {"code": "FREESHIP", "discount_type": DiscountType.FREE_SHIPPING, "value": Decimal("0")},
        ]
        for spec in specs:
            code = spec.pop("code")
            Coupon.objects.get_or_create(
                project=self.project, code=code, defaults={**spec, "is_active": True},
            )
