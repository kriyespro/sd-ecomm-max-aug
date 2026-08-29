"""Control-panel CMS: pages, banners, FAQs, content blocks, menus, theme.

Scoped to the active project; mutations audited. Menus get a small nested-item
editor; everything else is straight CRUD.
"""

from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views.generic import (
    CreateView,
    DeleteView,
    ListView,
    TemplateView,
    UpdateView,
    View,
)

from apps.cms.models import (
    FAQ,
    Banner,
    ContentBlock,
    Menu,
    MenuItem,
    Page,
    StoreProfile,
    ThemeSettings,
)
from apps.core.models import AuditLog
from apps.core.services import record_audit

from .forms import (
    BannerForm,
    ContentBlockForm,
    FAQForm,
    MenuForm,
    MenuItemForm,
    PageForm,
    StoreProfileForm,
    ThemeSettingsForm,
)
from .mixins import ActiveProjectMixin


class _ScopedList(ActiveProjectMixin, ListView):
    model = None

    def get_queryset(self):
        return self.model.objects.filter(project=self.active_project)


class _ScopedForm(ActiveProjectMixin):
    model = None

    def get_queryset(self):
        return self.model.objects.filter(project=self.active_project)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["project"] = self.active_project
        return kwargs

    def form_valid(self, form):
        response = super().form_valid(form)
        record_audit(
            actor=self.request.user, project=self.active_project,
            action=AuditLog.Action.CREATE if isinstance(self, CreateView) else AuditLog.Action.UPDATE,
            target=self.object, request=self.request,
        )
        messages.success(self.request, f"{self.model._meta.verbose_name.title()} saved.")
        return response


class _ScopedDelete(ActiveProjectMixin, DeleteView):
    model = None
    template_name = "control/catalog/confirm_delete.jinja"

    def get_queryset(self):
        return self.model.objects.filter(project=self.active_project)


# --- Pages ---------------------------------------------------------

class PageListView(_ScopedList):
    model = Page
    template_name = "control/cms/page_list.jinja"
    context_object_name = "pages"


class _PageForm(_ScopedForm):
    model = Page
    form_class = PageForm
    template_name = "control/_object_form.jinja"
    success_url = reverse_lazy("control:cms_pages")


class PageCreateView(_PageForm, CreateView):
    pass


class PageUpdateView(_PageForm, UpdateView):
    pass


class PageDeleteView(_ScopedDelete):
    model = Page
    success_url = reverse_lazy("control:cms_pages")


# --- Banners ------------------------------------------------------

class BannerListView(_ScopedList):
    model = Banner
    template_name = "control/cms/banner_list.jinja"
    context_object_name = "banners"


class _BannerForm(_ScopedForm):
    model = Banner
    form_class = BannerForm
    template_name = "control/_object_form.jinja"
    success_url = reverse_lazy("control:cms_banners")


class BannerCreateView(_BannerForm, CreateView):
    pass


class BannerUpdateView(_BannerForm, UpdateView):
    pass


class BannerDeleteView(_ScopedDelete):
    model = Banner
    success_url = reverse_lazy("control:cms_banners")


# --- FAQs --------------------------------------------------------

class FAQListView(_ScopedList):
    model = FAQ
    template_name = "control/cms/faq_list.jinja"
    context_object_name = "faqs"


class _FAQForm(_ScopedForm):
    model = FAQ
    form_class = FAQForm
    template_name = "control/_object_form.jinja"
    success_url = reverse_lazy("control:cms_faqs")


class FAQCreateView(_FAQForm, CreateView):
    pass


class FAQUpdateView(_FAQForm, UpdateView):
    pass


class FAQDeleteView(_ScopedDelete):
    model = FAQ
    success_url = reverse_lazy("control:cms_faqs")


# --- Content blocks --------------------------------------------

class ContentBlockListView(_ScopedList):
    model = ContentBlock
    template_name = "control/cms/block_list.jinja"
    context_object_name = "blocks"


class _BlockForm(_ScopedForm):
    model = ContentBlock
    form_class = ContentBlockForm
    template_name = "control/_object_form.jinja"
    success_url = reverse_lazy("control:cms_blocks")


class ContentBlockCreateView(_BlockForm, CreateView):
    pass


class ContentBlockUpdateView(_BlockForm, UpdateView):
    pass


