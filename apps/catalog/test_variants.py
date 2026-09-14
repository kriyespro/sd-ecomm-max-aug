from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase, override_settings

from apps.catalog.models import Attribute, Product, ProductKind, Variant
from apps.catalog.variants import (
    apply_size_color,
    combo_key,
    matrix_from_post,
    parse_list,
    size_color_of,
    storefront_axes,
)
from apps.projects.models import Domain, Project


class ParseListTests(TestCase):
    def test_trims_dedupes_keeps_order(self):
        self.assertEqual(parse_list(" S , M ,m, L\nXL"), ["S", "M", "L", "XL"])

    def test_blank(self):
        self.assertEqual(parse_list(""), [])
        self.assertEqual(parse_list(None), [])


class ApplySizeColorTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="Threads", status="active")
        self.product = Product.objects.create(
            project=self.project, title="Tee", price=Decimal("999"),
            status="active", kind=ProductKind.SIMPLE,
        )

    def test_builds_full_matrix_with_overrides(self):
        created, updated, gone = apply_size_color(
            self.product, sizes=["S", "M"], colors=["Red", "Blue"],
            matrix={
                combo_key("S", "Red"): {"price": "1099", "stock": "4"},
                combo_key("M", "Blue"): {"sale_price": "899"},
            },
        )
        self.assertEqual((created, updated, gone), (4, 0, 0))
        self.assertEqual(self.product.variants.filter(is_active=True).count(), 4)

        self.product.refresh_from_db()
        self.assertEqual(self.product.kind, ProductKind.VARIABLE)

        for name in ("Size", "Color"):
            attr = Attribute.objects.get(project=self.project, name=name)
            self.assertTrue(attr.is_variant)

        s_red = self.product.variants.get(name="S / Red")
        self.assertEqual(s_red.price, Decimal("1099"))
        self.assertEqual(s_red.stock, 4)
        self.assertEqual(
            sorted(v.value for v in s_red.attribute_values.all()), ["Red", "S"]
        )

    def test_reapply_deactivates_stale_combo_not_delete(self):
        apply_size_color(self.product, sizes=["S", "M"], colors=["Red"])
        self.assertEqual(Variant.objects.filter(product=self.product).count(), 2)

        apply_size_color(self.product, sizes=["S"], colors=["Red"])
        self.assertEqual(Variant.objects.filter(product=self.product).count(), 2)
        self.assertEqual(
            self.product.variants.filter(is_active=True).count(), 1
        )
        self.assertFalse(self.product.variants.get(name="M / Red").is_active)

    def test_clearing_both_lists_retires_all(self):
        apply_size_color(self.product, sizes=["S"], colors=["Red"])
        _, _, gone = apply_size_color(self.product, sizes=[], colors=[])
        self.assertEqual(gone, 1)
        self.assertEqual(self.product.variants.filter(is_active=True).count(), 0)

    def test_single_axis(self):
        apply_size_color(self.product, sizes=["S", "M", "L"], colors=[])
        self.assertEqual(self.product.variants.filter(is_active=True).count(), 3)
        self.assertFalse(Attribute.objects.filter(name="Color", project=self.project).exists())

    def test_size_color_of_round_trip(self):
        apply_size_color(
            self.product, sizes=["S", "M"], colors=["Red"],
            matrix={combo_key("S", "Red"): {"price": "1099", "stock": "4"}},
        )
        sizes, colors, rows = size_color_of(self.product)
        self.assertEqual(sizes, ["S", "M"])
        self.assertEqual(colors, ["Red"])
        self.assertEqual(rows[combo_key("S", "Red")]["price"], "1099.00")
        self.assertEqual(rows[combo_key("S", "Red")]["stock"], "4")


