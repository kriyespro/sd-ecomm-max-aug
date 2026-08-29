"""Control-panel: storefront skins.

Two audiences:
* Platform admins — register / edit skins, toggle availability, set the default.
* Platform admins acting on one store — grant which skins that store may use
  (``Project.allowed_skins``). The store owner then picks one on the Theme
  screen.
"""

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views.generic import CreateView, ListView, TemplateView, UpdateView, View

from apps.accounts.permissions import (
    OWNER_MANAGER,
    StoreRoleRequiredMixin,
    is_platform_admin,
)
from apps.cms.models import Skin, SkinSource, SkinStatus
from apps.cms.skin_upload import create_skin_from_upload
from apps.cms.skins import allowed_skins_for
from apps.core.mixins import PlatformAdminRequiredMixin
from apps.core.models import AuditLog
from apps.core.services import record_audit

from .forms import SkinForm, SkinUploadForm
from .mixins import ActiveProjectMixin


class SkinListView(PlatformAdminRequiredMixin, ListView):
    template_name = "control/skins/skin_list.jinja"
    context_object_name = "skins"
    queryset = Skin.objects.all()


class _SkinFormView(PlatformAdminRequiredMixin):
    form_class = SkinForm
    template_name = "control/skins/skin_form.jinja"
    success_url = reverse_lazy("control:skin_list")
    queryset = Skin.objects.all()

    def form_valid(self, form):
        response = super().form_valid(form)
        record_audit(
            actor=self.request.user,
            action=AuditLog.Action.CREATE if isinstance(self, CreateView) else AuditLog.Action.UPDATE,
            target=self.object, request=self.request,
        )
        messages.success(self.request, "Skin saved.")
        return response


class SkinCreateView(_SkinFormView, CreateView):
    pass


class SkinUpdateView(_SkinFormView, UpdateView):
    pass


class SkinToggleActiveView(PlatformAdminRequiredMixin, View):
    def post(self, request, pk, *args, **kwargs):
        skin = Skin.objects.filter(pk=pk).first()
        if skin is None:
            raise PermissionDenied
        skin.is_active = not skin.is_active
        if not skin.is_active and skin.is_default:
            messages.error(request, "The default skin cannot be deactivated.")
            return redirect("control:skin_list")
        skin.save(update_fields=["is_active", "updated_at"])
        record_audit(actor=request.user, action=AuditLog.Action.UPDATE,
                     target=skin, changes={"is_active": skin.is_active}, request=request)
        messages.success(request, f"{skin.label} {'activated' if skin.is_active else 'deactivated'}.")
        return redirect("control:skin_list")


class SkinSetDefaultView(PlatformAdminRequiredMixin, View):
    @transaction.atomic
    def post(self, request, pk, *args, **kwargs):
        skin = Skin.objects.select_for_update().filter(pk=pk).first()
        if skin is None:
            raise PermissionDenied
        if not skin.is_active:
            messages.error(request, "Activate the skin before making it the default.")
            return redirect("control:skin_list")
        Skin.objects.filter(is_default=True).exclude(pk=skin.pk).update(is_default=False)
        skin.is_default = True
        skin.save(update_fields=["is_default", "updated_at"])
        record_audit(actor=request.user, action=AuditLog.Action.UPDATE,
                     target=skin, changes={"is_default": True}, request=request)
        messages.success(request, f"{skin.label} is now the default skin.")
        return redirect("control:skin_list")


class StoreSkinAccessView(ActiveProjectMixin, TemplateView):
    """Per-store: which shared skins the owner may choose + upload permission.
    Platform admins only."""

    template_name = "control/skins/store_access.jinja"

    def check_active_project_access(self, request):
        parent = super().check_active_project_access(request)
        if parent is not None:
            return parent
        if not is_platform_admin(request.user):
            raise PermissionDenied("Only a platform admin can change a store's skin access.")
        return None

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["all_skins"] = Skin.objects.filter(
            is_active=True, status=SkinStatus.APPROVED, project__isnull=True
        ).order_by("label")
        ctx["allowed_ids"] = set(
            self.active_project.allowed_skins.values_list("id", flat=True)
        )
        ctx["effective"] = allowed_skins_for(self.active_project)
        ctx["upload_enabled"] = bool(
            self.active_project.feature_flags.get("skin_upload")
        )
        return ctx

    def post(self, request, *args, **kwargs):
        ids = request.POST.getlist("skins")
        skins = Skin.objects.filter(
            is_active=True, status=SkinStatus.APPROVED, project__isnull=True, id__in=ids
        )
        self.active_project.allowed_skins.set(skins)
        flags = dict(self.active_project.feature_flags or {})
        flags["skin_upload"] = request.POST.get("skin_upload") == "on"
        self.active_project.feature_flags = flags
        self.active_project.save(update_fields=["feature_flags"])
        record_audit(
            actor=request.user, project=self.active_project,
            action=AuditLog.Action.UPDATE, target=self.active_project,
            changes={"allowed_skins": sorted(s.slug for s in skins),
                     "skin_upload": flags["skin_upload"]}, request=request,
        )
        messages.success(request, "Skin access updated.")
        return redirect("control:store_skin_access")


