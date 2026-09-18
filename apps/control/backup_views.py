"""Store-owner backup / restore — the owner's own copy of their storefront
content. Same engine as the platform-side store backup (apps.control.store_backup),
just scoped to the active project and gated to the OWNER role."""

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import redirect
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView

from apps.accounts.permissions import (
    StoreRole,
    StoreRoleRequiredMixin,
    is_platform_admin,
    store_role,
)

from . import store_backup
from .mixins import ActiveProjectMixin

_MAX_UPLOAD = 700 * 1024 * 1024


class _OwnerOnly(StoreRoleRequiredMixin, ActiveProjectMixin):
    """Store owner, or the store's DGC (Platform Manager credited on the
    subscription) even with no team membership -- backup/restore is store
    *setup*, not orders/customers/money, so it's explicitly one of the
    things apps.accounts.permissions.dgc_without_membership's own docstring
    says such a DGC should be able to do. StoreRoleRequiredMixin already
    grants this correctly via has_store_role()'s platform-staff/subscription
    -manager special case -- no StoreDataAccessMixin here, since that mixin
    is for the orders/customers/payments boundary this view doesn't touch.

    That said, orders/customers/payments ARE what dump_store/restore_store's
    own ``include_sensitive`` flag adds on top of catalog/CMS/theme -- and
    that half is only for the store's real owner (a genuine StoreRole.OWNER
    membership row), or a platform admin, never a DGC reaching this same
    screen via the subscription-manager bypass above. _include_sensitive()
    is how each view below tells those two apart."""

    required_store_roles = frozenset({StoreRole.OWNER})
    role_denied_message = "Only the store owner can back up or restore this store."

    def _include_sensitive(self):
        user = self.request.user
        return is_platform_admin(user) or store_role(user, self.active_project) == StoreRole.OWNER


class OwnerBackupView(_OwnerOnly, TemplateView):
    template_name = "control/backup.jinja"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["include_sensitive"] = self._include_sensitive()
        return ctx


class OwnerBackupDownloadView(_OwnerOnly, View):
    def get(self, request, *args, **kwargs):
        store = self.active_project
        try:
            blob = store_backup.dump_store(store, include_sensitive=self._include_sensitive())
        except store_backup.BackupError as exc:
            messages.error(request, str(exc))
            return redirect("control:owner_backup")
        slug = store.slug or f"store-{store.pk}"
        resp = HttpResponse(blob, content_type="application/zip")
        resp["Content-Disposition"] = (
            f'attachment; filename="{slug}-backup-{timezone.now():%Y%m%d}.zip"'
        )
        return resp


class OwnerRestoreView(_OwnerOnly, View):
    def post(self, request, *args, **kwargs):
        store = self.active_project
        if (request.POST.get("confirm_name", "") or "").strip() != store.name:
            messages.error(request, "Type your store's exact name to confirm.")
            return redirect("control:owner_backup")
        upload = request.FILES.get("backup")
        if upload is None:
            messages.error(request, "Choose a backup .zip file.")
            return redirect("control:owner_backup")
        if upload.size and upload.size > _MAX_UPLOAD:
            messages.error(request, "That file is too large.")
            return redirect("control:owner_backup")
        try:
            counts = store_backup.restore_store(
                store, upload.read(), actor=request.user,
                include_sensitive=self._include_sensitive(),
            )
        except store_backup.BackupError as exc:
            messages.error(request, str(exc))
            return redirect("control:owner_backup")
        except Exception as exc:  # noqa: BLE001
            import logging

            logging.getLogger(__name__).exception("owner restore failed")
            messages.error(request, f"Restore failed: {exc}")
            return redirect("control:owner_backup")
        detail = f"({counts.get('product', 0)} products, {counts.get('category', 0)} categories"
        if "order" in counts:
            detail += f", {counts.get('order', 0)} orders, {counts.get('customer', 0)} customers"
        detail += ")"
        messages.success(request, f"Restored — {sum(counts.values())} items {detail}.")
        return redirect("control:owner_backup")
