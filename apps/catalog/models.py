"""Catalog: brands, product types, attributes, products, images, variants.

All top-level entities are project-scoped (project.md section 7). Child rows
(images, variants, attribute values) reach the project through their parent.
"""

import re
import secrets
from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.utils.text import slugify

from apps.core.html import sanitize_html
from apps.core.models import SeoFieldsModel, TenantScopedModel, TimeStampedModel

MONEY = dict(max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0"))])


class ProductKind(models.TextChoices):
    SIMPLE = "simple", "Simple"
    VARIABLE = "variable", "Variable"
    DIGITAL = "digital", "Digital"
    SERVICE = "service", "Service"


class ProductStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    ACTIVE = "active", "Active"
    ARCHIVED = "archived", "Archived"


def _unique_slug(model, project, value, *, instance_pk=None, field="slug"):
    base = slugify(value)[:150] or "item"
    slug = base
    i = 2
    qs = model.objects.filter(project=project)
    if instance_pk:
        qs = qs.exclude(pk=instance_pk)
    while qs.filter(**{field: slug}).exists():
        slug = f"{base}-{i}"
        i += 1
    return slug


def _unique_sku(scope_qs, seed, *, instance_pk=None):
    """Auto SKU: initials of the seed text + a short random tail, unique within
    ``scope_qs`` (project- or product-scoped)."""
    words = [w for w in re.split(r"[-\s]+", slugify(seed)) if w]
    prefix = ("".join(w[0] for w in words)[:5] or "SKU").upper()
    qs = scope_qs.exclude(pk=instance_pk) if instance_pk else scope_qs
    for _ in range(25):
        candidate = f"{prefix}-{secrets.token_hex(3).upper()}"
        if not qs.filter(sku=candidate).exists():
            return candidate
    return f"{prefix}-{secrets.token_hex(6).upper()}"


class Brand(TenantScopedModel):
    name = models.CharField(max_length=140)
    slug = models.SlugField(max_length=160, blank=True)
    logo = models.ImageField(upload_to="brands/", blank=True, null=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["project", "slug"], name="uniq_brand_project_slug")
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = _unique_slug(Brand, self.project, self.name, instance_pk=self.pk)
        super().save(*args, **kwargs)


class ProductType(TenantScopedModel):
    """Groups the attribute set a product exposes."""

    name = models.CharField(max_length=140)
    slug = models.SlugField(max_length=160, blank=True)
    kind = models.CharField(max_length=20, choices=ProductKind.choices, default=ProductKind.SIMPLE)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["project", "slug"], name="uniq_producttype_project_slug")
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = _unique_slug(ProductType, self.project, self.name, instance_pk=self.pk)
        super().save(*args, **kwargs)


class Attribute(TenantScopedModel):
    name = models.CharField(max_length=140)
    slug = models.SlugField(max_length=160, blank=True)
    is_variant = models.BooleanField(
        default=False, help_text="Used to build product variants (e.g. Size, Color)."
    )

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["project", "slug"], name="uniq_attribute_project_slug")
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = _unique_slug(Attribute, self.project, self.name, instance_pk=self.pk)
        super().save(*args, **kwargs)


class AttributeValue(TimeStampedModel):
    attribute = models.ForeignKey(Attribute, on_delete=models.CASCADE, related_name="values")
    value = models.CharField(max_length=140)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["attribute__name", "order", "value"]
        constraints = [
            models.UniqueConstraint(fields=["attribute", "value"], name="uniq_attributevalue")
        ]

    def __str__(self):
        return f"{self.attribute.name}: {self.value}"


class Tag(TenantScopedModel):
    name = models.CharField(max_length=80)
    slug = models.SlugField(max_length=90, blank=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["project", "slug"], name="uniq_tag_project_slug")
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = _unique_slug(Tag, self.project, self.name, instance_pk=self.pk)
        super().save(*args, **kwargs)