# --- tenant upload -------------------------------------------------

class SkinUploadView(StoreRoleRequiredMixin, ActiveProjectMixin, TemplateView):
    template_name = "control/skins/upload.jinja"
    required_store_roles = OWNER_MANAGER
    role_denied_message = "Only the store owner or a manager can upload skins."

    def check_active_project_access(self, request):
        parent = super().check_active_project_access(request)
        if parent is not None:
            return parent
        if not (is_platform_admin(request.user)
                or self.active_project.feature_flags.get("skin_upload")):
            raise PermissionDenied(
                "Skin upload is not enabled for this store. Ask a platform admin."
            )
        return None

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.setdefault("form", SkinUploadForm())
        ctx["my_skins"] = Skin.objects.filter(
            source=SkinSource.UPLOAD, project=self.active_project
        ).order_by("-created_at")
        return ctx

    def post(self, request, *args, **kwargs):
        form = SkinUploadForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                skin = create_skin_from_upload(
                    project=self.active_project, user=request.user,
                    fileobj=form.cleaned_data["bundle"],
                    label=form.cleaned_data["label"],
                )
            except ValidationError as exc:
                for msg in exc.messages:
                    messages.error(request, msg)
            else:
                record_audit(actor=request.user, project=self.active_project,
                             action=AuditLog.Action.CREATE, target=skin, request=request)
                messages.success(
                    request,
                    f"“{skin.label}” uploaded. It is now pending platform review.",
                )
                return redirect("control:skin_upload")
        return self.render_to_response(self.get_context_data(form=form))


# --- platform review ----------------------------------------------

class SkinReviewView(PlatformAdminRequiredMixin, TemplateView):
    template_name = "control/skins/review.jinja"

    def _skin(self):
        return get_object_or_404(Skin, pk=self.kwargs["pk"], source=SkinSource.UPLOAD)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        skin = self._skin()
        ctx["skin"] = skin
        ctx["files"] = skin.files.all()
        ctx["assets"] = skin.assets.all()
        ctx["preview_project"] = skin.project
        return ctx

    def post(self, request, *args, **kwargs):
        skin = self._skin()
        action = request.POST.get("action")
        note = request.POST.get("note", "").strip()[:255]
        if action == "approve":
            skin.status = SkinStatus.APPROVED
        elif action == "reject":
            skin.status = SkinStatus.REJECTED
        else:
            messages.error(request, "Unknown action.")
            return redirect("control:skin_review", pk=skin.pk)
        skin.review_note = note
        skin.save(update_fields=["status", "review_note", "updated_at"])
        record_audit(actor=request.user, action=AuditLog.Action.UPDATE, target=skin,
                     changes={"status": skin.status, "note": note}, request=request)
        messages.success(request, f"Skin {skin.get_status_display().lower()}.")
        return redirect("control:skin_list")


class SkinPromoteView(PlatformAdminRequiredMixin, View):
    def post(self, request, pk, *args, **kwargs):
        skin = get_object_or_404(Skin, pk=pk, source=SkinSource.UPLOAD)
        if skin.status != SkinStatus.APPROVED:
            messages.error(request, "Approve the skin before promoting it.")
            return redirect("control:skin_list")
        skin.project = None
        skin.save(update_fields=["project", "updated_at"])
        record_audit(actor=request.user, action=AuditLog.Action.UPDATE, target=skin,
                     changes={"promoted": True}, request=request)
        messages.success(request, f"“{skin.label}” is now in the shared catalogue.")
        return redirect("control:skin_list")
