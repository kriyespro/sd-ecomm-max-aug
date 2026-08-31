"""Shopfront — a full server-rendered storefront to exercise the backend.

Jinja2 + HTMX (partial swaps) + Alpine (UI state). Views read through the app
service layer, the same code the REST API uses. Guest cart via session; customer
accounts via django.contrib.auth.
"""

import json
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth import authenticate, get_user_model, login, logout
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import F, Q, Sum
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.views import View

from .render import render, render_to_string

from apps.cart import services as cart_svc
from apps.catalog.models import Product, Variant
from apps.categories.models import Category
from apps.checkout import services as checkout_svc
from apps.cms.models import Page
from apps.coupons import services as coupons_svc
from apps.customers import services as customers_svc
from apps.orders.models import Order
from apps.reviews import services as reviews_svc
from apps.reviews.models import ReviewStatus
from apps.shipping import services as ship_svc
from apps.wishlist import services as wishlist_svc

from .context import base_context, current_project, get_cart

User = get_user_model()


def _htmx(request):
    return request.headers.get("HX-Request") == "true"


# --- home ---------------------------------------------------------

class HomeView(View):
    def get(self, request):
        from apps.reviews.models import Review

        project = current_project(request)
        catalogue = list(
            Product.objects.filter(project=project, status="active", search_indexed=True)
            .select_related("brand", "category").prefetch_related("images")
            .order_by("-created_at")
        )

        featured = [p for p in catalogue if p.is_featured][:8] or catalogue[:8]
        new_arrivals = [p for p in catalogue if p.is_new_arrival][:8] or catalogue[:8]

        # one representative image per category for the tile grid — from the
        # already-loaded catalogue, no extra queries
        tiles, seen = [], set()
        for p in catalogue:
            c = p.category
            if c and c.id not in seen and c.is_active:
                seen.add(c.id)
                imgs = list(p.images.all())
                tiles.append({"category": c, "image": imgs[0].image.url if imgs else None})
            if len(tiles) == 6:
                break

        testimonials = list(
            Review.objects.filter(project=project, status=ReviewStatus.APPROVED)
            .exclude(body="").select_related("product")
            .order_by("-rating", "-created_at")[:3]
        )

        ctx = base_context(
            request, project,
            featured=featured, new_arrivals=new_arrivals,
            cat_tiles=tiles, testimonials=testimonials,
        )
        return render(request, "shopfront/home.jinja", ctx)


# --- catalog -----------------------------------------------------

class ShopView(View):
    per_page = 12

    def get(self, request):
        project = current_project(request)
        qs = (
            Product.objects.filter(project=project, status="active", search_indexed=True)
            .select_related("brand", "category").prefetch_related("images")
        )
        f = request.GET
        cat = f.get("category", "").strip()
        if cat:
            qs = qs.filter(category__slug=cat)
        q = f.get("q", "").strip()
        if q:
            qs = qs.filter(Q(title__icontains=q) | Q(short_description__icontains=q))
        for key, lookup in (("min", "price__gte"), ("max", "price__lte")):
            raw = f.get(key, "").strip()
            if raw:
                try:
                    qs = qs.filter(**{lookup: Decimal(raw)})
                except Exception:  # noqa: BLE001
                    pass
        sort = f.get("sort", "new")
        qs = {
            "new": qs.order_by("-created_at"),
            "price_asc": qs.order_by("price"),
            "price_desc": qs.order_by("-price"),
            "rating": qs.order_by("-rating_avg", "-rating_count"),
            "name": qs.order_by("title"),
        }.get(sort, qs.order_by("-created_at"))

        page = Paginator(qs, self.per_page).get_page(f.get("page"))
        ctx = base_context(
            request, project,
            page_obj=page, products=page.object_list,
            active_category=cat, query=q, sort=sort,
            price_min=f.get("min", ""), price_max=f.get("max", ""),
            all_categories=Category.objects.filter(project=project, is_active=True),
            result_count=page.paginator.count,
        )
        if _htmx(request):
            return render(request, "shopfront/partials/_grid.jinja", ctx)
        return render(request, "shopfront/shop.jinja", ctx)


