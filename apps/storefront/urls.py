from django.urls import path

from . import views

app_name = "storefront"

urlpatterns = [
    path("", views.HomeView.as_view(), name="home"),
    path("cart/", views.CartView.as_view(), name="cart"),
    path("cart/add/", views.CartAddView.as_view(), name="cart_add"),
    path("cart/update/", views.CartUpdateView.as_view(), name="cart_update"),
    path("cart/remove/", views.CartRemoveView.as_view(), name="cart_remove"),
    path("checkout/", views.CheckoutView.as_view(), name="checkout"),
    path("order/<str:number>/", views.OrderView.as_view(), name="order"),
    path("product/<slug:slug>/", views.ProductView.as_view(), name="product"),
    path("product/<slug:slug>/review/", views.ReviewSubmitView.as_view(), name="review_submit"),
    path("p/<slug:slug>/", views.PageView.as_view(), name="page"),
]
