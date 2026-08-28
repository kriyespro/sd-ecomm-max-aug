"""Admin/management API v1 (read-only slice).

Full store management lives in the Mission Control panel; this exposes the same
data to trusted integrations. Gated by staff + active membership in the resolved
project (or platform admin).
"""

from rest_framework import generics
from rest_framework.response import Response
from rest_framework.schemas.openapi import AutoSchema
from rest_framework.views import APIView

from apps.inventory import services as inventory_svc
from apps.orders.models import Order

from ..permissions import IsStoreStaff, resolved_project
from .serializers import OrderSerializer, ProductListSerializer


class AdminMixin:
    permission_classes = [IsStoreStaff]

    @property
    def project(self):
        return resolved_project(self.request)


class AdminOrderListView(AdminMixin, generics.ListAPIView):
    serializer_class = OrderSerializer
    schema = AutoSchema(operation_id_base="AdminOrder")
    ordering_fields = ["created_at", "grand_total", "status"]
    ordering = ["-created_at"]

    def get_queryset(self):
        qs = Order.objects.filter(project=self.project).prefetch_related("items")
        p = self.request.query_params
        if p.get("status"):
            qs = qs.filter(status=p["status"])
        if p.get("payment_status"):
            qs = qs.filter(payment_status=p["payment_status"])
        return qs


class AdminOrderDetailView(AdminMixin, generics.RetrieveAPIView):
    serializer_class = OrderSerializer
    schema = AutoSchema(operation_id_base="AdminOrder")
    lookup_field = "number"

    def get_queryset(self):
        return Order.objects.filter(project=self.project).prefetch_related("items")


class AdminProductListView(AdminMixin, generics.ListAPIView):
    serializer_class = ProductListSerializer
    schema = AutoSchema(operation_id_base="AdminProduct")
    search_fields = ["title", "sku"]

    def get_queryset(self):
        from apps.catalog.models import Product

        return Product.objects.filter(project=self.project).select_related("brand", "category")


class AdminLowStockView(AdminMixin, APIView):
    def get(self, request):
        items = inventory_svc.low_stock_items(request.project)
        return Response({"count": len(items), "items": [
            {"product": i.product.title, "variant": (i.variant.name if i.variant else None),
             "warehouse": i.warehouse.name, "available": i.available,
             "threshold": i.low_stock_threshold}
            for i in items
        ]})
