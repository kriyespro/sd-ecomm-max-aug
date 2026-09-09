"""Control-panel CRUD for catalog + categories, scoped to the active project.

Thin views: querysets filtered by ``self.active_project``; forms receive it as a
kwarg; mutations recorded to the audit log.
"""

import os

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.files.base import ContentFile
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import (
    CreateView,
    DeleteView,
    ListView,
    TemplateView,
    UpdateView,
)

from apps.catalog import importer as product_importer
from apps.catalog.models import Brand, Product, ProductImage, ProductType, Tag
from apps.categories.models import Category
from apps.core.events import Events, emit
from apps.core.models import AuditLog
from apps.core.services import record_audit

from .forms import (
    BrandForm,
    CategoryForm,
    ProductForm,
    ProductImportForm,
    ProductTypeForm,
    TagForm,
)
from .mixins import ActiveProjectMixin
from .trash import (
    TRASH_RETENTION_DAYS,
    purge_product,
    restore_product,
    trash_product,
)


class _ScopedQuerysetMixin(ActiveProjectMixin):
    model = None

    def get_queryset(self):
        return self.model.objects.filter(project=self.active_project)


class _ScopedFormMixin(_ScopedQuerysetMixin):
    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["project"] = self.active_project
        return kwargs

    def form_valid(self, form):
        response = super().form_valid(form)
        action = AuditLog.Action.CREATE if isinstance(self, CreateView) else AuditLog.Action.UPDATE
        record_audit(
            actor=self.request.user,
            project=self.active_project,
            action=action,
            target=self.object,
            request=self.request,
        )
        messages.success(self.request, f"{self.model.__name__} saved.")
        if self.model is Product:
            emit(
                Events.PRODUCT_UPDATED, project=self.active_project,
                payload={"id": self.object.pk, "slug": self.object.slug,
                         "title": self.object.title, "status": self.object.status,
                         "price": str(self.object.price)},
                instance=self.object,
            )
        return response


# --- Categories ------------------------------------------------------

class CategoryListView(_ScopedQuerysetMixin, ListView):
    model = Category
    template_name = "control/catalog/category_list.jinja"
    context_object_name = "categories"


class CategoryCreateView(_ScopedFormMixin, CreateView):
    model = Category
    form_class = CategoryForm
    template_name = "control/catalog/category_form.jinja"
    success_url = reverse_lazy("control:category_list")


class CategoryUpdateView(_ScopedFormMixin, UpdateView):
    model = Category
    form_class = CategoryForm
    template_name = "control/catalog/category_form.jinja"
    success_url = reverse_lazy("control:category_list")


class CategoryDeleteView(_ScopedQuerysetMixin, DeleteView):
    model = Category
    template_name = "control/catalog/confirm_delete.jinja"
    success_url = reverse_lazy("control:category_list")

    def form_valid(self, form):
        record_audit(
            actor=self.request.user, project=self.active_project,
            action=AuditLog.Action.DELETE, target=self.get_object(), request=self.request,
        )
        return super().form_valid(form)


# --- Brands ----------------------------------------------------------

class BrandListView(_ScopedQuerysetMixin, ListView):
    model = Brand
    template_name = "control/catalog/brand_list.jinja"
    context_object_name = "brands"


class BrandCreateView(_ScopedFormMixin, CreateView):
    model = Brand
    form_class = BrandForm
    template_name = "control/catalog/brand_form.jinja"
    success_url = reverse_lazy("control:brand_list")


class BrandUpdateView(_ScopedFormMixin, UpdateView):
    model = Brand
    form_class = BrandForm
    template_name = "control/catalog/brand_form.jinja"
    success_url = reverse_lazy("control:brand_list")


class BrandDeleteView(_ScopedQuerysetMixin, DeleteView):
    model = Brand
    template_name = "control/catalog/confirm_delete.jinja"
    success_url = reverse_lazy("control:brand_list")

    def form_valid(self, form):
        record_audit(
            actor=self.request.user, project=self.active_project,
            action=AuditLog.Action.DELETE, target=self.get_object(), request=self.request,
        )
        return super().form_valid(form)