class TrackVariantStockTests(TestCase):
    """Opt-in real stock enforcement for Size/Colour variants
    (Product.track_variant_stock) — off by default, matching the platform's
    existing "no InventoryItem row = unlimited" convention."""

    def setUp(self):
        self.project = Project.objects.create(name="TrackCo", status="active")
        self.product = Product.objects.create(
            project=self.project, title="Hoodie", price=Decimal("1999"), status="active",
        )

    def test_off_by_default_creates_no_inventory_item(self):
        from apps.inventory.models import InventoryItem

        apply_size_color(
            self.product, sizes=["S", "M"], colors=[],
            matrix={combo_key("S", ""): {"stock": "5"}},
        )
        self.assertFalse(InventoryItem.objects.filter(product=self.product).exists())

    def test_enabling_creates_inventory_items_synced_to_variant_stock(self):
        from apps.inventory.models import InventoryItem, Warehouse

        self.product.track_variant_stock = True
        self.product.save(update_fields=["track_variant_stock"])
        apply_size_color(
            self.product, sizes=["S", "M"], colors=[],
            matrix={combo_key("S", ""): {"stock": "5"}, combo_key("M", ""): {"stock": "2"}},
        )
        s = self.product.variants.get(name="S")
        m = self.product.variants.get(name="M")
        item_s = InventoryItem.objects.get(product=self.product, variant=s)
        item_m = InventoryItem.objects.get(product=self.product, variant=m)
        self.assertEqual(item_s.quantity, 5)
        self.assertEqual(item_m.quantity, 2)
        self.assertTrue(Warehouse.objects.filter(project=self.project, is_default=True).exists())

    def test_checkout_actually_blocks_oversell_once_enabled(self):
        from apps.cart.models import Cart, CartItem
        from apps.orders.services import OrderError, place_order

        self.product.track_variant_stock = True
        self.product.save(update_fields=["track_variant_stock"])
        apply_size_color(
            self.product, sizes=["S"], colors=[],
            matrix={combo_key("S", ""): {"stock": "1"}},
        )
        variant = self.product.variants.get(name="S")

        cart = Cart.objects.create(project=self.project, is_active=True)
        CartItem.objects.create(cart=cart, product=self.product, variant=variant,
                                quantity=2, unit_price=self.product.price)
        with self.assertRaises(OrderError):
            place_order(
                project=self.project, cart=cart, email="x@t.test",
                billing_address={}, shipping_address={"name": "X"},
            )

    def test_disabling_removes_inventory_items(self):
        from apps.inventory.models import InventoryItem

        self.product.track_variant_stock = True
        self.product.save(update_fields=["track_variant_stock"])
        apply_size_color(
            self.product, sizes=["S"], colors=[],
            matrix={combo_key("S", ""): {"stock": "5"}},
        )
        self.assertTrue(InventoryItem.objects.filter(product=self.product).exists())

        self.product.track_variant_stock = False
        self.product.save(update_fields=["track_variant_stock"])
        apply_size_color(
            self.product, sizes=["S"], colors=[],
            matrix={combo_key("S", ""): {"stock": "5"}},
        )
        self.assertFalse(InventoryItem.objects.filter(product=self.product).exists())

    def test_storefront_axes_reports_live_availability_when_tracked(self):
        from apps.inventory import services as inv

        self.product.track_variant_stock = True
        self.product.save(update_fields=["track_variant_stock"])
        apply_size_color(
            self.product, sizes=["S"], colors=[],
            matrix={combo_key("S", ""): {"stock": "3"}},
        )
        variant = self.product.variants.get(name="S")
        item = variant.inventory_items.get()
        inv.reserve(item=item, quantity=3)  # sold out

        variants = list(
            self.product.variants.filter(is_active=True)
            .prefetch_related("attribute_values__attribute")
        )
        axes = storefront_axes(variants)
        self.assertEqual(axes["map"][combo_key("S", "")]["stock"], 0)
        self.assertFalse(axes["map"][combo_key("S", "")]["in_stock"])

    def test_storefront_axes_falls_back_to_variant_stock_when_untracked(self):
        apply_size_color(
            self.product, sizes=["S"], colors=[],
            matrix={combo_key("S", ""): {"stock": "7"}},
        )
        variants = list(
            self.product.variants.filter(is_active=True)
            .prefetch_related("attribute_values__attribute")
        )
        axes = storefront_axes(variants)
        self.assertEqual(axes["map"][combo_key("S", "")]["stock"], 7)


