"""Store-owner backup / restore — the owner's own copy of their storefront
content. Same engine as the platform-side store backup (apps.control.store_backup),
just scoped to the active project and gated to the OWNER role."""

from django.contrib import messages
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView

from apps.accounts.permissions import (
    StoreRole,
    StoreRoleRequiredMixin,
    is_platform_admin,
    store_role,
)
from apps.billing import limits as billing_limits

from . import store_backup
from .mixins import ActiveProjectMixin
from .models import RETENTION_DAYS, StoreBackupSnapshot

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
    membership row) on a Growth/Pro plan (apps.billing.limits
    .full_backup_allowed), or a platform admin (always, regardless of
    plan), never a DGC reaching this same screen via the subscription
    -manager bypass above. _include_sensitive() is how each view below
    tells all of that apart."""

    required_store_roles = frozenset({StoreRole.OWNER})
    role_denied_message = "Only the store owner can back up or restore this store."

    def _include_sensitive(self):
        user = self.request.user
        if is_platform_admin(user):
            return True
        if store_role(user, self.active_project) != StoreRole.OWNER:
            return False
        return billing_limits.full_backup_allowed(self.active_project)


class OwnerBackupView(_OwnerOnly, TemplateView):
    template_name = "control/backup.jinja"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        include_sensitive = self._include_sensitive()
        ctx["include_sensitive"] = include_sensitive
        ctx["retention_days"] = RETENTION_DAYS
        ctx["auto_backup_enabled"] = bool((self.active_project.feature_flags or {}).get("auto_backup"))
        # Automatic daily snapshots always carry orders/customers/payments
        # (apps.control.tasks.daily_store_backup_task) -- owner-only, same
        # rule as the manual download/restore above.
        ctx["restore_job"] = self.active_project.restore_jobs.first()
        if include_sensitive:
            ctx["auto_snapshots"] = self.active_project.backup_snapshots.all()[:RETENTION_DAYS]
        elif store_role(self.request.user, self.active_project) == StoreRole.OWNER:
            # A real owner blocked only by plan (not the DGC-without-
            # membership case) -- worth the upgrade nudge; a DGC isn't the
            # one who'd act on it (see billing.md's plan-screen-hidden-
            # from-DGC's-team precedent).
            ctx["show_backup_upgrade_nudge"] = True
        return ctx


class OwnerAutoBackupToggleView(_OwnerOnly, View):
    """The "Automatic backup" checkbox on /admin/backup/ -- off by default,
    apps.control.tasks.daily_store_backup_task only backs up a store once
    its owner opts in here."""

    def post(self, request, *args, **kwargs):
        store = self.active_project
        wants_on = request.POST.get("auto_backup") == "on"
        if wants_on and not self._include_sensitive():
            messages.error(
                request,
                "Automatic backup is a Growth/Pro feature — upgrade under Plan & billing to turn it on.",
            )
            return redirect("control:owner_backup")
        flags = store.feature_flags or {}
        flags["auto_backup"] = wants_on
        store.feature_flags = flags
        store.save(update_fields=["feature_flags", "updated_at"])
        messages.success(
            request,
            "Automatic daily backups turned on." if wants_on
            else "Automatic daily backups turned off.",
        )
        return redirect("control:owner_backup")


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


class OwnerRestoreChunkView(_OwnerOnly, View):
    """One slice of a chunked backup upload — see chunked_upload's module
    docstring for why the upload is sliced client-side at all (a single
    giant POST can take longer to transfer than Cloudflare's own edge
    timeout, regardless of how fast nginx/Django would respond once the
    body actually arrives)."""

    def post(self, request, *args, **kwargs):
        from . import chunked_upload

        blob = request.FILES.get("chunk")
        try:
            chunk_index = int(request.POST.get("chunk_index", ""))
        except ValueError:
            return JsonResponse({"ok": False, "error": "Bad chunk index."}, status=400)
        if blob is None:
            return JsonResponse({"ok": False, "error": "No chunk data."}, status=400)
        try:
            total = chunked_upload.append_chunk(
                request.POST.get("upload_id", ""), chunk_index, blob.read(), _MAX_UPLOAD,
            )
        except chunked_upload.ChunkUploadError as exc:
            return JsonResponse({"ok": False, "error": str(exc)}, status=400)
        return JsonResponse({"ok": True, "received": total})


class OwnerRestoreView(_OwnerOnly, View):
    """Finalizes a chunked upload (see OwnerRestoreChunkView above) and
    queues the actual restore. JSON in, JSON out — the page that posts the
    chunks navigates itself once this returns ok."""

    def post(self, request, *args, **kwargs):
        from . import chunked_upload

        store = self.active_project
        upload_id = request.POST.get("upload_id", "")
        if (request.POST.get("confirm_name", "") or "").strip() != store.name:
            chunked_upload.abort(upload_id)
            return JsonResponse({"ok": False, "error": "Type your store's exact name to confirm."}, status=400)

        try:
            archive = chunked_upload.finalize(upload_id, _MAX_UPLOAD)
        except chunked_upload.ChunkUploadError as exc:
            return JsonResponse({"ok": False, "error": str(exc)}, status=400)

        try:
            store_backup.precheck_archive(archive)
        except store_backup.BackupError as exc:
            return JsonResponse({"ok": False, "error": str(exc)}, status=400)

        from .tasks import queue_store_restore

        queue_store_restore(
            store, archive, actor=request.user,
            include_sensitive=self._include_sensitive(), source_label="owner upload",
        )
        messages.info(
            request,
            "Restoring your store in the background — this can take a few minutes "
            "for a large store. Refresh this page to check progress.",
        )
        return JsonResponse({"ok": True})


class _OwnerSnapshotBase(_OwnerOnly):
    """Automatic daily snapshots are owner-only data (see StoreBackupSnapshot's
    own docstring) -- a DGC reaching the plain backup screen via the
    subscription-manager bypass must never touch these, even by guessing a
    snapshot's pk for a store they can otherwise open."""

    def get_snapshot(self, pk):
        snap = get_object_or_404(StoreBackupSnapshot, pk=pk, project=self.active_project)
        # Safety snapshots (catalogue/CMS only) are open to any owner; the
        # full daily ones carry orders/customers/payments.
        if snap.includes_orders and not self._include_sensitive():
            raise Http404
        return snap


class OwnerBackupSnapshotDownloadView(_OwnerSnapshotBase, View):
    def get(self, request, *args, **kwargs):
        snap = self.get_snapshot(kwargs["pk"])
        resp = HttpResponse(snap.archive.read(), content_type="application/zip")
        resp["Content-Disposition"] = f'attachment; filename="{snap.archive.name.rsplit("/", 1)[-1]}"'
        return resp


class OwnerBackupSnapshotRestoreView(_OwnerSnapshotBase, View):
    def post(self, request, *args, **kwargs):
        snap = self.get_snapshot(kwargs["pk"])
        store = self.active_project

        from .tasks import queue_store_restore

        queue_store_restore(
            store, snap.archive.read(), actor=request.user,
            include_sensitive=snap.includes_orders,
            source_label=f"snapshot {snap.created_at:%d %b %Y %H:%M}",
        )
        messages.info(
            request,
            "Restoring from that snapshot in the background — this can take a few "
            "minutes for a large store. Refresh this page to check progress.",
        )
        return redirect("control:owner_backup")
