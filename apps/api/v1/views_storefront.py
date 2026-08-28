"""Storefront (public) API v1.

Read access to catalog / CMS plus the cart + checkout flow. The project is
resolved from the Host header; no endpoint trusts a client-supplied store id.
Anonymous carts are keyed by an opaque ``X-Cart-Token`` the client stores.
"""

from decimal import Decimal

from django.shortcuts import get_object_or_404
from django.utils.crypto import get_random_string
from rest_framework import generics, status
from rest_framework.authentication import TokenAuthentication
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.cart import services as cart_svc
from apps.cart.models import Cart, CartItem
from apps.catalog.models import Brand, Product, Variant
from apps.categories.models import Category
from apps.checkout import services as checkout_svc
from apps.cms import services as cms_svc
from apps.cms.models import FAQ, Page
from apps.coupons import services as coupons_svc
from apps.reviews import services as reviews_svc
from apps.reviews.models import Review, ReviewStatus
from apps.seo import services as seo_svc
from apps.shipping import services as shipping_svc

from ..permissions import HasStore, resolved_project
from ..throttling import CheckoutThrottle
from .serializers import (
    AddToCartSerializer,
    BrandSerializer,
    CartSerializer,
    CategorySerializer,
    CheckoutSerializer,
    CouponValidateSerializer,
    FAQSerializer,
    OrderSerializer,
    PageSerializer,
    ProductDetailSerializer,
    ProductListSerializer,
    ReviewSerializer,
    ShippingQuoteSerializer,
    SubmitReviewSerializer,
    UpdateCartItemSerializer,
)

CART_TOKEN_HEADER = "HTTP_X_CART_TOKEN"


class StorefrontMixin:
    # Public storefront API: token auth only. It must NOT authenticate via the
    # Django admin session cookie — doing so would trigger CSRF enforcement on
    # guest POSTs (cart / checkout) from a browser that also has an admin session.
    authentication_classes = [TokenAuthentication]
    permission_classes = [AllowAny, HasStore]

    @property
    def project(self):
        return resolved_project(self.request)


# --- store config -------------------------------------------

class StoreConfigView(StorefrontMixin, APIView):
    def get(self, request, *args, **kwargs):
        return Response(cms_svc.store_config(request.project))


# --- catalog ---------------------------------------------

class ProductListView(StorefrontMixin, generics.ListAPIView):
    serializer_class = ProductListSerializer
    search_fields = ["title", "sku", "short_description"]
    ordering_fields = ["price", "created_at", "rating_avg", "title"]
    ordering = ["-created_at"]

    def get_queryset(self):
        qs = (
            Product.objects.filter(project=self.project, status="active", search_indexed=True)
            .select_related("brand", "category").prefetch_related("images")
        )
        p = self.request.query_params
        if p.get("category"):
            qs = qs.filter(category__slug=p["category"])
        if p.get("brand"):
            qs = qs.filter(brand__slug=p["brand"])
        if p.get("min_price"):
            qs = qs.filter(price__gte=p["min_price"])
        if p.get("max_price"):
            qs = qs.filter(price__lte=p["max_price"])
        if p.get("featured") == "true":
            qs = qs.filter(is_featured=True)
        if p.get("new") == "true":
            qs = qs.filter(is_new_arrival=True)
        return qs


class ProductDetailView(StorefrontMixin, generics.RetrieveAPIView):
    serializer_class = ProductDetailSerializer
    lookup_field = "slug"

    def get_queryset(self):
        return (
            Product.objects.filter(project=self.project, status="active")
            .select_related("brand", "category").prefetch_related("images", "variants")
        )

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        data = self.get_serializer(instance).data
        data["meta"] = seo_svc.meta_for(
            request.project, path=f"/product/{instance.slug}/", obj=instance, obj_type="product"
        )
        return Response(data)


class ProductReviewsView(StorefrontMixin, generics.ListAPIView):
    serializer_class = ReviewSerializer

    def get_queryset(self):
        product = get_object_or_404(Product, project=self.project, slug=self.kwargs["slug"])
        return product.reviews.filter(status=ReviewStatus.APPROVED)


class CategoryListView(StorefrontMixin, generics.ListAPIView):
    serializer_class = CategorySerializer
    pagination_class = None

    def get_queryset(self):
        return Category.objects.filter(project=self.project, is_active=True).select_related("parent")


class BrandListView(StorefrontMixin, generics.ListAPIView):
    serializer_class = BrandSerializer
    pagination_class = None

    def get_queryset(self):
        return Brand.objects.filter(project=self.project, is_active=True)


# --- cms ------------------------------------------------

class PageDetailView(StorefrontMixin, APIView):
    def get(self, request, slug):
        page = Page.objects.filter(project=request.project, slug=slug).first()
        if page is None or not page.is_live:
            return Response({"error": {"code": "not_found", "message": "Not found."}}, status=404)
        data = PageSerializer(page).data
        data["meta"] = seo_svc.meta_for(request.project, path=f"/{page.slug}/", obj=page, obj_type="page")
        return Response(data)


class NavigationView(StorefrontMixin, APIView):
    def get(self, request, location):
        return Response({"location": location, "items": cms_svc.menu_tree(request.project, location)})


class FAQListView(StorefrontMixin, generics.ListAPIView):
    serializer_class = FAQSerializer
    pagination_class = None

    def get_queryset(self):
        return FAQ.objects.filter(project=self.project, is_active=True)


# --- cart --------------------------------------------

