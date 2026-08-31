from django import forms

from apps.accounts.models import PartnerApplication


class PartnerApplicationForm(forms.ModelForm):
    # honeypot — bots fill it, humans never see it
    website = forms.CharField(required=False, widget=forms.HiddenInput)

    class Meta:
        model = PartnerApplication
        fields = ["full_name", "email", "phone", "company", "audience"]
        widgets = {
            "audience": forms.Textarea(attrs={"rows": 4}),
        }

    _CSS = (
        "w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm "
        "text-slate-900 outline-none transition focus:border-brand-500 "
        "focus:ring-2 focus:ring-brand-500/30"
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if name == "website":
                continue
            field.widget.attrs.setdefault("class", self._CSS)
        self.fields["full_name"].widget.attrs["placeholder"] = "Your name"
        self.fields["email"].widget.attrs["placeholder"] = "you@example.com"
        self.fields["phone"].widget.attrs["placeholder"] = "Phone (optional)"
        self.fields["company"].widget.attrs["placeholder"] = "Company / brand (optional)"
        self.fields["audience"].widget.attrs["placeholder"] = (
            "How do you reach merchants? Agency clients, a community, a channel…"
        )
        self.fields["phone"].required = False
        self.fields["company"].required = False

    def clean_email(self):
        email = (self.cleaned_data["email"] or "").strip().lower()
        if PartnerApplication.objects.filter(
            email__iexact=email, status=PartnerApplication.Status.PENDING
        ).exists():
            raise forms.ValidationError(
                "An application with this email is already under review."
            )
        return email

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("website"):
            raise forms.ValidationError("Could not submit the form.")
        return cleaned