class MatrixFromPostTests(TestCase):
    def test_pulls_combo_fields(self):
        post = {
            "combo_price[S|||Red]": "10",
            "combo_stock[S|||Red]": "3",
            "combo_sale[M|||Blue]": "8",
            "title": "ignored",
        }
        out = matrix_from_post(post)
        self.assertEqual(out["S|||Red"], {"price": "10", "stock": "3"})
        self.assertEqual(out["M|||Blue"], {"sale_price": "8"})


class StorefrontAxesTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="Threads", status="active")
        self.product = Product.objects.create(
            project=self.project, title="Tee", price=Decimal("999"), status="active",
        )

    def test_none_without_axis_variants(self):
        Variant.objects.create(product=self.product, name="Plain", is_active=True)
        variants = list(self.product.variants.prefetch_related("attribute_values__attribute"))
        self.assertIsNone(storefront_axes(variants))

    def test_axes_and_map(self):
        apply_size_color(self.product, sizes=["S", "M"], colors=["Red"])
        variants = list(
            self.product.variants.filter(is_active=True)
            .prefetch_related("attribute_values__attribute")
        )
        axes = storefront_axes(variants)
        self.assertEqual([a["name"] for a in axes["axes"]], ["Size", "Color"])
        self.assertEqual(axes["axes"][0]["options"], ["S", "M"])
        self.assertIn(combo_key("S", "Red"), axes["map"])
        self.assertEqual(
            Decimal(axes["map"][combo_key("S", "Red")]["price"]), Decimal("999")
        )
        self.assertIsNone(axes["map"][combo_key("S", "Red")]["sale_price"])

    def test_per_variant_price_override(self):
        """Each size can have its own price — the map must carry each
        variant's own regular/sale price, not the product's shared price."""
        apply_size_color(
            self.product, sizes=["S", "M"], colors=[],
            matrix={
                combo_key("S", ""): {"price": "1200"},
                combo_key("M", ""): {"price": "1500", "sale_price": "1300"},
            },
        )
        variants = list(
            self.product.variants.filter(is_active=True)
            .prefetch_related("attribute_values__attribute")
        )
        axes = storefront_axes(variants)
        s = axes["map"][combo_key("S", "")]
        m = axes["map"][combo_key("M", "")]
        self.assertEqual(Decimal(s["price"]), Decimal("1200"))
        self.assertIsNone(s["sale_price"])
        self.assertEqual(Decimal(m["price"]), Decimal("1500"))
        self.assertEqual(Decimal(m["sale_price"]), Decimal("1300"))

    def test_variant_with_no_price_override_falls_back_to_product_price(self):
        apply_size_color(self.product, sizes=["S"], colors=[])
        variants = list(
            self.product.variants.filter(is_active=True)
            .prefetch_related("attribute_values__attribute")
        )
        axes = storefront_axes(variants)
        self.assertEqual(
            Decimal(axes["map"][combo_key("S", "")]["price"]), self.product.price,
        )


