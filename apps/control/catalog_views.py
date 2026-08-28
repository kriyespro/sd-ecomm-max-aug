"""Control-panel CRUD for catalog + categories, scoped to the active project.

Thin views: querysets filtered by ``self.active_project``; forms receive it as a
kwarg; mutations recorded to the audit log.
"""

from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import (
    CreateView,
    DeleteView,
    ListView,
    TemplateView,
    UpdateView,
)

from apps.catalog.models import Brand, Product, ProductImage, ProductType, Tag
from apps.categories.models import Category
from apps.core.events import Events, emit
from apps.core.models import AuditLog
from apps.core.services import record_audit

from .forms import BrandForm, CategoryForm, ProductForm, ProductTypeForm, TagForm
from .mixins import ActiveProjectMixin


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
        q = self.request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(title__icontains=q)
        status = self.request.GET.get("status", "").strip()
        if status:
            qs = qs.filter(status=status)
        return qs

    def get_template_names(self):
        if self.request.headers.get("HX-Request"):
            return ["control/catalog/_product_rows.jinja"]
        return [self.template_name]

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["q"] = self.request.GET.get("q", "")
        ctx["status"] = self.request.GET.get("status", "")
        return ctx


class ProductCreateView(_ScopedFormMixin, CreateView):
    model = Product
    form_class = ProductForm
    template_name = "control/catalog/product_form.jinja"

    def post(self, request, *args, **kwargs):
        from apps.billing import limits
        limits.check_can_add_product(self.active_project)
        return super().post(request, *args, **kwargs)

    def get_success_url(self):
        return reverse_lazy("control:product_edit", kwargs={"pk": self.object.pk})


class ProductUpdateView(_ScopedFormMixin, UpdateView):
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


class ProductDeleteView(_ScopedQuerysetMixin, DeleteView):
    model = Product
    template_name = "control/catalog/confirm_delete.jinja"
    success_url = reverse_lazy("control:product_list")

    def form_valid(self, form):
        record_audit(
            actor=self.request.user, project=self.active_project,
            action=AuditLog.Action.DELETE, target=self.get_object(), request=self.request,
        )
        return super().form_valid(form)
