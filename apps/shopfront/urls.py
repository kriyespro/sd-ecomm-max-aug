from django.urls import path

from . import views

app_name = "shopfront"

urlpatterns = [
    path("", views.HomeView.as_view(), name="home"),
    path("shop/", views.ShopView.as_view(), name="shop"),
    path("p/<slug:slug>/", views.ProductView.as_view(), name="product"),
    path("p/<slug:slug>/review/", views.ReviewSubmitView.as_view(), name="review"),
    path("quick/<slug:slug>/", views.QuickView.as_view(), name="quickview"),
    path("search/suggest/", views.SearchSuggestView.as_view(), name="search_suggest"),

    path("cart/", views.CartView.as_view(), name="cart"),
    path("cart/add/", views.CartAddView.as_view(), name="cart_add"),
    path("cart/update/", views.CartUpdateView.as_view(), name="cart_update"),
    path("cart/remove/", views.CartRemoveView.as_view(), name="cart_remove"),
    path("cart/drawer/", views.CartDrawerView.as_view(), name="cart_drawer"),

    path("checkout/", views.CheckoutView.as_view(), name="checkout"),
    path("checkout/shipping/", views.ShippingQuoteView.as_view(), name="shipping_quote"),
    path("checkout/coupon/", views.CouponPreviewView.as_view(), name="coupon_preview"),
    path("order/<str:number>/", views.OrderView.as_view(), name="order"),

    path("account/", views.AccountView.as_view(), name="account"),
    path("account/login/", views.LoginView.as_view(), name="login"),
    path("account/register/", views.RegisterView.as_view(), name="register"),
    path("account/logout/", views.LogoutView.as_view(), name="logout"),
    path("wishlist/", views.WishlistPageView.as_view(), name="wishlist"),
    path("wishlist/toggle/", views.WishlistToggleView.as_view(), name="wishlist_toggle"),
    path("track/", views.TrackOrderView.as_view(), name="track"),

    path("page/<slug:slug>/", views.PageView.as_view(), name="page"),
]