@override_settings(ALLOWED_HOSTS=["*"])
class StorefrontPickerRenderTests(TestCase):
    def setUp(self):
        cache.clear()
        self.project = Project.objects.create(
            name="Rack", status="active", feature_flags={"onboarded": True},
        )
        Domain.objects.create(
            project=self.project, host="shop.rack.test", is_verified=True, is_primary=True
        )
        self.product = Product.objects.create(
            project=self.project, title="Cotton Tee", price=Decimal("799"),
            status="active",
        )
        apply_size_color(self.product, sizes=["S", "M"], colors=["Black", "White"])

    def test_product_page_renders_size_and_colour_buttons(self):
        resp = self.client.get(
            f"/p/{self.product.slug}/", HTTP_HOST="shop.rack.test"
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn(">Size<", body)
        self.assertIn(">Color<", body)
        self.assertIn('sel["Size"] = "S"', body)  # axis button
        self.assertIn(combo_key("M", "White"), body)  # variant map key

    def test_stock_badge_and_add_to_bag_share_the_pickers_selection_state(self):
        """Regression: the top stock badge and the Add-to-bag button used to
        be computed once server-side from an aggregate across every variant,
        completely ignoring which size/colour the shopper picked. They must
        now read the same live `avail`/`cur` the picker itself uses, from one
        shared Alpine scope — not each carry their own disconnected copy."""
        resp = self.client.get(f"/p/{self.product.slug}/", HTTP_HOST="shop.rack.test")
        body = resp.content.decode()
        # One shared scope defines the derived stock state...
        self.assertEqual(body.count("get avail()"), 1)
        self.assertIn("hasAxes: true", body)
        self.assertIn("get cur()", body)
        # ...and the badge / button actually reference it, instead of a
        # server-baked number frozen at page-render time.
        self.assertIn('x-if="avail !== null"', body)
        self.assertIn("avail !== null && avail <= 0", body)
        # The picker no longer declares its own, disconnected copy of this
        # state (that was the bug: two separate `sel`/`map` objects that
        # couldn't see each other).
        self.assertEqual(body.count('sel: {'), 1)

    def test_price_updates_with_the_selected_size_not_frozen_at_render_time(self):
        """Regression: the price shown was always the product's own price,
        server-baked once at render time — picking a size with its own
        (different) price never changed what was on screen. The map must
        carry each size's price, and the display must read it reactively."""
        apply_size_color(
            self.product, sizes=["S", "M"], colors=[],
            matrix={
                combo_key("S", ""): {"price": "699"},
                combo_key("M", ""): {"price": "899", "sale_price": "799"},
            },
        )
        resp = self.client.get(f"/p/{self.product.slug}/", HTTP_HOST="shop.rack.test")
        body = resp.content.decode()
        # Each size's own price is in the shared map the picker also reads
        # (exact price/sale_price values are covered precisely by
        # StorefrontAxesTests — here just confirm both sizes' own combo keys
        # made it into the page's map, each carrying a "price").
        import re

        for key in (combo_key("S", ""), combo_key("M", "")):
            self.assertRegex(body, re.escape(f'"{key}"') + r'\s*:\s*\{[^}]*"price"')
        self.assertIn('"sale_price": "799.00"', body)
        # ...and the price shown on screen is computed reactively, not a
        # single server-rendered current_price frozen at page load.
        self.assertNotIn("{{ product.current_price", body)
        self.assertIn("get regularPrice()", body)
        self.assertIn("get salePrice()", body)
        self.assertIn('x-text="fmt(displayPrice)"', body)


@override_settings(ALLOWED_HOSTS=["*"])
class QuickviewPriceTests(TestCase):
    """The quickview modal (opened from a product card's "Quick view" button)
    has its own price summary above the variant <select> — it used to just
    show the product's own price regardless of which variant was picked."""

    def setUp(self):
        cache.clear()
        self.project = Project.objects.create(
            name="QuickRack", status="active", feature_flags={"onboarded": True},
        )
        Domain.objects.create(
            project=self.project, host="quick.rack.test", is_verified=True, is_primary=True,
        )
        self.product = Product.objects.create(
            project=self.project, title="Lamp", price=Decimal("500"), status="active",
        )
        self.small = Variant.objects.create(
            product=self.product, name="Small", price=Decimal("400"), is_active=True,
        )
        self.large = Variant.objects.create(
            product=self.product, name="Large", price=Decimal("900"),
            sale_price=Decimal("750"), is_active=True,
        )

    def test_price_summary_reads_from_the_same_scope_the_select_updates(self):
        resp = self.client.get(f"/quick/{self.product.slug}/", HTTP_HOST="quick.rack.test")
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        # Each variant's own regular/sale price is available to Alpine...
        self.assertIn(f'"pk": {self.small.pk}, "price": 400.00', body)
        self.assertIn(f'"pk": {self.large.pk}, "price": 900.00, "sale": 750.00', body)
        # ...the <select> drives the same `sel` the price reads...
        self.assertIn('x-model.number="sel"', body)
        # ...and the summary is reactive, not a frozen server-rendered price.
        self.assertIn('x-text="fmt(displayPrice)"', body)
        self.assertNotIn("{{ product.current_price", body)

    def test_defaults_to_the_first_variant_not_the_product_price(self):
        resp = self.client.get(f"/quick/{self.product.slug}/", HTTP_HOST="quick.rack.test")
        body = resp.content.decode()
        self.assertIn(f"sel: {self.small.pk}", body)
