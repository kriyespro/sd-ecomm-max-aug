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