# --- Product types & Tags -----------------------------------------

class _TaxonomyDelete(_ScopedQuerysetMixin, DeleteView):
    template_name = "control/catalog/confirm_delete.jinja"

    def form_valid(self, form):
        record_audit(actor=self.request.user, project=self.active_project,
                     action=AuditLog.Action.DELETE, target=self.get_object(), request=self.request)
        return super().form_valid(form)


class ProductTypeListView(_ScopedQuerysetMixin, ListView):
    model = ProductType
    template_name = "control/catalog/producttype_list.jinja"
    context_object_name = "types"


class _ProductTypeForm(_ScopedFormMixin):
    model = ProductType
    form_class = ProductTypeForm
    template_name = "control/_object_form.jinja"
    success_url = reverse_lazy("control:product_type_list")


class ProductTypeCreateView(_ProductTypeForm, CreateView):
    pass


class ProductTypeUpdateView(_ProductTypeForm, UpdateView):
    pass


class ProductTypeDeleteView(_TaxonomyDelete):
    model = ProductType
    success_url = reverse_lazy("control:product_type_list")


class TagListView(_ScopedQuerysetMixin, ListView):
    model = Tag
    template_name = "control/catalog/tag_list.jinja"
    context_object_name = "tags"


class _TagForm(_ScopedFormMixin):
    model = Tag
    form_class = TagForm
    template_name = "control/_object_form.jinja"
    success_url = reverse_lazy("control:tag_list")


class TagCreateView(_TagForm, CreateView):
    pass


class TagUpdateView(_TagForm, UpdateView):
    pass


class TagDeleteView(_TaxonomyDelete):
    model = Tag
    success_url = reverse_lazy("control:tag_list")


# --- Products -------------------------------------------------------

class ProductListView(_ScopedQuerysetMixin, ListView):
    model = Product
    template_name = "control/catalog/product_list.jinja"
    context_object_name = "products"
    paginate_by = 25

    def get_queryset(self):
        qs = super().get_queryset().select_related("brand", "category")
        if self._show_trash():
            qs = qs.filter(trashed_at__isnull=False).order_by("-trashed_at")
        else:
            qs = qs.filter(trashed_at__isnull=True)
        q = self.request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(title__icontains=q)
        status = self.request.GET.get("status", "").strip()
        if status and not self._show_trash():
            qs = qs.filter(status=status)
        return qs

    def _show_trash(self):
        return (self.request.GET.get("trash", "") or "").strip() in ("1", "true", "yes")

    def get_template_names(self):
        if self.request.headers.get("HX-Request"):
            return ["control/catalog/_product_rows.jinja"]
        return [self.template_name]

    def get_context_data(self, **kwargs):
        from apps.accounts.permissions import OWNER_MANAGER, has_store_role

        ctx = super().get_context_data(**kwargs)
        ctx["q"] = self.request.GET.get("q", "")
        ctx["status"] = self.request.GET.get("status", "")
        ctx["show_trash"] = self._show_trash()
        ctx["trash_count"] = Product.objects.filter(
            project=self.active_project, trashed_at__isnull=False
        ).count()
        ctx["can_manage_trash"] = has_store_role(
            self.request.user, self.active_project, OWNER_MANAGER
        )
        ctx["trash_retention_days"] = TRASH_RETENTION_DAYS
        return ctx


