"""Account API v1: register / login (token) / me, order history, wishlist.

Customer identity is the auth user's email scoped to the resolved project.
"""

from django.contrib.auth import authenticate, get_user_model
from django.shortcuts import get_object_or_404
from rest_framework import generics, status
from rest_framework.authentication import TokenAuthentication
from rest_framework.authtoken.models import Token
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts import ratelimit as login_ratelimit
from apps.catalog.models import Product, Variant
from apps.customers import services as customers_svc
from apps.orders.models import Order
from apps.wishlist import services as wishlist_svc

from ..permissions import HasStore
from ..throttling import AuthThrottle
from .serializers import (
    LoginSerializer,
    MeSerializer,
    OrderSerializer,
    RegisterSerializer,
)

User = get_user_model()


class RegisterView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny, HasStore]
    throttle_classes = [AuthThrottle]

    def post(self, request):
        s = RegisterSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        user = User.objects.create_user(
            username=d["email"], email=d["email"], password=d["password"],
            first_name=d.get("first_name", ""), last_name=d.get("last_name", ""),
        )
        customers_svc.get_or_create_customer(
            project=request.project, email=d["email"], user=user,
            first_name=d.get("first_name", ""), last_name=d.get("last_name", ""),
        )
        token, _ = Token.objects.get_or_create(user=user)
        return Response({"token": token.key, "email": user.email}, status=status.HTTP_201_CREATED)


class LoginView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny, HasStore]
    throttle_classes = [AuthThrottle]

    def post(self, request):
        s = LoginSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        email = s.validated_data["email"]
        if login_ratelimit.is_locked(request, email):
            return Response(
                {"error": {"code": "too_many_attempts", "message": login_ratelimit.LOCK_MESSAGE}},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
        user = authenticate(username=email, password=s.validated_data["password"])
        if user is None:
            login_ratelimit.record_failure(request, email)
            return Response(
                {"error": {"code": "authentication_failed", "message": "Invalid email or password."}},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        login_ratelimit.clear(request, email)
        if not user.is_active:
            return Response(
                {"error": {"code": "account_disabled", "message": "This account is disabled."}},
                status=status.HTTP_403_FORBIDDEN,
            )
        token, _ = Token.objects.get_or_create(user=user)
        return Response({"token": token.key, "email": user.email})


class LogoutView(APIView):
    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        Token.objects.filter(user=request.user).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(APIView):
    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(MeSerializer(request.user).data)


class MyOrderListView(generics.ListAPIView):
    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated, HasStore]
    serializer_class = OrderSerializer

    def get_queryset(self):
        return (
            Order.objects.filter(project=self.request.project, email__iexact=self.request.user.email)
            .prefetch_related("items").order_by("-created_at")
        )


class MyOrderDetailView(generics.RetrieveAPIView):
    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated, HasStore]
    serializer_class = OrderSerializer
    lookup_field = "number"

    def get_queryset(self):
        return Order.objects.filter(
            project=self.request.project, email__iexact=self.request.user.email
        ).prefetch_related("items")


class WishlistView(APIView):
    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated, HasStore]

    def _wishlist(self, request):
        customer = customers_svc.get_or_create_customer(
            project=request.project, email=request.user.email, user=request.user,
        )
        return wishlist_svc.get_or_create_wishlist(project=request.project, customer=customer)

    def get(self, request):
        wl = self._wishlist(request)
        return Response({"items": [
            {"product": i.product.slug, "variant": i.variant_id, "title": i.product.title, "note": i.note}
            for i in wl.items.select_related("product", "variant")
        ]})

    def post(self, request):
        wl = self._wishlist(request)
        product = get_object_or_404(Product, project=request.project, slug=request.data.get("product"))
        variant = None
        if request.data.get("variant"):
            variant = get_object_or_404(Variant, product=product, pk=request.data["variant"])
        wishlist_svc.add_item(wishlist=wl, product=product, variant=variant,
                              note=request.data.get("note", ""))
        return Response(status=status.HTTP_201_CREATED)

    def delete(self, request):
        wl = self._wishlist(request)
        product = get_object_or_404(Product, project=request.project, slug=request.data.get("product"))
        variant = None
        if request.data.get("variant"):
            variant = Variant.objects.filter(product=product, pk=request.data["variant"]).first()
        wishlist_svc.remove_item(wishlist=wl, product=product, variant=variant)
        return Response(status=status.HTTP_204_NO_CONTENT)