class Product(TenantScopedModel, SeoFieldsModel):
    kind = models.CharField(max_length=20, choices=ProductKind.choices, default=ProductKind.SIMPLE)
    type = models.ForeignKey(
        ProductType, on_delete=models.SET_NULL, null=True, blank=True, related_name="products"
    )
    brand = models.ForeignKey(
        Brand, on_delete=models.SET_NULL, null=True, blank=True, related_name="products"
    )
    category = models.ForeignKey(
        "categories.Category",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="products",
    )

    title = models.CharField(max_length=220)
    slug = models.SlugField(max_length=240, blank=True)
    sku = models.CharField(max_length=64, blank=True)
    short_description = models.CharField(max_length=320, blank=True)
    description = models.TextField(blank=True)

    price = models.DecimalField(default=Decimal("0"), **MONEY)
    sale_price = models.DecimalField(null=True, blank=True, **MONEY)
    cost_price = models.DecimalField(null=True, blank=True, **MONEY)

    tax_class = models.CharField(max_length=60, blank=True)
    barcode = models.CharField(max_length=64, blank=True)
    hsn_sac = models.CharField(max_length=20, blank=True)

    weight = models.DecimalField(max_digits=8, decimal_places=3, null=True, blank=True)
    length = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    width = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    height = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)

    status = models.CharField(
        max_length=20, choices=ProductStatus.choices, default=ProductStatus.DRAFT, db_index=True
    )
    is_featured = models.BooleanField(default=False)
    is_new_arrival = models.BooleanField(default=False)
    is_bestseller = models.BooleanField(default=False)
    search_indexed = models.BooleanField(default=True)

    # Cached review aggregate, refreshed by apps.reviews.services.
    rating_avg = models.DecimalField(max_digits=3, decimal_places=2, default=Decimal("0"))
    rating_count = models.PositiveIntegerField(default=0)

    tags = models.ManyToManyField(Tag, blank=True, related_name="products")
    attribute_values = models.ManyToManyField(AttributeValue, blank=True, related_name="products")
    related_products = models.ManyToManyField("self", symmetrical=False, blank=True, related_name="related_to")
    cross_sell = models.ManyToManyField("self", symmetrical=False, blank=True, related_name="cross_sold_by")
    upsell = models.ManyToManyField("self", symmetrical=False, blank=True, related_name="upsold_by")

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["project", "slug"], name="uniq_product_project_slug")
        ]
        indexes = [
            models.Index(fields=["project", "status"]),
            models.Index(fields=["project", "sku"]),
        ]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = _unique_slug(Product, self.project, self.title, instance_pk=self.pk)
        if not self.sku:
            self.sku = _unique_sku(
                Product.objects.filter(project=self.project), self.title, instance_pk=self.pk
            )
        self.description = sanitize_html(self.description)
        super().save(*args, **kwargs)

    @property
    def current_price(self):
        if self.sale_price is not None and self.sale_price < self.price:
            return self.sale_price
        return self.price

    @property
    def on_sale(self):
        return self.sale_price is not None and self.sale_price < self.price


class ProductImage(TimeStampedModel):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="images")
    # ``image`` is the served file. On upload it holds the raw original; a
    # background task (apps.catalog.tasks) rewrites it to an optimised WebP,
    # keeps the untouched upload in ``original`` and fills the fields below.
    image = models.ImageField(upload_to="products/")
    original = models.ImageField(upload_to="products/originals/", blank=True)
    alt = models.CharField(max_length=200, blank=True)
    order = models.PositiveIntegerField(default=0)
    is_primary = models.BooleanField(default=False)

    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    bytes = models.PositiveIntegerField(default=0)
    # {"512": "/media/products/r/foo_512.webp", "1024": ...} — responsive set.
    renditions = models.JSONField(default=dict, blank=True)
    optimized_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-is_primary", "order", "id"]

    def __str__(self):
        return f"Image<{self.product_id}>"

    @property
    def is_optimized(self):
        return self.optimized_at is not None

    @property
    def image_is_webp(self):
        return bool(self.image) and self.image.name.lower().endswith(".webp")

    @property
    def kept_original(self):
        """Optimised pass ran but left the file alone — it was already small."""
        return self.is_optimized and not self.image_is_webp

    @property
    def srcset(self):
        """``srcset`` attribute value built from ``renditions`` plus the full
        image. Empty string when nothing has been generated yet."""
        if not self.renditions or not self.image:
            return ""
        parts = [
            f"{url} {w}w"
            for w, url in sorted(self.renditions.items(), key=lambda kv: int(kv[0]))
        ]
        if self.width:
            parts.append(f"{self.image.url} {self.width}w")
        return ", ".join(parts)


class Variant(TimeStampedModel):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="variants")
    name = models.CharField(max_length=200, blank=True)
    sku = models.CharField(max_length=64, blank=True)
    price = models.DecimalField(null=True, blank=True, **MONEY)
    sale_price = models.DecimalField(null=True, blank=True, **MONEY)
    cost_price = models.DecimalField(null=True, blank=True, **MONEY)
    # Placeholder stock. Real inventory (reserved/available/movements) is the
    # inventory app in Phase 4, which will supersede this field.
    stock = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    attribute_values = models.ManyToManyField(AttributeValue, blank=True, related_name="variants")

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return self.name or f"Variant<{self.product_id}>"

    def save(self, *args, **kwargs):
        if not self.sku and self.product_id:
            parent = self.product.sku or _unique_sku(
                Product.objects.filter(project=self.product.project), self.product.title
            )
            n = (
                Variant.objects.filter(product_id=self.product_id)
                .exclude(pk=self.pk)
                .count()
                + 1
            )
            self.sku = f"{parent}-{n}"
        super().save(*args, **kwargs)

    @property
    def effective_price(self):
        base = self.price if self.price is not None else self.product.price
        if self.sale_price is not None and self.sale_price < base:
            return self.sale_price
        return base