def _resolve_cart(request, *, create=True):
    user = request.user if request.user.is_authenticated else None
    token = request.META.get(CART_TOKEN_HEADER, "")
    if user is not None:
        cart = Cart.objects.filter(project=request.project, user=user, is_active=True).first()
        if cart is None and create:
            cart = Cart.objects.create(project=request.project, user=user)
        return cart, (cart.session_key if cart else "")
    cart = None
    if token:
        cart = Cart.objects.filter(
            project=request.project, session_key=token, user__isnull=True, is_active=True
        ).first()
    if cart is None and create:
        token = get_random_string(32)
        cart = Cart.objects.create(project=request.project, session_key=token)
    return cart, token


class CartView(StorefrontMixin, APIView):
    def get(self, request):
        cart, token = _resolve_cart(request, create=True)
        return _cart_response(cart, token)

    def delete(self, request):
        cart, token = _resolve_cart(request, create=False)
        if cart:
            cart_svc.clear_cart(cart)
        return _cart_response(cart, token)


class CartItemsView(StorefrontMixin, APIView):
    def post(self, request):
        s = AddToCartSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        cart, token = _resolve_cart(request, create=True)
        product = get_object_or_404(Product, project=request.project, slug=s.validated_data["product"])
        variant = None
        if s.validated_data.get("variant"):
            variant = get_object_or_404(Variant, product=product, pk=s.validated_data["variant"])
        cart_svc.add_to_cart(cart=cart, product=product, variant=variant,
                             quantity=s.validated_data["quantity"])
        return _cart_response(cart, token, status_code=status.HTTP_201_CREATED)


class CartItemDetailView(StorefrontMixin, APIView):
    def _item(self, request, item_id):
        cart, token = _resolve_cart(request, create=False)
        if cart is None:
            return None, None, token
        return cart, get_object_or_404(CartItem, cart=cart, pk=item_id), token

    def patch(self, request, item_id):
        cart, item, token = self._item(request, item_id)
        if item is None:
            return Response({"error": {"code": "not_found", "message": "Cart is empty."}}, status=404)
        s = UpdateCartItemSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        cart_svc.set_quantity(cart=cart, item=item, quantity=s.validated_data["quantity"])
        return _cart_response(cart, token)

    def delete(self, request, item_id):
        cart, item, token = self._item(request, item_id)
        if item is not None:
            cart_svc.remove_item(cart=cart, item=item)
        return _cart_response(cart, token)


def _cart_response(cart, token, status_code=status.HTTP_200_OK):
    if cart is None:
        body = {"cart": None, "token": token}
    else:
        body = {"cart": CartSerializer(cart).data, "token": token}
    resp = Response(body, status=status_code)
    if token:
        resp["X-Cart-Token"] = token
    return resp


# --- shipping / coupons quote ------------------------

class ShippingQuoteView(StorefrontMixin, APIView):
    def post(self, request):
        s = ShippingQuoteSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        address = {"country": d["country"], "state": d.get("state", ""), "postal_code": d["postal_code"]}
        methods = shipping_svc.available_methods(
            project=request.project, address=address,
            subtotal=d["subtotal"], weight=d.get("weight") or Decimal("0"), cod=d["cod"],
        )
        return Response({"methods": [
            {"id": m.pk, "name": m.name, "carrier": m.carrier, "amount": str(q),
             "cod_available": m.cod_available, "estimate": m.estimate_label()}
            for m, q in methods
        ]})


class CouponValidateView(StorefrontMixin, APIView):
    def post(self, request):
        s = CouponValidateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        try:
            coupon = coupons_svc.validate_coupon(
                project=request.project, code=s.validated_data["code"],
                subtotal=s.validated_data["subtotal"],
                customer_email=s.validated_data.get("email", ""),
            )
        except coupons_svc.CouponError as exc:
            return Response({"valid": False, "message": str(exc)}, status=400)
        return Response({
            "valid": True, "code": coupon.code, "discount_type": coupon.discount_type,
            "value": str(coupon.value), "description": coupon.description,
        })


# --- reviews (submit) --------------------------------

class SubmitReviewView(StorefrontMixin, APIView):
    throttle_scope = "write"

    def post(self, request):
        s = SubmitReviewSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        product = get_object_or_404(Product, project=request.project, slug=s.validated_data["product"])
        try:
            review = reviews_svc.submit_review(
                project=request.project, product=product,
                author_name=s.validated_data["author_name"],
                author_email=s.validated_data["author_email"],
                rating=s.validated_data["rating"],
                title=s.validated_data.get("title", ""),
                body=s.validated_data.get("body", ""),
            )
        except reviews_svc.ReviewError as exc:
            return Response({"error": {"code": "review_error", "message": str(exc)}}, status=400)
        return Response(ReviewSerializer(review).data, status=status.HTTP_201_CREATED)


# --- checkout ---------------------------------------

class CheckoutView(StorefrontMixin, APIView):
    throttle_classes = [CheckoutThrottle]

    def post(self, request):
        s = CheckoutSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        cart, token = _resolve_cart(request, create=False)
        if cart is None or not cart.items.exists():
            return Response({"error": {"code": "empty_cart", "message": "Cart is empty."}}, status=400)

        d = s.validated_data
        try:
            order, payment_context = checkout_svc.complete_checkout(
                project=request.project,
                cart=cart,
                email=d["email"],
                phone=d.get("phone", ""),
                shipping_address=d["shipping_address"],
                billing_address=d.get("billing_address"),
                customer_note=d.get("customer_note", ""),
                coupon_code=d.get("coupon_code") or None,
                payment_method=d.get("payment_method") or None,
                user=request.user if request.user.is_authenticated else None,
            )
        except checkout_svc.CheckoutError as exc:
            return Response({"error": {"code": "checkout_error", "message": str(exc)}}, status=400)

        return Response(
            {"order": OrderSerializer(order).data, "payment": payment_context},
            status=status.HTTP_201_CREATED,
        )
