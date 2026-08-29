"""API v1 serializers. Read serializers are lean storefront-facing shapes;
write serializers validate input only — business logic stays in app services.
"""

from django.contrib.auth import get_user_model, password_validation
from rest_framework import serializers

from apps.cart.models import Cart, CartItem
from apps.catalog.models import Brand, Product, ProductImage, Variant
from apps.categories.models import Category
from apps.cms.models import FAQ, Page
from apps.orders.models import Order, OrderItem
from apps.reviews.models import Review

User = get_user_model()


# --- catalog ---------------------------------------------------

class BrandSerializer(serializers.ModelSerializer):
    class Meta:
        model = Brand
        fields = ["id", "name", "slug", "description"]


class CategorySerializer(serializers.ModelSerializer):
    parent = serializers.SlugRelatedField(slug_field="slug", read_only=True)

    class Meta:
        model = Category
        fields = ["id", "name", "slug", "parent", "description", "is_featured", "order"]


class ProductImageSerializer(serializers.ModelSerializer):
    image = serializers.ImageField(use_url=True)
    srcset = serializers.CharField(read_only=True)

    class Meta:
        model = ProductImage
        fields = [
            "image", "alt", "is_primary", "order",
            "width", "height", "srcset", "renditions",
        ]


class VariantSerializer(serializers.ModelSerializer):
    price = serializers.DecimalField(source="effective_price", max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = Variant
        fields = ["id", "name", "sku", "price", "is_active"]


class ProductListSerializer(serializers.ModelSerializer):
    brand = serializers.SlugRelatedField(slug_field="slug", read_only=True)
    category = serializers.SlugRelatedField(slug_field="slug", read_only=True)
    price = serializers.DecimalField(source="current_price", max_digits=12, decimal_places=2, read_only=True)
    list_price = serializers.DecimalField(source="price", max_digits=12, decimal_places=2, read_only=True)
    on_sale = serializers.BooleanField(read_only=True)
    primary_image = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            "id", "title", "slug", "sku", "kind", "brand", "category",
            "price", "list_price", "on_sale", "rating_avg", "rating_count",
            "is_featured", "is_new_arrival", "is_bestseller", "primary_image",
        ]

    def get_primary_image(self, obj):
        img = next((i for i in obj.images.all() if i.is_primary), None) or (
            obj.images.all()[0] if obj.images.all() else None
        )
        if img and img.image:
            return self.context["request"].build_absolute_uri(img.image.url)
        return None


class ProductDetailSerializer(ProductListSerializer):
    images = ProductImageSerializer(many=True, read_only=True)
    variants = serializers.SerializerMethodField()

    class Meta(ProductListSerializer.Meta):
        fields = ProductListSerializer.Meta.fields + [
            "short_description", "description", "barcode", "hsn_sac",
            "weight", "length", "width", "height",
            "seo_title", "seo_description", "images", "variants",
        ]

    def get_variants(self, obj):
        return VariantSerializer(obj.variants.filter(is_active=True), many=True).data


# --- cms ------------------------------------------------------

class PageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Page
        fields = ["kind", "title", "slug", "excerpt", "body", "blocks", "template_key", "published_at"]


class FAQSerializer(serializers.ModelSerializer):
    class Meta:
        model = FAQ
        fields = ["group", "question", "answer", "order"]


# --- cart ---------------------------------------------------

class CartItemSerializer(serializers.ModelSerializer):
    product_slug = serializers.SlugRelatedField(source="product", slug_field="slug", read_only=True)
    product_title = serializers.CharField(source="product.title", read_only=True)
    variant_name = serializers.CharField(source="variant.name", read_only=True, default="")
    line_total = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = CartItem
        fields = ["id", "product_slug", "product_title", "variant", "variant_name",
                  "quantity", "unit_price", "line_total"]


