"""Server-rendered storefront (Jinja2).

A reference frontend for the headless backend: real Django views + Jinja2
templates that read through the app services, not the JSON API. Guest checkout
only; customer accounts stay on the API side.
"""

from django.contrib import messages
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from apps.cart import services as cart_svc
from apps.catalog.models import Product, Variant
from apps.categories.models import Category
from apps.checkout import services as checkout_svc
from apps.cms.models import Page
from apps.orders.models import Order
from apps.reviews import services as reviews_svc
from apps.reviews.models import ReviewStatus
from apps.seo import services as seo_svc

from .services import base_context, current_project, get_cart


class HomeView(View):
    per_page = 24

    def get(self, request):
        project = current_project(request)
        ctx = base_context(request, project)
        products = (
            Product.objects.filter(project=project, status="active", search_indexed=True)
            .select_related("brand", "category").prefetch_related("images")
        )
        category = request.GET.get("category", "").strip()
        if category:
            products = products.filter(category__slug=category)
        q = request.GET.get("q", "").strip()
        if q:
            products = products.filter(title__icontains=q)
        page = Paginator(products.order_by("-created_at"), self.per_page).get_page(
            request.GET.get("page")
        )
        ctx.update({
            "products": page.object_list,
            "page_obj": page,
            "categories": Category.objects.filter(project=project, is_active=True),
            "active_category": category,
            "query": q,
        })
        return render(request, "storefront/home.jinja", ctx)


class PageView(View):
    def get(self, request, slug):
        project = current_project(request)
        page = Page.objects.filter(project=project, slug=slug).first()
        if page is None or not page.is_live:
            raise Http404
        ctx = base_context(request, project)
        ctx["page"] = page
        return render(request, "storefront/page.jinja", ctx)


class ProductView(View):
    def get(self, request, slug):
        project = current_project(request)
        product = get_object_or_404(
            Product.objects.select_related("brand", "category").prefetch_related("images", "variants"),
            project=project, slug=slug, status="active",
        )
        ctx = base_context(request, project)
        ctx.update({
            "product": product,
            "variants": product.variants.filter(is_active=True),
            "reviews": product.reviews.filter(status=ReviewStatus.APPROVED),
            "meta": seo_svc.meta_for(project, path=f"/product/{product.slug}/",
                                     obj=product, obj_type="product"),
        })
        return render(request, "storefront/product.jinja", ctx)


class ReviewSubmitView(View):
    def post(self, request, slug):
        project = current_project(request)
        product = get_object_or_404(Product, project=project, slug=slug)
        try:
            reviews_svc.submit_review(
                project=project, product=product,
                author_name=request.POST.get("author_name", "").strip(),
                author_email=request.POST.get("author_email", "").strip(),
                rating=int(request.POST.get("rating") or 0),
                title=request.POST.get("title", "").strip(),
                body=request.POST.get("body", "").strip(),
            )
            messages.success(request, "Thanks — your review is awaiting moderation.")
        except (reviews_svc.ReviewError, ValueError) as exc:
            messages.error(request, str(exc))
        return redirect("storefront:product", slug=slug)


class CartView(View):
    def get(self, request):
        project = current_project(request)
        return render(request, "storefront/cart.jinja", base_context(request, project))


class CartAddView(View):
    def post(self, request):
        project = current_project(request)
        cart = get_cart(request, project)
        product = get_object_or_404(Product, project=project, slug=request.POST.get("product"), status="active")
        variant = None
        if request.POST.get("variant"):
            variant = get_object_or_404(Variant, product=product, pk=request.POST["variant"])
        qty = max(1, int(request.POST.get("quantity") or 1))
        cart_svc.add_to_cart(cart=cart, product=product, variant=variant, quantity=qty)
        messages.success(request, f"Added {product.title} to your cart.")
        return redirect(request.POST.get("next") or "storefront:cart")


class CartUpdateView(View):
    def post(self, request):
        project = current_project(request)
        cart = get_cart(request, project)
        item = cart.items.filter(pk=request.POST.get("item")).first()
        if item is not None:
            cart_svc.set_quantity(cart=cart, item=item, quantity=int(request.POST.get("quantity") or 0))
        return redirect("storefront:cart")


class CartRemoveView(View):
    def post(self, request):
        project = current_project(request)
        cart = get_cart(request, project)
        item = cart.items.filter(pk=request.POST.get("item")).first()
        if item is not None:
            cart_svc.remove_item(cart=cart, item=item)
        return redirect("storefront:cart")


class CheckoutView(View):
    def get(self, request):
        project = current_project(request)
        ctx = base_context(request, project)
        if not ctx["cart"].items.exists():
            return redirect("storefront:cart")
        return render(request, "storefront/checkout.jinja", ctx)

    def post(self, request):
        project = current_project(request)
        cart = get_cart(request, project)
        address = {
            k: request.POST.get(k, "").strip()
            for k in ("name", "line1", "line2", "city", "state", "postal_code", "country", "phone")
        }
        try:
            order, _payment = checkout_svc.complete_checkout(
                project=project,
                cart=cart,
                email=request.POST.get("email", "").strip(),
                phone=address["phone"],
                shipping_address=address,
                customer_note=request.POST.get("customer_note", "").strip(),
                coupon_code=request.POST.get("coupon_code", "").strip() or None,
                payment_method=request.POST.get("payment_method") or "cod",
                user=request.user if request.user.is_authenticated else None,
            )
        except checkout_svc.CheckoutError as exc:
            messages.error(request, str(exc))
            return redirect("storefront:checkout")

        placed = request.session.get("storefront_orders", [])
        request.session["storefront_orders"] = list({*placed, order.number})
        request.session.modified = True
        return redirect("storefront:order", number=order.number)


class OrderView(View):
    def get(self, request, number):
        project = current_project(request)
        if number not in request.session.get("storefront_orders", []):
            raise Http404
        order = get_object_or_404(
            Order.objects.prefetch_related("items"), project=project, number=number
        )
        ctx = base_context(request, project)
        ctx["order"] = order
        return render(request, "storefront/order.jinja", ctx)