class _ProductSizeColorMixin:
    """Apparel stores (see ``apps.projects.verticals``) get the Size & Colour
    quick builder on the product form. On save the two comma lists + per-combo
    price/stock table are reconciled into ``Variant`` rows."""

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from apps.projects.verticals import wants_size_color

        enabled = wants_size_color(self.active_project)
        ctx["size_color_enabled"] = enabled
        obj = ctx.get("object")
        if enabled and obj is not None:
            from apps.catalog.variants import size_color_of

            sizes, colors, rows = size_color_of(obj)
            ctx["sc_sizes"] = ", ".join(sizes)
            ctx["sc_colors"] = ", ".join(colors)
            ctx["sc_rows"] = rows
        else:
            ctx["sc_sizes"] = ctx["sc_colors"] = ""
            ctx["sc_rows"] = {}
        return ctx

    def form_valid(self, form):
        response = super().form_valid(form)
        from apps.projects.verticals import wants_size_color

        if wants_size_color(self.active_project):
            from apps.catalog.variants import (
                apply_size_color,
                matrix_from_post,
                parse_list,
            )

            post = self.request.POST
            apply_size_color(
                self.object,
                sizes=parse_list(post.get("sizes")),
                colors=parse_list(post.get("colors")),
                matrix=matrix_from_post(post),
            )
        return response


class ProductImportSampleView(ActiveProjectMixin, View):
    """Download a ready-to-fill sample CSV."""

    def get(self, request, *args, **kwargs):
        resp = HttpResponse(product_importer.sample_csv(), content_type="text/csv")
        resp["Content-Disposition"] = 'attachment; filename="product-import-sample.csv"'
        return resp


class ProductImportView(ActiveProjectMixin, View):
    template_name = "control/catalog/product_import.jinja"

    def get(self, request, *args, **kwargs):
        return render(request, self.template_name, {
            "form": ProductImportForm(),
            "active_project": self.active_project,
            "headers": product_importer.TEMPLATE_HEADERS,
        })

    def post(self, request, *args, **kwargs):
        from apps.billing import limits

        form = ProductImportForm(request.POST, request.FILES)
        ctx = {
            "form": form, "active_project": self.active_project,
            "headers": product_importer.TEMPLATE_HEADERS,
        }
        if not form.is_valid():
            return render(request, self.template_name, ctx)

        upload = form.cleaned_data["file"]
        try:
            rows = product_importer.read_table(upload, upload.name)
            result = product_importer.run_import(
                self.active_project, rows, actor=request.user,
                default_status=form.cleaned_data["default_status"],
            )
        except product_importer.ProductImportError as exc:
            messages.error(request, str(exc))
            return render(request, self.template_name, ctx)

        try:
            limits.check_can_add_product(self.active_project)
        except Exception:  # noqa: BLE001 — over the plan cap: report, don't 500
            messages.warning(
                request,
                "You are at or over your plan's product limit — some imported "
                "products may be hidden until you upgrade.",
            )

        record_audit(
            actor=request.user, project=self.active_project,
            action=AuditLog.Action.CREATE, target=self.active_project,
            changes={"product_import": {
                "created": result.created, "updated": result.updated,
                "errors": len(result.errors),
            }}, request=request,
        )
        if result.ok_rows:
            emit(Events.PRODUCT_UPDATED, project=self.active_project,
                 payload={"bulk": True, "count": result.ok_rows})

        parts = []
        if result.created:
            parts.append(f"{result.created} created")
        if result.updated:
            parts.append(f"{result.updated} updated")
        if result.images_attached:
            parts.append(f"{result.images_attached} images attached")
        if parts:
            messages.success(request, "Import done — " + ", ".join(parts) + ".")
        if result.missing_images:
            messages.warning(
                request,
                "Not found in Media, skipped: "
                + ", ".join(sorted(result.missing_images)[:12])
                + ". Upload them under Catalog → Media, then re-run.",
            )
        if result.errors:
            ctx["errors"] = result.errors
            ctx["result"] = result
            return render(request, self.template_name, ctx)
        if not parts:
            messages.info(request, "Nothing to import — the file had no usable rows.")
        return redirect("control:product_list")


class ProductCreateView(_ProductSizeColorMixin, _ScopedFormMixin, CreateView):
    model = Product
    form_class = ProductForm
    template_name = "control/catalog/product_form.jinja"

    def post(self, request, *args, **kwargs):
        from apps.billing import limits
        limits.check_can_add_product(self.active_project)
        return super().post(request, *args, **kwargs)

    def get_success_url(self):
        return reverse_lazy("control:product_edit", kwargs={"pk": self.object.pk})


