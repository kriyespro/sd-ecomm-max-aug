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
    UGCVideo,
)
from apps.categories.models import Category
from apps.core.models import AuditLog
from apps.core.services import record_audit

from .forms import (
    BannerForm,
    ContentBlockForm,
    FAQForm,
    MenuForm,
    MenuItemEditForm,
    PageForm,
    StoreProfileForm,
    ThemeSettingsForm,
    UGCVideoForm,
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


# --- UGC / Shorts videos -------------------------------------------

class UGCVideoListView(_ScopedList):
    model = UGCVideo
    template_name = "control/cms/ugc_video_list.jinja"
    context_object_name = "videos"


class _UGCVideoForm(_ScopedForm):
    model = UGCVideo
    form_class = UGCVideoForm
    template_name = "control/_object_form.jinja"
    success_url = reverse_lazy("control:cms_ugc_videos")

    def form_valid(self, form):
        if not form.instance.pk:
            form.instance.added_by = self.request.user
        return super().form_valid(form)


class UGCVideoCreateView(_UGCVideoForm, CreateView):
    pass


class UGCVideoUpdateView(_UGCVideoForm, UpdateView):
    pass


class UGCVideoDeleteView(_ScopedDelete):
    model = UGCVideo
    success_url = reverse_lazy("control:cms_ugc_videos")


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

    def get_queryset(self):
        from django.db.models import Count

        return super().get_queryset().annotate(item_count=Count("items"))


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


class _MenuScopedView(ActiveProjectMixin, View):
    def _menu(self):
        menu = get_object_or_404(Menu, pk=self.kwargs["pk"])
        if menu.project_id != self.active_project.pk:
            raise Http404
        return menu

    def _item(self, menu):
        return get_object_or_404(MenuItem, pk=self.kwargs["item_pk"], menu=menu)

    def _next_order(self, menu, parent_id):
        last = (
            menu.items.filter(parent_id=parent_id)
            .order_by("-order", "-id").values_list("order", flat=True).first()
        )
        return (last or 0) + 1


def _renumber(menu, parent_id):
    """Rewrite ``order`` to 1..n for one sibling group so up/down stays sane."""
    sibs = list(menu.items.filter(parent_id=parent_id).order_by("order", "id"))
    for i, s in enumerate(sibs, start=1):
        if s.order != i:
            MenuItem.objects.filter(pk=s.pk).update(order=i)


class MenuDetailView(ActiveProjectMixin, TemplateView):
    template_name = "control/cms/menu_detail.jinja"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        menu = get_object_or_404(Menu, pk=self.kwargs["pk"], project=self.active_project)

        items = list(
            menu.items.select_related("page", "category").order_by("order", "id")
        )
        by_parent = {}
        for it in items:
            by_parent.setdefault(it.parent_id, []).append(it)
        for it in items:
            it.child_items = by_parent.get(it.id, [])

        used_pages = {it.page_id for it in items if it.page_id}
        used_cats = {it.category_id for it in items if it.category_id}

        ctx["menu"] = menu
        ctx["tree"] = by_parent.get(None, [])
        ctx["top_items"] = by_parent.get(None, [])
        ctx["pages"] = [
            p for p in Page.objects.filter(project=self.active_project)
            .only("title", "slug", "status", "published_at", "kind")
            if p.is_live and p.id not in used_pages
        ]
        ctx["categories"] = [
            c for c in Category.objects.filter(project=self.active_project, is_active=True)
            if c.id not in used_cats
        ]
        ctx["edit_form"] = MenuItemEditForm()
        return ctx


class MenuQuickAddView(_MenuScopedView):
    """Add one or more items in a single click — pages, categories or a link."""

    def post(self, request, *args, **kwargs):
        menu = self._menu()
        kind = request.POST.get("kind", "")
        parent_id = request.POST.get("parent") or None
        if parent_id:
            parent = MenuItem.objects.filter(
                pk=parent_id, menu=menu, parent__isnull=True
            ).first()
            parent_id = parent.pk if parent else None

        added = 0
        order = self._next_order(menu, parent_id)

        if kind == "pages":
            pages = Page.objects.filter(
                project=self.active_project, pk__in=request.POST.getlist("page_ids"),
            )
            for p in pages:
                MenuItem.objects.create(
                    menu=menu, parent_id=parent_id, label=p.title,
                    link_type="page", page=p, order=order,
                )
                order += 1
                added += 1
        elif kind == "categories":
            cats = Category.objects.filter(
                project=self.active_project, pk__in=request.POST.getlist("category_ids"),
            )
            for c in cats:
                MenuItem.objects.create(
                    menu=menu, parent_id=parent_id, label=c.name,
                    link_type="category", category=c, order=order,
                )
                order += 1
                added += 1
        elif kind == "link":
            label = (request.POST.get("label") or "").strip()
            url = (request.POST.get("url") or "").strip()
            if label and url:
                MenuItem.objects.create(
                    menu=menu, parent_id=parent_id, label=label,
                    link_type="external" if url.startswith("http") else "url",
                    url=url, open_in_new_tab=bool(request.POST.get("open_in_new_tab")),
                    order=order,
                )
                added = 1
            else:
                messages.error(request, "A link needs both a label and a URL.")

        if added:
            record_audit(actor=request.user, project=self.active_project,
                         action=AuditLog.Action.UPDATE, target=menu, request=request)
            messages.success(request, f"Added {added} item(s).")
        return redirect("control:cms_menu_detail", pk=menu.pk)


class MenuItemUpdateView(_MenuScopedView):
    def post(self, request, *args, **kwargs):
        menu = self._menu()
        item = self._item(menu)
        form = MenuItemEditForm(request.POST, instance=item)
        if form.is_valid():
            form.save()
            messages.success(request, "Saved.")
        else:
            messages.error(request, "; ".join(f"{k}: {v[0]}" for k, v in form.errors.items()))
        return redirect("control:cms_menu_detail", pk=menu.pk)


class MenuItemMoveView(_MenuScopedView):
    """Reorder / (un)nest an item — this is how a merchant builds a dropdown."""

    def post(self, request, *args, **kwargs):
        menu = self._menu()
        item = self._item(menu)
        action = request.POST.get("action", "")

        sibs = list(
            menu.items.filter(parent_id=item.parent_id).order_by("order", "id")
        )
        idx = next((i for i, s in enumerate(sibs) if s.pk == item.pk), 0)

        if action in ("up", "down"):
            swap_with = sibs[idx - 1] if action == "up" and idx > 0 else (
                sibs[idx + 1] if action == "down" and idx < len(sibs) - 1 else None
            )
            if swap_with is not None:
                item.order, swap_with.order = swap_with.order, item.order
                MenuItem.objects.bulk_update([item, swap_with], ["order"])
        elif action == "indent":
            # Nest under the sibling directly above — one level only.
            if item.parent_id is None and idx > 0:
                new_parent = sibs[idx - 1]
                item.parent = new_parent
                item.order = self._next_order(menu, new_parent.pk)
                # its own children can't go two deep — lift them to top level
                item.children.update(parent=None)
                item.save(update_fields=["parent", "order", "updated_at"])
                _renumber(menu, None)
        elif action == "outdent":
            if item.parent_id is not None:
                grandparent_id = item.parent.parent_id  # always None (1 level)
                old_parent_order = item.parent.order
                item.parent_id = grandparent_id
                item.save(update_fields=["parent", "updated_at"])
                _renumber(menu, grandparent_id)
                # drop it just after its old parent
                MenuItem.objects.filter(pk=item.pk).update(order=old_parent_order)
                _renumber(menu, grandparent_id)

        record_audit(actor=request.user, project=self.active_project,
                     action=AuditLog.Action.UPDATE, target=menu, request=request)
        return redirect("control:cms_menu_detail", pk=menu.pk)


class MenuItemDeleteView(_MenuScopedView):
    def post(self, request, *args, **kwargs):
        menu = self._menu()
        MenuItem.objects.filter(pk=kwargs["item_pk"], menu=menu).delete()
        _renumber(menu, None)
        messages.success(request, "Item removed.")
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


class DemoContentRemoveView(ActiveProjectMixin, View):
    """One-click wipe of the auto-seeded demo catalogue / banners / pages.

    Deletes only the rows the seeder recorded on ``feature_flags``; anything the
    owner has added stays. POST-only, from the "showing demo content" banner.
    """

    def post(self, request, *args, **kwargs):
        from apps.control.starter_content import is_seeded, remove_starter_content

        if not is_seeded(self.active_project):
            messages.info(request, "No demo content to remove.")
        else:
            remove_starter_content(self.active_project)
            record_audit(actor=request.user, project=self.active_project,
                         action=AuditLog.Action.DELETE, target=self.active_project,
                         changes={"demo_content": "removed"}, request=request)
            messages.success(request, "Demo content removed.")
        return redirect(request.POST.get("next") or "control:product_list")


class DemoContentImportView(ActiveProjectMixin, View):
    """Wipe this store's catalogue + CMS content and replace it with a fresh
    demo set. Destructive — gated behind a typed "DELETE" confirmation, for
    existing stores that want to start from the template.
    """

    def post(self, request, *args, **kwargs):
        from apps.control.starter_content import reset_and_seed

        if (request.POST.get("confirm") or "").strip() != "DELETE":
            messages.error(request, 'Type DELETE to confirm — nothing was changed.')
            return redirect(request.POST.get("next") or "control:cms_store_profile")

        counts = reset_and_seed(self.active_project)
        record_audit(actor=request.user, project=self.active_project,
                     action=AuditLog.Action.DELETE, target=self.active_project,
                     changes={"demo_import": "wiped + reseeded", "removed": counts},
                     request=request)
        messages.success(
            request,
            "Store reset and demo content imported. Edit the samples, then "
            "swap in your own images.",
        )
        return redirect("control:product_list")
