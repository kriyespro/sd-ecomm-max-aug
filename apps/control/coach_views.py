"""Daily coach: the owner's checklist toggle + the platform admin's editor.

- The panel itself renders inside the store dashboard (``DashboardView``).
- Platform admin writes tasks / tips / messages at /admin/coach/.
"""

import datetime as dt

from django import forms
from django.contrib import messages
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.urls import reverse_lazy
from django.views.generic import CreateView, DeleteView, ListView, UpdateView, View

from apps.accounts.permissions import StoreRoleRequiredMixin
from apps.coach import services
from apps.coach.checks import AUTO_CHOICES
from apps.coach.models import Mission, MissionKind
from apps.core.mixins import PlatformAdminRequiredMixin
from apps.core.models import AuditLog
from apps.core.services import record_audit

from .mixins import ActiveProjectMixin


class CoachToggleView(StoreRoleRequiredMixin, ActiveProjectMixin, View):
    """htmx POST — tick/untick an owner-checked task, return the fresh panel."""

    http_method_names = ["post"]

    def post(self, request, pk):
        mission = get_object_or_404(Mission, pk=pk, is_active=True, kind=MissionKind.TASK)
        try:
            services.toggle_manual(self.active_project, mission, request.user)
        except ValueError:
            raise Http404
        ctx = services.panel_context(self.active_project, request.user)
        ctx["active_project"] = self.active_project
        return render(request, "control/coach/_panel.jinja", ctx)


class MissionForm(forms.ModelForm):
    auto_key = forms.ChoiceField(
        choices=AUTO_CHOICES, required=False, label="How it completes",
        help_text="Auto tasks tick themselves when the store does the thing; manual ones the owner ticks.",
    )

    class Meta:
        model = Mission
        fields = ["kind", "title", "icon", "detail", "auto_key", "target", "points",
                  "cta_label", "cta_url", "sort_order", "starts_on", "ends_on", "is_active"]
        widgets = {
            "detail": forms.Textarea(attrs={"rows": 3}),
            "starts_on": forms.DateInput(attrs={"type": "date"}),
            "ends_on": forms.DateInput(attrs={"type": "date"}),
        }

    def clean(self):
        data = super().clean()
        if data.get("kind") != MissionKind.TASK:
            data["auto_key"], data["target"], data["points"] = "", 1, 0
        a, b = data.get("starts_on"), data.get("ends_on")
        if a and b and b < a:
            self.add_error("ends_on", "End date is before the start date.")
        return data

    def clean_cta_url(self):
        url = self.cleaned_data["cta_url"].strip()
        if url and not (url.startswith(("/", "https://", "http://", "{store_url}"))):
            raise forms.ValidationError("Use a /admin/… path or a full https:// link.")
        return url


class CoachListView(PlatformAdminRequiredMixin, ListView):
    template_name = "control/coach/list.jinja"
    context_object_name = "missions"

    def get_queryset(self):
        today = services.local_today()
        return Mission.objects.annotate(
            done_today=Count("logs", filter=Q(logs__day=today, logs__completed_at__isnull=False)),
            done_week=Count("logs", filter=Q(logs__day__gte=today - dt.timedelta(days=6),
                                             logs__completed_at__isnull=False)),
        )


class _MissionEdit(PlatformAdminRequiredMixin):
    model = Mission
    form_class = MissionForm
    template_name = "control/coach/form.jinja"
    success_url = reverse_lazy("control:coach")

    def form_valid(self, form):
        resp = super().form_valid(form)
        record_audit(actor=self.request.user, action=AuditLog.Action.UPDATE,
                     target=self.object, request=self.request)
        messages.success(self.request, "Saved — store owners see it on their dashboard.")
        return resp


class CoachCreateView(_MissionEdit, CreateView):
    def get_initial(self):
        kind = self.request.GET.get("kind")
        return {"kind": kind} if kind in MissionKind.values else {}


class CoachUpdateView(_MissionEdit, UpdateView):
    pass


class CoachDeleteView(PlatformAdminRequiredMixin, DeleteView):
    model = Mission
    success_url = reverse_lazy("control:coach")

    def form_valid(self, form):
        record_audit(actor=self.request.user, action=AuditLog.Action.DELETE,
                     target=self.get_object(), request=self.request)
        messages.success(self.request, "Removed.")
        return super().form_valid(form)