# --- product -----------------------------------------------------

def _available(product):
    agg = product.inventory_items.aggregate(a=Sum(F("quantity") - F("reserved")))
    return agg["a"]


def _rating_breakdown(reviews_qs):
    counts = {5: 0, 4: 0, 3: 0, 2: 0, 1: 0}
    total = 0
    for r in reviews_qs.values_list("rating", flat=True):
        counts[r] = counts.get(r, 0) + 1
        total += 1
    return [
        {"stars": s, "count": counts[s], "pct": (counts[s] * 100 // total) if total else 0}
        for s in (5, 4, 3, 2, 1)
    ], total


class ProductView(View):
    def get(self, request, slug):
        from datetime import timedelta

        from django.utils import timezone

        from apps.shipping.models import ShippingMethod

        project = current_project(request)
        product = get_object_or_404(
            Product.objects.select_related("brand", "category").prefetch_related("images", "variants"),
            project=project, slug=slug, status="active",
        )
        related = list(
            Product.objects.filter(project=project, status="active", category=product.category)
            .exclude(pk=product.pk)
            .select_related("brand", "category").prefetch_related("images")[:4]
        )

        # recently viewed (session) — only maintained once the visitor already
        # has a session, so a fresh anonymous PDP hit stays cookie-free / edge
        # cacheable.
        recent = [slug]
        if request.session.session_key:
            recent += [s for s in request.session.get("recent_slugs", []) if s != slug]
            request.session["recent_slugs"] = recent[:10]
            request.session.modified = True
        rv_slugs = recent[1:7]
        rv_map = {
            p.slug: p for p in Product.objects.filter(
                project=project, slug__in=rv_slugs, status="active"
            ).select_related("brand", "category").prefetch_related("images")
        }
        recently_viewed = [rv_map[s] for s in rv_slugs if s in rv_map]

        # delivery estimate from the cheapest active method
        method = (
            ShippingMethod.objects.filter(project=project, is_active=True)
            .order_by("base_rate").first()
        )
        delivery = None
        if method:
            now = timezone.localdate()
            delivery = {
                "min": now + timedelta(days=method.min_days),
                "max": now + timedelta(days=method.max_days),
                "label": method.estimate_label(),
                "free_over": method.free_over,
            }

        reviews_qs = product.reviews.filter(status=ReviewStatus.APPROVED)
        breakdown, review_total = _rating_breakdown(reviews_qs)

        ctx = base_context(
            request, project,
            product=product,
            # reuse the prefetched variants instead of re-querying
            variants=[v for v in product.variants.all() if v.is_active],
            reviews=reviews_qs,
            rating_breakdown=breakdown,
            review_total=review_total,
            related=related,
            recently_viewed=recently_viewed,
            delivery=delivery,
            available=_available(product),
        )
        return render(request, "shopfront/product.jinja", ctx)


class QuickView(View):
    def get(self, request, slug):
        project = current_project(request)
        product = get_object_or_404(
            Product.objects.select_related("category").prefetch_related("images", "variants"),
            project=project, slug=slug, status="active",
        )
        return render(request, "shopfront/partials/_quickview.jinja", base_context(
            request, project, product=product,
            variants=list(product.variants.filter(is_active=True)),
            available=_available(product),
        ))


class SearchSuggestView(View):
    def get(self, request):
        project = current_project(request)
        q = request.GET.get("q", "").strip()
        results = []
        if len(q) >= 2:
            results = list(
                Product.objects.filter(project=project, status="active", search_indexed=True)
                .filter(Q(title__icontains=q) | Q(sku__icontains=q))
                .prefetch_related("images")[:6]
            )
        return render(request, "shopfront/partials/_suggest.jinja",
                      base_context(request, project, suggestions=results, query=q))


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
            ok, msg = True, "Thanks — your review is awaiting moderation."
        except (reviews_svc.ReviewError, ValueError) as exc:
            ok, msg = False, str(exc)
        reviews_qs = product.reviews.filter(status=ReviewStatus.APPROVED)
        breakdown, total = _rating_breakdown(reviews_qs)
        ctx = base_context(
            request, project, product=product,
            reviews=reviews_qs, rating_breakdown=breakdown, review_total=total,
            review_ok=ok, review_msg=msg,
        )
        return render(request, "shopfront/partials/_reviews.jinja", ctx)


# --- cart --------------------------------------------------------

class CartView(View):
    def get(self, request):
        project = current_project(request)
        return render(request, "shopfront/cart.jinja", base_context(request, project))


class CartAddView(View):
    def post(self, request):
        project = current_project(request)
        cart = get_cart(request, project, create=True)
        product = get_object_or_404(Product, project=project, slug=request.POST.get("product"), status="active")
        variant = None
        if request.POST.get("variant"):
            variant = get_object_or_404(Variant, product=product, pk=request.POST["variant"])
        qty = max(1, int(request.POST.get("quantity") or 1))
        cart_svc.add_to_cart(cart=cart, product=product, variant=variant, quantity=qty)
        resp = render(request, "shopfront/partials/_cart_fragments.jinja",
                      base_context(request, project))
        resp["HX-Trigger"] = json.dumps({
            "toast": {"msg": f"Added — {product.title}", "kind": "success"},
            "cart-open": True,
        })
        return resp


def _cart_mutation_response(request, project):
    ctx = base_context(request, project)
    which = request.POST.get("view", "page")
    tmpl = "shopfront/partials/_cart_drawer.jinja" if which == "drawer" else "shopfront/partials/_cart_page.jinja"
    body = render_to_string(tmpl, ctx, request)
    badge = render_to_string("shopfront/partials/_cart_fragments.jinja", ctx, request)
    return HttpResponse(body + badge)


class CartUpdateView(View):
    def post(self, request):
        project = current_project(request)
        cart = get_cart(request, project, create=True)
        item = cart.items.filter(pk=request.POST.get("item")).first()
        if item is not None:
            cart_svc.set_quantity(cart=cart, item=item, quantity=int(request.POST.get("quantity") or 0))
        return _cart_mutation_response(request, project)


class CartRemoveView(View):
    def post(self, request):
        project = current_project(request)
        cart = get_cart(request, project, create=True)
        item = cart.items.filter(pk=request.POST.get("item")).first()
        if item is not None:
            cart_svc.remove_item(cart=cart, item=item)
        return _cart_mutation_response(request, project)


class CartDrawerView(View):
    def get(self, request):
        project = current_project(request)
        return render(request, "shopfront/partials/_cart_drawer.jinja", base_context(request, project))


# --- checkout ---------------------------------------------------

def _address(post):
    return {k: post.get(k, "").strip() for k in
            ("name", "line1", "line2", "city", "state", "postal_code", "country", "phone")}


class CheckoutView(View):
    def get(self, request):
        project = current_project(request)
        ctx = base_context(request, project)
        if not ctx["cart"].items.exists():
            return redirect("shopfront:cart")
        return render(request, "shopfront/checkout.jinja", ctx)

    def post(self, request):
        project = current_project(request)
        cart = get_cart(request, project, create=True)
        if not cart.items.exists():
            return redirect("shopfront:cart")
        address = _address(request.POST)
        coupon = request.POST.get("coupon_code", "").strip()
        method_id = request.POST.get("shipping_method", "").strip()

        # validate the coupon up-front so a bad code never leaves an order behind
        if coupon:
            try:
                coupons_svc.validate_coupon(
                    project=project, code=coupon, subtotal=cart.subtotal,
                    customer_email=request.POST.get("email", "").strip(),
                )
            except coupons_svc.CouponError as exc:
                messages.error(request, str(exc))
                return redirect("shopfront:checkout")

        try:
            order, _payment = checkout_svc.complete_checkout(
                project=project, cart=cart,
                email=request.POST.get("email", "").strip(),
                phone=address["phone"], shipping_address=address,
                customer_note=request.POST.get("customer_note", "").strip(),
                coupon_code=coupon or None,
                payment_method=request.POST.get("payment_method") or "cod",
                user=request.user if request.user.is_authenticated else None,
            )
        except checkout_svc.CheckoutError as exc:
            messages.error(request, str(exc))
            return redirect("shopfront:checkout")

        if method_id:
            method = project.shippingmethods.filter(pk=method_id).first() if hasattr(project, "shippingmethods") else None
            if method is None:
                from apps.shipping.models import ShippingMethod
                method = ShippingMethod.objects.filter(project=project, pk=method_id).first()
            if method is not None:
                try:
                    ship_svc.set_order_shipping(order=order, method=method)
                except ship_svc.ShippingError:
                    pass

        placed = request.session.get("shopfront_orders", [])
        request.session["shopfront_orders"] = list({*placed, order.number})
        request.session.modified = True
        return redirect("shopfront:order", number=order.number)


class ShippingQuoteView(View):
    def post(self, request):
        project = current_project(request)
        cart = get_cart(request, project, create=True)
        a = _address(request.POST)
        methods = ship_svc.available_methods(
            project=project,
            address={"country": a["country"] or "IN", "state": a["state"], "postal_code": a["postal_code"]},
            subtotal=cart.subtotal, weight=Decimal("0"),
            cod=(request.POST.get("payment_method") == "cod"),
        )
        return render(request, "shopfront/partials/_shipping_methods.jinja",
                      base_context(request, project, shipping_methods=methods))


class CouponPreviewView(View):
    def post(self, request):
        project = current_project(request)
        cart = get_cart(request, project, create=True)
        code = request.POST.get("coupon_code", "").strip()
        discount, msg, ok = Decimal("0"), "", False
        if code:
            try:
                coupon = coupons_svc.validate_coupon(
                    project=project, code=code, subtotal=cart.subtotal,
                    customer_email=(request.user.email if request.user.is_authenticated else ""),
                )
                discount = coupons_svc.quote_discount(
                    coupon, order_items=list(cart.items.select_related("product")),
                    subtotal=cart.subtotal,
                )
                ok, msg = True, f"Coupon applied — you save {discount}"
            except coupons_svc.CouponError as exc:
                msg = str(exc)
        ctx = base_context(request, project, coupon_code=code, coupon_ok=ok,
                           coupon_msg=msg, coupon_discount=discount)
        return render(request, "shopfront/partials/_checkout_summary.jinja", ctx)


class OrderView(View):
    def get(self, request, number):
        project = current_project(request)
        allowed = number in request.session.get("shopfront_orders", [])
        if request.user.is_authenticated:
            allowed = allowed or Order.objects.filter(
                project=project, number=number, email__iexact=request.user.email
            ).exists()
        if not allowed:
            raise Http404
        order = get_object_or_404(Order.objects.prefetch_related("items"), project=project, number=number)
        return render(request, "shopfront/order.jinja", base_context(request, project, order=order))


# --- account ---------------------------------------------------

class AccountView(View):
    def get(self, request):
        project = current_project(request)
        ctx = base_context(request, project)
        if request.user.is_authenticated:
            ctx["orders"] = Order.objects.filter(
                project=project, email__iexact=request.user.email
            ).order_by("-created_at")[:20]
            customer = customers_svc.get_or_create_customer(
                project=project, email=request.user.email, user=request.user
            )
            wl = wishlist_svc.get_or_create_wishlist(project=project, customer=customer)
            ctx["wishlist_items"] = [
                w.product for w in wl.items
                .select_related("product", "product__brand", "product__category")
                .prefetch_related("product__images")
            ]
        return render(request, "shopfront/account.jinja", ctx)


class LoginView(View):
    def post(self, request):
        project = current_project(request)
        email = request.POST.get("email", "").strip()
        user = authenticate(request, username=email, password=request.POST.get("password", ""))
        if user is None:
            messages.error(request, "Invalid email or password.")
        else:
            login(request, user)
            messages.success(request, "Signed in.")
        return redirect(request.POST.get("next") or "shopfront:account")


class RegisterView(View):
    def post(self, request):
        project = current_project(request)
        email = request.POST.get("email", "").strip().lower()
        password = request.POST.get("password", "")
        if User.objects.filter(username__iexact=email).exists() or User.objects.filter(email__iexact=email).exists():
            messages.error(request, "An account with this email already exists.")
            return redirect("shopfront:account")
        try:
            validate_password(password)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
            return redirect("shopfront:account")
        user = User.objects.create_user(
            username=email, email=email, password=password,
            first_name=request.POST.get("first_name", "").strip(),
            last_name=request.POST.get("last_name", "").strip(),
        )
        customers_svc.get_or_create_customer(project=project, email=email, user=user)
        login(request, user)
        messages.success(request, "Account created.")
        return redirect("shopfront:account")


class LogoutView(View):
    def post(self, request):
        logout(request)
        messages.success(request, "Signed out.")
        return redirect("shopfront:home")


class WishlistToggleView(View):
    def post(self, request):
        project = current_project(request)
        if not request.user.is_authenticated:
            return render(request, "shopfront/partials/_wishlist_btn.jinja",
                          base_context(request, project, wl_need_login=True,
                                       wl_slug=request.POST.get("product", "")))
        product = get_object_or_404(Product, project=project, slug=request.POST.get("product"))
        customer = customers_svc.get_or_create_customer(
            project=project, email=request.user.email, user=request.user
        )
        wl = wishlist_svc.get_or_create_wishlist(project=project, customer=customer)
        in_list = wl.items.filter(product=product).exists()
        if in_list:
            wishlist_svc.remove_item(wishlist=wl, product=product)
        else:
            wishlist_svc.add_item(wishlist=wl, product=product)
        return render(request, "shopfront/partials/_wishlist_btn.jinja",
                      base_context(request, project, wl_slug=product.slug, wl_active=not in_list))


class WishlistPageView(View):
    def get(self, request):
        project = current_project(request)
        ctx = base_context(request, project)
        if request.user.is_authenticated:
            customer = customers_svc.get_or_create_customer(
                project=project, email=request.user.email, user=request.user
            )
            wl = wishlist_svc.get_or_create_wishlist(project=project, customer=customer)
            ctx["wishlist_products"] = [
                w.product for w in wl.items.select_related("product").prefetch_related("product__images")
            ]
        return render(request, "shopfront/wishlist.jinja", ctx)


class TrackOrderView(View):
    def get(self, request):
        return render(request, "shopfront/track.jinja", base_context(request, current_project(request)))

    def post(self, request):
        project = current_project(request)
        number = request.POST.get("number", "").strip().upper()
        email = request.POST.get("email", "").strip()
        order = Order.objects.filter(
            project=project, number=number, email__iexact=email
        ).prefetch_related("items", "events").first()
        ctx = base_context(request, project, order=order,
                           tracked=True, track_number=number, track_email=email)
        return render(request, "shopfront/track.jinja", ctx)


# --- cms pages ------------------------------------------------

class PageView(View):
    def get(self, request, slug):
        project = current_project(request)
        page = Page.objects.filter(project=project, slug=slug).first()
        if page is None or not page.is_live:
            raise Http404
        return render(request, "shopfront/page.jinja", base_context(request, project, page=page))