class ProductUpdateView(_ProductSizeColorMixin, _ScopedFormMixin, UpdateView):
    model = Product
    form_class = ProductForm
    template_name = "control/catalog/product_form.jinja"

    def get_success_url(self):
        return reverse_lazy("control:product_edit", kwargs={"pk": self.object.pk})


class _ProductImageBase(ActiveProjectMixin, View):
    def get_product(self):
        product = get_object_or_404(Product, pk=self.kwargs["pk"])
        if product.project_id != self.active_project.pk:
            raise Http404
        return product

    def _render_panel(self, product):
        return render(self.request, "control/catalog/_product_images.jinja",
                      {"object": product, "active_project": self.active_project})


class ProductImagePanelView(_ProductImageBase):
    """GET the images panel — used by the uploader to refresh after uploads and
    to poll optimisation status."""

    def get(self, request, *args, **kwargs):
        return self._render_panel(self.get_product())


class ProductImageUploadView(_ProductImageBase):
    def post(self, request, *args, **kwargs):
        product = self.get_product()
        files = request.FILES.getlist("images")
        start = product.images.order_by("-order").values_list("order", flat=True).first() or 0
        has_primary = product.images.filter(is_primary=True).exists()
        for i, f in enumerate(files):
            ProductImage.objects.create(
                product=product, image=f, alt=request.POST.get("alt", "").strip(),
                order=start + i + 1, is_primary=(not has_primary and i == 0),
            )
        if files:
            record_audit(actor=request.user, project=self.active_project,
                         action=AuditLog.Action.UPDATE, target=product,
                         changes={"images_added": len(files)}, request=request)

        # AJAX uploader (one request per file): reply small JSON, it refreshes
        # the panel itself once every file is in.
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            if not files:
                return JsonResponse({"ok": False, "error": "No file received."}, status=400)
            return JsonResponse({"ok": True, "added": len(files)})

        if files:
            messages.success(request, f"Added {len(files)} image(s).")
        else:
            messages.error(request, "Choose at least one image file.")
        return self._render_panel(product)


class ProductImageDeleteView(_ProductImageBase):
    def post(self, request, *args, **kwargs):
        product = self.get_product()
        img = ProductImage.objects.filter(pk=kwargs["image_pk"], product=product).first()
        if img is not None:
            was_primary = img.is_primary
            img.image.delete(save=False)
            img.delete()
            if was_primary:
                nxt = product.images.order_by("order", "id").first()
                if nxt is not None:
                    nxt.is_primary = True
                    nxt.save(update_fields=["is_primary"])
            messages.success(request, "Image removed.")
        return self._render_panel(product)


class ProductImagePrimaryView(_ProductImageBase):
    def post(self, request, *args, **kwargs):
        product = self.get_product()
        img = ProductImage.objects.filter(pk=kwargs["image_pk"], product=product).first()
        if img is not None:
            product.images.exclude(pk=img.pk).update(is_primary=False)
            img.is_primary = True
            img.save(update_fields=["is_primary"])
        return self._render_panel(product)


class ProductImageMoveView(_ProductImageBase):
    def post(self, request, *args, **kwargs):
        product = self.get_product()
        direction = kwargs["dir"]
        ordered = list(product.images.order_by("order", "id"))
        idx = next((i for i, im in enumerate(ordered) if im.pk == int(kwargs["image_pk"])), None)
        if idx is not None:
            swap = idx - 1 if direction == "up" else idx + 1
            if 0 <= swap < len(ordered):
                a, b = ordered[idx], ordered[swap]
                a.order, b.order = b.order, a.order
                ProductImage.objects.bulk_update([a, b], ["order"])
        return self._render_panel(product)


