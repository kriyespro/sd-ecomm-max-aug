"""Store-owner backup / restore — the owner's own copy of their storefront
content. Same engine as the platform-side store backup (apps.control.store_backup),
just scoped to the active project and gated to the OWNER role."""

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import redirect
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView

from apps.accounts.permissions import StoreRole, StoreRoleRequiredMixin

from . import store_backup
from .mixins import ActiveProjectMixin

_MAX_UPLOAD = 700 * 1024 * 1024


class _OwnerOnly(StoreRoleRequiredMixin, ActiveProjectMixin):
    required_store_roles = frozenset({StoreRole.OWNER})
    role_denied_message = "Only the store owner can back up or restore this store."


class OwnerBackupView(_OwnerOnly, TemplateView):
    template_name = "control/backup.jinja"


class OwnerBackupDownloadView(_OwnerOnly, View):
    def get(self, request, *args, **kwargs):
        store = self.active_project
        try:
            blob = store_backup.dump_store(store)
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
            counts = store_backup.restore_store(store, upload.read(), actor=request.user)
        except store_backup.BackupError as exc:
            messages.error(request, str(exc))
            return redirect("control:owner_backup")
        except Exception as exc:  # noqa: BLE001
            import logging

            logging.getLogger(__name__).exception("owner restore failed")
            messages.error(request, f"Restore failed: {exc}")
            return redirect("control:owner_backup")
        messages.success(
            request,
            f"Restored — {sum(counts.values())} items "
            f"({counts.get('product', 0)} products, {counts.get('category', 0)} categories).",
        )
        return redirect("control:owner_backup")
