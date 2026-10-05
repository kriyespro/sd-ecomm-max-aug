"""Platform admin editor for the testimonials shown on the marketing and
ad landing pages (/admin/testimonials/). Only real, consented quotes go live
(see ``apps.core.models.Testimonial``).
"""

from django import forms
from django.contrib import messages
from django.urls import reverse_lazy
from django.views.generic import CreateView, DeleteView, ListView, UpdateView

from apps.core.landing_pages import PAGES
from apps.core.mixins import PlatformAdminRequiredMixin
from apps.core.models import AuditLog, Testimonial
from apps.core.services import record_audit


class TestimonialForm(forms.ModelForm):
    pages = forms.MultipleChoiceField(
        choices=[(slug, f"/{slug}/") for slug in PAGES],
        widget=forms.CheckboxSelectMultiple, required=False,
        help_text="Leave all unticked to show it on every landing page.",
    )

    class Meta:
        model = Testimonial
        fields = ["quote", "name", "role", "pages", "consent_confirmed",
                  "is_published", "order"]
        widgets = {"quote": forms.Textarea(attrs={"rows": 4})}
        labels = {"consent_confirmed": "I have this seller's permission to publish"}

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("is_published") and not cleaned.get("consent_confirmed"):
            self.add_error(
                "consent_confirmed",
                "Confirm you have the seller's permission before publishing.",
            )
        return cleaned


class TestimonialListView(PlatformAdminRequiredMixin, ListView):
    template_name = "control/testimonials/list.jinja"
    context_object_name = "testimonials"
    queryset = Testimonial.objects.all()


class _TestimonialForm(PlatformAdminRequiredMixin):
    model = Testimonial
    form_class = TestimonialForm
    template_name = "control/testimonials/form.jinja"
    success_url = reverse_lazy("control:testimonials")

    def form_valid(self, form):
        resp = super().form_valid(form)
        record_audit(actor=self.request.user, action=AuditLog.Action.UPDATE,
                     target=self.object, request=self.request)
        messages.success(self.request, "Testimonial saved.")
        return resp


class TestimonialCreateView(_TestimonialForm, CreateView):
    pass


class TestimonialUpdateView(_TestimonialForm, UpdateView):
    pass


class TestimonialDeleteView(PlatformAdminRequiredMixin, DeleteView):
    model = Testimonial
    success_url = reverse_lazy("control:testimonials")

    def form_valid(self, form):
        record_audit(actor=self.request.user, action=AuditLog.Action.DELETE,
                     target=self.get_object(), request=self.request)
        messages.success(self.request, "Testimonial removed.")
        return super().form_valid(form)