class ProductDuplicateView(_ScopedQuerysetMixin, View):
    """Clone a product (+ its tags, attribute values, variants and images) as a
    draft and open the copy for editing."""

    model = Product

    def post(self, request, *args, **kwargs):
        from apps.billing import limits
        from apps.catalog.models import ProductStatus, Variant

        src = get_object_or_404(self.get_queryset(), pk=self.kwargs["pk"])
        try:
            limits.check_can_add_product(self.active_project)
        except PermissionDenied as exc:
            messages.error(request, str(exc))
            return redirect("control:product_edit", pk=src.pk)

        clone = Product.objects.get(pk=src.pk)
        clone.pk = None
        clone._state.adding = True
        clone.slug = ""
        clone.sku = ""
        clone.title = f"{src.title} (copy)"
        clone.status = ProductStatus.DRAFT
        clone.search_indexed = False
        clone.rating_avg = 0
        clone.rating_count = 0
        clone.save()

        clone.tags.set(src.tags.all())
        clone.attribute_values.set(src.attribute_values.all())

        for v in src.variants.all():
            new_v = Variant.objects.get(pk=v.pk)
            new_v.pk = None
            new_v._state.adding = True
            new_v.sku = ""
            new_v.product = clone
            new_v.save()
            new_v.attribute_values.set(v.attribute_values.all())

        for img in src.images.all():
            if not img.image:
                continue
            try:
                img.image.open("rb")
                data = img.image.read()
            finally:
                img.image.close()
            new_img = ProductImage(
                product=clone, alt=img.alt, order=img.order, is_primary=img.is_primary,
            )
            new_img.image.save(os.path.basename(img.image.name), ContentFile(data), save=True)

        record_audit(
            actor=request.user, project=self.active_project,
            action=AuditLog.Action.CREATE, target=clone,
            changes={"duplicated_from": src.pk}, request=request,
        )
        messages.success(request, "Product duplicated — you're now editing the copy.")
        return redirect("control:product_edit", pk=clone.pk)


class _ProductTrashBase(_ScopedQuerysetMixin):
    """Owner / manager only — trashing is reversible, purging is not."""

    def check_active_project_access(self, request):
        parent = super().check_active_project_access(request)
        if parent is not None:
            return parent
        from apps.accounts.permissions import OWNER_MANAGER, assert_store_role

        assert_store_role(request.user, self.active_project, OWNER_MANAGER,
                          "Only the store owner or a manager can do this.")
        return None

    def _product(self):
        return get_object_or_404(
            Product.objects.filter(project=self.active_project), pk=self.kwargs["pk"]
        )


class ProductDeleteView(_ProductTrashBase, View):
    """"Delete" = move to Trash (kept for 30 days, then purged)."""

    def get(self, request, *args, **kwargs):
        return render(request, "control/catalog/confirm_delete.jinja",
                      {"object": self._product(), "active_project": self.active_project,
                       "trash_days": TRASH_RETENTION_DAYS})

    def post(self, request, *args, **kwargs):
        product = self._product()
        trash_product(product)
        record_audit(actor=request.user, project=self.active_project,
                     action=AuditLog.Action.UPDATE, target=product,
                     changes={"trashed": True}, request=request)
        messages.success(request, f"“{product.title}” moved to Trash.")
        return redirect("control:product_list")


class ProductRestoreView(_ProductTrashBase, View):
    def post(self, request, *args, **kwargs):
        product = self._product()
        restore_product(product)
        messages.success(request, f"“{product.title}” restored.")
        return redirect(f"{reverse_lazy('control:product_list')}?trash=1")


class ProductPurgeView(_ProductTrashBase, View):
    def post(self, request, *args, **kwargs):
        product = self._product()
        title = product.title
        if purge_product(product):
            record_audit(actor=request.user, project=self.active_project,
                         action=AuditLog.Action.DELETE, target=None,
                         changes={"purged_product": title}, request=request)
            messages.success(request, f"“{title}” permanently deleted.")
        else:
            messages.error(request, f"“{title}” is still in a shopper's cart — can't delete it yet.")
        return redirect(f"{reverse_lazy('control:product_list')}?trash=1")
