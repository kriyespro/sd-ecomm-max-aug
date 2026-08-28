from django.contrib import admin

from .models import (
    Attribute,
    AttributeValue,
    Brand,
    Product,
    ProductImage,
    ProductType,
    Tag,
    Variant,
)


class AttributeValueInline(admin.TabularInline):
    model = AttributeValue
    extra = 1


class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 1


class VariantInline(admin.TabularInline):
    model = Variant
    extra = 0
    filter_horizontal = ("attribute_values",)


@admin.register(Brand)
class BrandAdmin(admin.ModelAdmin):
    list_display = ("name", "project", "is_active")
    list_filter = ("project", "is_active")
    search_fields = ("name", "slug")


@admin.register(ProductType)
class ProductTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "project", "kind")
    list_filter = ("project", "kind")
    search_fields = ("name", "slug")


@admin.register(Attribute)
class AttributeAdmin(admin.ModelAdmin):
    list_display = ("name", "project", "is_variant")
    list_filter = ("project", "is_variant")
    search_fields = ("name", "slug")
    inlines = [AttributeValueInline]


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ("name", "project")
    list_filter = ("project",)
    search_fields = ("name", "slug")


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("title", "project", "kind", "status", "price", "sale_price", "is_featured")
    list_filter = ("project", "status", "kind", "is_featured", "is_bestseller")
    search_fields = ("title", "slug", "sku", "barcode")
    autocomplete_fields = ("brand", "category", "type")
    filter_horizontal = (
        "tags",
        "attribute_values",
        "related_products",
        "cross_sell",
        "upsell",
    )
    inlines = [ProductImageInline, VariantInline]


@admin.register(Variant)
class VariantAdmin(admin.ModelAdmin):
    list_display = ("__str__", "product", "sku", "price", "stock", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "sku", "product__title")
