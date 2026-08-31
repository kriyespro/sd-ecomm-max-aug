"""Seed a full role/store playground for manual testing.

    python manage.py seed_playground

Creates one login-ready account per role (all with the same simple password),
plus 3 demo stores with small, image-backed catalogues. Product images are
pulled once from Unsplash (falls back to a generated tile offline), same as
``seed_acme``. Idempotent — re-running resets the stores and re-sets passwords.

Log in at /accounts/login/ with the **email as the username** and the password
printed at the end.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils.text import slugify

from apps.accounts.models import Membership, PlatformRole, Profile, StoreRole
from apps.billing.models import BillingPeriod, Plan
from apps.categories.models import Category
from apps.catalog.models import Product, ProductImage
from apps.control import store_services
from apps.inventory import services as inv
from apps.inventory.models import Warehouse
from apps.payments.models import PaymentProviderConfig, Provider
from apps.projects.models import Domain, Project
from apps.shipping.models import RateType, ShippingMethod, ShippingZone
from apps.shopfront.management.commands.seed_acme import (
    UNSPLASH,
    _download,
    _make_image,
    _unsplash_url,
)

User = get_user_model()

PASSWORD = "durga123@@@"

# role key -> (email, display name, platform_role, is_staff)
ROLE_USERS = [
    ("superadmin",   "superadmin@sd.test",    "Sam Admin",     PlatformRole.NONE,    True),   # + is_superuser
    ("platformowner", "platformowner@sd.test", "Olivia Platt",  PlatformRole.OWNER,   True),
    ("dgc",          "dgc@sd.test",           "Dev Grewal",     PlatformRole.MANAGER, True),
    ("dgc2",         "dgc2@sd.test",          "Gita Rao",       PlatformRole.MANAGER, True),
    ("storeowner",   "storeowner@sd.test",    "Owen Shah",      PlatformRole.NONE,    True),
    ("storemanager", "storemanager@sd.test",  "Mira Khan",      PlatformRole.NONE,    True),
    ("storestaff",   "storestaff@sd.test",    "Sina Patel",     PlatformRole.NONE,    True),
    ("customer",     "customer@sd.test",      "Cara Nair",      PlatformRole.NONE,    False),
]

# store -> catalogue. photo pools borrowed from seed_acme's UNSPLASH.
STORES = [
    dict(
        name="Aster Home & Living", label="aster", plan="basic",
        period=BillingPeriod.MONTHLY, dgc="dgc",
        accent="#7b6a4f",
        categories={
            "Home": [
                ("Stoneware Dinner Set", 4990, 3990, "12-piece reactive-glaze stoneware.", True, True),
                ("Linen Waffle Throw", 3490, None, "Stonewashed linen-cotton, 130x170cm.", True, False),
                ("Cedar & Amber Candle", 1690, None, "Coconut-soy wax, 55hr burn.", False, True),
                ("Lambswool Blanket", 5990, 4490, "Mill-woven, 150x200cm.", False, False),
            ],
            "Accessories": [
                ("Woven Seagrass Basket", 1990, None, "Handwoven storage, leather handles.", False, True),
                ("Brass Table Clock", 2790, None, "Solid brass, silent sweep.", True, False),
            ],
        },
    ),
    dict(
        name="Kindred Apparel", label="kindred", plan="growth",
        period=BillingPeriod.YEARLY, dgc="dgc",
        accent="#3f5138",
        categories={
            "Apparel": [
                ("Everyday Oxford Shirt", 2490, None, "Washed cotton oxford, relaxed collar.", True, True),
                ("Merino Crew Sweater", 4990, 3990, "Fine-gauge extra-fine merino.", True, False),
                ("Heavyweight Tee 3-Pack", 1990, 1590, "230gsm supima cotton.", False, True),
                ("Selvedge Denim", 5490, None, "14oz Japanese selvedge, straight leg.", True, False),
            ],
            "Footwear": [
                ("Canvas Low-Top", 2990, None, "Organic cotton canvas, vulcanised sole.", False, True),
                ("Suede Chukka", 6490, 4990, "Water-repellent suede, crepe sole.", True, False),
            ],
        },
    ),
    dict(
        name="Northbound Bags", label="northbound", plan="pro",
        period=BillingPeriod.MONTHLY, dgc="dgc2",
        accent="#2f3a44",
        categories={
            "Bags": [
                ("Weekender Duffel", 8990, None, "Waxed canvas and leather, 40L.", True, True),
                ("Structured Leather Tote", 9990, 7990, "Full-grain leather, laptop sleeve.", True, False),
                ("Roll-Top Backpack", 5490, None, "Ripstop nylon, 20L, weatherproof.", False, True),
                ("Travel Dopp Kit", 1990, None, "Wipe-clean lining, hanging hook.", False, False),
            ],
            "Accessories": [
                ("Leather Card Holder", 1490, None, "Vegetable-tanned leather, three slots.", False, True),
                ("Woven Stretch Belt", 1290, 990, "Elastic weave, matte buckle.", False, False),
            ],
        },
    ),
]

# my category name -> seed_acme UNSPLASH pool key
_POOL = {"Home": "Home", "Accessories": "Accessories", "Apparel": "Apparel",
         "Footwear": "Footwear", "Bags": "Bags"}


def _user(email, name, *, platform_role=PlatformRole.NONE, is_staff=True, is_superuser=False):
    first, _, last = (name or "").partition(" ")
    user, _ = User.objects.get_or_create(username=email[:150], defaults={"email": email})
    user.email = email
    user.first_name, user.last_name = first, last
    user.is_active = True
    user.is_staff = is_staff or is_superuser
    user.is_superuser = is_superuser
    user.set_password(PASSWORD)
    user.save()
    profile, _ = Profile.objects.get_or_create(user=user)
    if profile.platform_role != platform_role:
        profile.platform_role = platform_role
        profile.save(update_fields=["platform_role", "updated_at"])
    return user


class Command(BaseCommand):
    help = "Seed one account per role + 3 image-backed demo stores."

    def add_arguments(self, parser):
        parser.add_argument("--domain-suffix", default="localhost",
                            help="Host suffix for the stores (e.g. mnxstore.com). Default: localhost.")
        parser.add_argument("--refresh", action="store_true",
                            help="Re-fetch product images even if they already exist.")

    @transaction.atomic
    def handle(self, *args, **opts):
        self.refresh = opts["refresh"]
        suffix = opts["domain_suffix"].strip().lstrip(".").lower()

        users = {}
        for key, email, name, prole, staff in ROLE_USERS:
            users[key] = _user(email, name, platform_role=prole, is_staff=staff,
                               is_superuser=(key == "superadmin"))

        actor = users["superadmin"]
        real = fallback = 0

        for spec in STORES:
            plan = Plan.objects.get(code=spec["plan"])
            Project.objects.filter(name=spec["name"]).delete()  # reset
            host = f"{spec['label']}.{suffix}"

            project, _owner, _ = store_services.create_store(
                name=spec["name"], primary_domain=host,
                owner_email=users["storeowner"].email, owner_name=users["storeowner"].get_full_name(),
                plan=plan, period=spec["period"], manager=users[spec["dgc"]], actor=actor,
            )
            Domain.objects.update_or_create(
                project=project, host=host,
                defaults={"is_primary": True, "is_verified": True},
            )

            # store team
            for ukey, role in (("storemanager", StoreRole.MANAGER),
                               ("storestaff", StoreRole.STAFF),
                               ("customer", StoreRole.CUSTOMER)):
                Membership.objects.update_or_create(
                    user=users[ukey], project=project,
                    defaults={"role": role, "is_active": True},
                )

            warehouse, _ = Warehouse.objects.get_or_create(
                project=project, code="main",
                defaults={"name": "Central Warehouse", "is_default": True},
            )

            for cat_name, rows in spec["categories"].items():
                category, _ = Category.objects.get_or_create(project=project, name=cat_name)
                photo_ids = UNSPLASH.get(_POOL.get(cat_name, ""), [])
                for idx, (title, price, sale, desc, featured, new) in enumerate(rows):
                    slug = slugify(title)
                    product, was_created = Product.objects.get_or_create(
                        project=project, slug=slug,
                        defaults={
                            "title": title, "status": "active", "search_indexed": True,
                            "kind": "simple", "price": Decimal(price), "category": category,
                            "sale_price": Decimal(sale) if sale else None,
                            "short_description": desc,
                            "description": f"{desc}\n\nDemo product. Free shipping over Rs 999. 30-day returns.",
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

                    if self.refresh and product.images.exists():
                        for pi in product.images.all():
                            pi.image.delete(save=False)
                        product.images.all().delete()

                    if not product.images.exists():
                        for v in range(2 if featured else 1):
                            data = None
                            if photo_ids:
                                pid = photo_ids[(idx + v) % len(photo_ids)]
                                data = _download(_unsplash_url(pid, 1000, 1250))
                            if data:
                                real += 1
                            else:
                                data = _make_image(title, slug, v)
                                fallback += 1
                            pi = ProductImage(product=product, alt=title, order=v, is_primary=(v == 0))
                            pi.image.save(f"{slug}-{v}.jpg", ContentFile(data), save=True)

                    item = inv.get_or_create_item(warehouse=warehouse, product=product)
                    if item.quantity == 0:
                        inv.receive_stock(item=item, quantity=80, note="playground seed")

            self._shipping(project)
            PaymentProviderConfig.objects.get_or_create(
                project=project, provider=Provider.COD,
                defaults={"is_enabled": True, "is_test_mode": True, "priority": 10},
            )
            self.stdout.write(self.style.SUCCESS(
                f"  {spec['name']:22} {plan.name}/{spec['period']:7} DGC={users[spec['dgc']].email}  ({host})"
            ))

        self._report(users, suffix, real, fallback)

    def _shipping(self, project):
        zone, _ = ShippingZone.objects.get_or_create(
            project=project, name="India",
            defaults={"countries": ["IN"], "is_active": True, "priority": 10},
        )
        ShippingMethod.objects.get_or_create(
            project=project, zone=zone, name="Standard",
            defaults={"rate_type": RateType.FLAT, "base_rate": Decimal("99"),
                      "free_over": Decimal("999"), "cod_available": True,
                      "min_days": 3, "max_days": 7},
        )

    def _report(self, users, suffix, real, fallback):
        rows = [
            ("Super Admin",     users["superadmin"].email),
            ("Platform Owner",  users["platformowner"].email),
            ("DGC (1)",         users["dgc"].email),
            ("DGC (2)",         users["dgc2"].email),
            ("Store Owner",     users["storeowner"].email),
            ("Store Manager",   users["storemanager"].email),
            ("Store Staff",     users["storestaff"].email),
            ("Customer",        users["customer"].email),
        ]
        w1 = max(len(r[0]) for r in rows)
        w2 = max(len(r[1]) for r in rows)
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING(
            "Log in at /accounts/login/  —  username = email,  password below"
        ))
        self.stdout.write(f"  {'ROLE':<{w1}}  {'EMAIL':<{w2}}  PASSWORD")
        self.stdout.write(f"  {'-'*w1}  {'-'*w2}  {'-'*len(PASSWORD)}")
        for role, email in rows:
            self.stdout.write(f"  {role:<{w1}}  {email:<{w2}}  {PASSWORD}")
        self.stdout.write("")
        self.stdout.write(f"  Images: {real} from Unsplash, {fallback} generated fallback.")
        self.stdout.write(f"  Storefronts: http://<label>.{suffix}/app/  (aster / kindred / northbound)")