class CartSerializer(serializers.ModelSerializer):
    items = CartItemSerializer(many=True, read_only=True)
    subtotal = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    item_count = serializers.IntegerField(read_only=True)
    currency = serializers.CharField(source="project.currency", read_only=True)

    class Meta:
        model = Cart
        fields = ["id", "currency", "items", "subtotal", "item_count"]


class AddToCartSerializer(serializers.Serializer):
    product = serializers.SlugField()
    variant = serializers.IntegerField(required=False, allow_null=True)
    quantity = serializers.IntegerField(min_value=1, default=1)


class UpdateCartItemSerializer(serializers.Serializer):
    quantity = serializers.IntegerField(min_value=0)


# --- orders / checkout -------------------------------------

class OrderItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderItem
        fields = ["product_title", "variant_name", "sku", "unit_price", "quantity",
                  "line_total", "fulfilled_quantity"]


class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)

    class Meta:
        model = Order
        fields = [
            "number", "status", "payment_status", "fulfillment_status", "shipping_status",
            "currency", "subtotal", "discount_total", "tax_total", "shipping_total", "grand_total",
            "coupon_code", "tracking_number", "courier",
            "billing_address", "shipping_address", "items",
            "customer_note", "placed_at", "created_at",
        ]


class AddressSerializer(serializers.Serializer):
    name = serializers.CharField()
    line1 = serializers.CharField()
    line2 = serializers.CharField(required=False, allow_blank=True)
    city = serializers.CharField()
    state = serializers.CharField(required=False, allow_blank=True)
    postal_code = serializers.CharField()
    country = serializers.CharField()
    phone = serializers.CharField(required=False, allow_blank=True)


class CheckoutSerializer(serializers.Serializer):
    email = serializers.EmailField()
    phone = serializers.CharField(required=False, allow_blank=True)
    shipping_address = AddressSerializer()
    billing_address = AddressSerializer(required=False)
    customer_note = serializers.CharField(required=False, allow_blank=True)
    coupon_code = serializers.CharField(required=False, allow_blank=True)
    payment_method = serializers.CharField(required=False, allow_blank=True)


# --- reviews ----------------------------------------------

class ReviewSerializer(serializers.ModelSerializer):
    class Meta:
        model = Review
        fields = ["author_name", "rating", "title", "body", "is_verified_purchase",
                  "helpful_count", "admin_reply", "created_at"]


class SubmitReviewSerializer(serializers.Serializer):
    product = serializers.SlugField()
    author_name = serializers.CharField(max_length=120)
    author_email = serializers.EmailField()
    rating = serializers.IntegerField(min_value=1, max_value=5)
    title = serializers.CharField(max_length=160, required=False, allow_blank=True)
    body = serializers.CharField(required=False, allow_blank=True)


# --- shipping / coupons ----------------------------------

class ShippingQuoteSerializer(serializers.Serializer):
    country = serializers.CharField()
    state = serializers.CharField(required=False, allow_blank=True)
    postal_code = serializers.CharField()
    subtotal = serializers.DecimalField(max_digits=12, decimal_places=2)
    weight = serializers.DecimalField(max_digits=8, decimal_places=3, required=False, default=0)
    cod = serializers.BooleanField(default=False)


class CouponValidateSerializer(serializers.Serializer):
    code = serializers.CharField()
    subtotal = serializers.DecimalField(max_digits=12, decimal_places=2)
    email = serializers.EmailField(required=False, allow_blank=True)


# --- auth / account -------------------------------------

class RegisterSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)
    first_name = serializers.CharField(required=False, allow_blank=True)
    last_name = serializers.CharField(required=False, allow_blank=True)

    def validate_email(self, value):
        if User.objects.filter(username__iexact=value).exists() or User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("An account with this email already exists.")
        return value.lower()

    def validate_password(self, value):
        password_validation.validate_password(value)
        return value


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)


class MeSerializer(serializers.Serializer):
    email = serializers.EmailField(read_only=True)
    first_name = serializers.CharField(read_only=True)
    last_name = serializers.CharField(read_only=True)
    is_staff = serializers.BooleanField(read_only=True)