class ContentBlockDeleteView(_ScopedDelete):
    model = ContentBlock
    success_url = reverse_lazy("control:cms_blocks")


# --- Menus + items -------------------------------------------

class MenuListView(_ScopedList):
    model = Menu
    template_name = "control/cms/menu_list.jinja"
    context_object_name = "menus"


class _MenuForm(_ScopedForm):
    model = Menu
    form_class = MenuForm
    template_name = "control/_object_form.jinja"
    success_url = reverse_lazy("control:cms_menus")


class MenuCreateView(_MenuForm, CreateView):
    pass


class MenuUpdateView(_MenuForm, UpdateView):
    pass


class MenuDeleteView(_ScopedDelete):
    model = Menu
    success_url = reverse_lazy("control:cms_menus")


class MenuDetailView(ActiveProjectMixin, TemplateView):
    template_name = "control/cms/menu_detail.jinja"

    def _menu(self):
        menu = get_object_or_404(Menu, pk=self.kwargs["pk"])
        if menu.project_id != self.active_project.pk:
            raise Http404
        return menu

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        menu = self._menu()
        ctx["menu"] = menu
        ctx["items"] = menu.items.select_related("page", "category").order_by("order", "id")
        ctx["form"] = MenuItemForm(menu=menu)
        return ctx


class MenuItemCreateView(ActiveProjectMixin, View):
    def post(self, request, *args, **kwargs):
        menu = get_object_or_404(Menu, pk=kwargs["pk"], project=self.active_project)
        form = MenuItemForm(request.POST, menu=menu)
        if form.is_valid():
            form.save()
            record_audit(actor=request.user, project=self.active_project,
                         action=AuditLog.Action.UPDATE, target=menu, request=request)
            messages.success(request, "Menu item added.")
        else:
            messages.error(request, "; ".join(f"{k}: {v[0]}" for k, v in form.errors.items()))
        return redirect("control:cms_menu_detail", pk=menu.pk)


class MenuItemUpdateView(ActiveProjectMixin, View):
    def post(self, request, *args, **kwargs):
        menu = get_object_or_404(Menu, pk=kwargs["pk"], project=self.active_project)
        item = get_object_or_404(MenuItem, pk=kwargs["item_pk"], menu=menu)
        form = MenuItemForm(request.POST, instance=item, menu=menu)
        if form.is_valid():
            form.save()
            messages.success(request, "Menu item updated.")
        else:
            messages.error(request, "; ".join(f"{k}: {v[0]}" for k, v in form.errors.items()))
        return redirect("control:cms_menu_detail", pk=menu.pk)


class MenuItemDeleteView(ActiveProjectMixin, View):
    def post(self, request, *args, **kwargs):
        menu = get_object_or_404(Menu, pk=kwargs["pk"], project=self.active_project)
        MenuItem.objects.filter(pk=kwargs["item_pk"], menu=menu).delete()
        messages.success(request, "Menu item removed.")
        return redirect("control:cms_menu_detail", pk=menu.pk)


# --- Theme -----------------------------------------------------

class ThemeSettingsView(ActiveProjectMixin, UpdateView):
    form_class = ThemeSettingsForm
    template_name = "control/cms/theme_form.jinja"
    success_url = reverse_lazy("control:cms_theme")

    def get_object(self, queryset=None):
        obj, _ = ThemeSettings.objects.get_or_create(project=self.active_project)
        return obj

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["project"] = self.active_project
        return kwargs

    def form_valid(self, form):
        response = super().form_valid(form)
        record_audit(actor=self.request.user, project=self.active_project,
                     action=AuditLog.Action.UPDATE, target=self.object, request=self.request)
        messages.success(self.request, "Theme saved.")
        return response


class StoreProfileView(ActiveProjectMixin, UpdateView):
    form_class = StoreProfileForm
    template_name = "control/cms/store_profile_form.jinja"
    success_url = reverse_lazy("control:cms_store_profile")

    def get_object(self, queryset=None):
        obj, _ = StoreProfile.objects.get_or_create(project=self.active_project)
        return obj

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["project"] = self.active_project
        return kwargs

    def form_valid(self, form):
        response = super().form_valid(form)
        record_audit(actor=self.request.user, project=self.active_project,
                     action=AuditLog.Action.UPDATE, target=self.object, request=self.request)
        messages.success(self.request, "Store profile saved.")
        return response
