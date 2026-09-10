"""Store-side jewellery / gemstone certificates (owner, manager, staff)."""

from django import forms
from django.contrib import messages
from django.db.models import Q
from django.urls import reverse_lazy
from django.views.generic import CreateView, DeleteView, ListView, UpdateView

from apps.certificates.models import (
    ALLOWED_UPLOAD_EXT,
    Certificate,
    ensure_footer_page,
)
from apps.core.models import AuditLog
from apps.core.services import record_audit

from .mixins import ActiveProjectMixin


class CertificateForm(forms.ModelForm):
    class Meta:
        model = Certificate
        fields = [
            "title", "product", "lab_name", "lab_number",
            "gem_type", "carat_weight", "shape_cut", "color_grade", "clarity_grade",
            "measurements", "metal", "gross_weight", "issued_on", "notes",
            "image_front", "image_back", "is_public",
        ]
        widgets = {
            "issued_on": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, project=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.project = project
        if project is not None:
            from apps.catalog.models import Product

            self.fields["product"].queryset = Product.objects.filter(project=project)
        self.fields["product"].required = False
        if self.instance.pk:
            self.fields["image_front"].required = False

    def _check_ext(self, field):
        f = self.cleaned_data.get(field)
        name = getattr(f, "name", "") or ""
        # Only a freshly uploaded file has content_type; a stored value is skipped.
        if f is not None and hasattr(f, "content_type"):
            ext = name.rsplit(".", 1)[-1].lower()
            if ext not in ALLOWED_UPLOAD_EXT:
                raise forms.ValidationError("Upload a JPG or PNG image.")
        return f

    def clean_image_front(self):
        return self._check_ext("image_front")

    def clean_image_back(self):
        return self._check_ext("image_back")

    def save(self, commit=True):
        obj = super().save(commit=False)
        if self.project is not None:
            obj.project = self.project
        if commit:
            obj.save()
        return obj


class _Base(ActiveProjectMixin):
    model = Certificate

    def get_queryset(self):
        return Certificate.objects.filter(project=self.active_project).select_related("product")


class CertificateListView(_Base, ListView):
    template_name = "control/certificates/list.jinja"
    context_object_name = "certificates"
    paginate_by = 40

    def get_queryset(self):
        qs = super().get_queryset()
        q = self.request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(Q(code__icontains=q) | Q(title__icontains=q)
                           | Q(lab_number__icontains=q) | Q(gem_type__icontains=q))
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["q"] = self.request.GET.get("q", "")
        ctx["verify_url"] = self.request.build_absolute_uri("/verify/")
        return ctx


class _CertForm(_Base):
    form_class = CertificateForm
    template_name = "control/certificates/form.jinja"
    success_url = reverse_lazy("control:certificates")

    def get_form_kwargs(self):
        kw = super().get_form_kwargs()
        kw["project"] = self.active_project
        return kw

    def form_valid(self, form):
        resp = super().form_valid(form)
        ensure_footer_page(self.active_project)
        record_audit(actor=self.request.user, project=self.active_project,
                     action=AuditLog.Action.UPDATE, target=self.object,
                     changes={"code": self.object.code}, request=self.request)
        messages.success(self.request, f"Certificate saved — code {self.object.code}.")
        return resp


class CertificateCreateView(_CertForm, CreateView):
    pass


class CertificateUpdateView(_CertForm, UpdateView):
    pass


class CertificateDeleteView(_Base, DeleteView):
    success_url = reverse_lazy("control:certificates")

    def form_valid(self, form):
        record_audit(actor=self.request.user, project=self.active_project,
                     action=AuditLog.Action.DELETE, target=self.get_object(),
                     request=self.request)
        messages.success(self.request, "Certificate deleted.")
        return super().form_valid(form)
