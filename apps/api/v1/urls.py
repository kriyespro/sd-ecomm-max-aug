from django.urls import path
from rest_framework.permissions import AllowAny
from rest_framework.renderers import JSONOpenAPIRenderer
from rest_framework.schemas import get_schema_view

from . import views_account as acc
from . import views_admin as adm
from . import views_storefront as sf

schema_view = get_schema_view(
    title="Storefront API",
    version="v1",
    description="Headless commerce API. Store resolved from the request host.",
    public=True,
    renderer_classes=[JSONOpenAPIRenderer],
    authentication_classes=[],
    permission_classes=[AllowAny],
)

app_name = "v1"

urlpatterns = [
    path("schema/", schema_view, name="schema"),

    # store
    path("store/config/", sf.StoreConfigView.as_view(), name="store-config"),

    # catalog
    path("catalog/products/", sf.ProductListView.as_view(), name="product-list"),
    path("catalog/products/<slug:slug>/", sf.ProductDetailView.as_view(), name="product-detail"),
    path("catalog/products/<slug:slug>/reviews/", sf.ProductReviewsView.as_view(), name="product-reviews"),
    path("catalog/categories/", sf.CategoryListView.as_view(), name="category-list"),
    path("catalog/brands/", sf.BrandListView.as_view(), name="brand-list"),

    # cms
    path("cms/pages/<slug:slug>/", sf.PageDetailView.as_view(), name="page-detail"),
    path("cms/navigation/<str:location>/", sf.NavigationView.as_view(), name="navigation"),
    path("cms/faqs/", sf.FAQListView.as_view(), name="faq-list"),

    # cart + checkout
    path("cart/", sf.CartView.as_view(), name="cart"),
    path("cart/items/", sf.CartItemsView.as_view(), name="cart-items"),
    path("cart/items/<int:item_id>/", sf.CartItemDetailView.as_view(), name="cart-item-detail"),
    path("checkout/", sf.CheckoutView.as_view(), name="checkout"),

    # shipping + coupons
    path("shipping/quote/", sf.ShippingQuoteView.as_view(), name="shipping-quote"),
    path("coupons/validate/", sf.CouponValidateView.as_view(), name="coupon-validate"),

    # reviews
    path("reviews/", sf.SubmitReviewView.as_view(), name="review-submit"),

    # auth + account
    path("auth/register/", acc.RegisterView.as_view(), name="auth-register"),
    path("auth/login/", acc.LoginView.as_view(), name="auth-login"),
    path("auth/logout/", acc.LogoutView.as_view(), name="auth-logout"),
    path("auth/me/", acc.MeView.as_view(), name="auth-me"),
    path("orders/", acc.MyOrderListView.as_view(), name="order-list"),
    path("orders/<str:number>/", acc.MyOrderDetailView.as_view(), name="order-detail"),
    path("wishlist/", acc.WishlistView.as_view(), name="wishlist"),

    # admin (read-only)
    path("admin/orders/", adm.AdminOrderListView.as_view(), name="admin-order-list"),
    path("admin/orders/<str:number>/", adm.AdminOrderDetailView.as_view(), name="admin-order-detail"),
    path("admin/products/", adm.AdminProductListView.as_view(), name="admin-product-list"),
    path("admin/inventory/low-stock/", adm.AdminLowStockView.as_view(), name="admin-low-stock"),
]
