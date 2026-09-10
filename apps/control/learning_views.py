"""Learning / training videos.

- Platform admin manages the library (/admin/learning/).
- Every control-panel user sees the videos for their role, grouped by topic
  (/admin/training/).
"""

from django import forms
from django.contrib import messages
from django.urls import reverse_lazy
from django.views.generic import CreateView, DeleteView, ListView, TemplateView, UpdateView

from apps.core.mixins import ControlAccessMixin, PlatformAdminRequiredMixin
from apps.core.models import AuditLog
from apps.core.services import record_audit
from apps.learning.models import (
    Audience,
    LearningVideo,
    audience_keys_for,
    extract_youtube_id,
    videos_for,
)

from .mixins import get_active_project


class LearningVideoForm(forms.ModelForm):
    audience = forms.MultipleChoiceField(
        choices=Audience.choices, widget=forms.CheckboxSelectMultiple,
        help_text="Leave all unticked to show it to everyone.",
        required=False,
    )

    class Meta:
        model = LearningVideo
        fields = ["title", "topic", "description", "source_url", "duration_label",
                  "audience", "order", "is_published"]
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}

    def clean_source_url(self):
        url = self.cleaned_data["source_url"].strip()
        if not extract_youtube_id(url):
            raise forms.ValidationError("Couldn't find a YouTube video id in that link.")
        return url


class LearningListView(PlatformAdminRequiredMixin, ListView):
    template_name = "control/learning/list.jinja"
    context_object_name = "videos"
    queryset = LearningVideo.objects.all()


class _LearningForm(PlatformAdminRequiredMixin):
    form_class = LearningVideoForm
    template_name = "control/learning/form.jinja"
    success_url = reverse_lazy("control:learning")
    model = LearningVideo

    def form_valid(self, form):
        resp = super().form_valid(form)
        record_audit(actor=self.request.user, action=AuditLog.Action.UPDATE,
                     target=self.object, request=self.request)
        messages.success(self.request, "Video saved.")
        return resp


class LearningCreateView(_LearningForm, CreateView):
    pass


class LearningUpdateView(_LearningForm, UpdateView):
    pass


class LearningDeleteView(PlatformAdminRequiredMixin, DeleteView):
    model = LearningVideo
    success_url = reverse_lazy("control:learning")

    def form_valid(self, form):
        record_audit(actor=self.request.user, action=AuditLog.Action.DELETE,
                     target=self.get_object(), request=self.request)
        messages.success(self.request, "Video removed.")
        return super().form_valid(form)


class TrainingLibraryView(ControlAccessMixin, TemplateView):
    """Read-only, role-filtered library for every control-panel user."""

    template_name = "control/learning/library.jinja"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        keys = audience_keys_for(self.request.user, get_active_project(self.request))
        ctx["groups"] = videos_for(keys)
        ctx["is_admin_editor"] = self.request.user.is_authenticated and (
            self.request.user.is_superuser
            or getattr(getattr(self.request.user, "profile", None), "is_platform_admin", False)
        )
        return ctx
