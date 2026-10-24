"""Platform marketing blog — post authoring (/admin/blog/). Platform admin
only; categories/tags/authors are low-churn lookup data managed in Django
admin instead (see apps.blog.admin)."""

from django import forms
from django.contrib import messages
from django.urls import reverse_lazy
from django.views.generic import CreateView, DeleteView, ListView, UpdateView

from apps.blog.models import BlogAuthor, BlogCategory, BlogPost, BlogTag
from apps.core.mixins import PlatformAdminRequiredMixin
from apps.core.models import AuditLog
from apps.core.services import record_audit


class BlogPostForm(forms.ModelForm):
    class Meta:
        model = BlogPost
        fields = ["title", "slug", "excerpt", "body", "cover_image", "category",
                  "tags", "author", "seo_title", "seo_description", "is_published"]
        widgets = {
            "excerpt": forms.Textarea(attrs={"rows": 2}),
            "body": forms.Textarea(attrs={"rows": 20}),
            "tags": forms.SelectMultiple,
        }
        help_texts = {
            "slug": "Leave blank to generate from the title.",
            "excerpt": "Shown on the blog list and used as the meta description if SEO description is blank.",
            "body": "HTML is sanitised on save (same rules as CMS pages) — safe to paste from a rich editor.",
            "seo_title": "Leave blank to use the post title.",
            "seo_description": "Leave blank to fall back to the excerpt.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = BlogCategory.objects.all()
        self.fields["tags"].queryset = BlogTag.objects.all()
        self.fields["author"].queryset = BlogAuthor.objects.all()


class BlogPostListView(PlatformAdminRequiredMixin, ListView):
    template_name = "control/blog/list.jinja"
    context_object_name = "posts"
    queryset = BlogPost.objects.select_related("category", "author")


class _BlogPostForm(PlatformAdminRequiredMixin):
    form_class = BlogPostForm
    template_name = "control/blog/form.jinja"
    success_url = reverse_lazy("control:blog")
    model = BlogPost

    def form_valid(self, form):
        resp = super().form_valid(form)
        record_audit(actor=self.request.user, action=AuditLog.Action.UPDATE,
                     target=self.object, request=self.request)
        messages.success(self.request, "Post saved.")
        return resp


class BlogPostCreateView(_BlogPostForm, CreateView):
    pass


class BlogPostUpdateView(_BlogPostForm, UpdateView):
    pass


class BlogPostDeleteView(PlatformAdminRequiredMixin, DeleteView):
    model = BlogPost
    success_url = reverse_lazy("control:blog")

    def form_valid(self, form):
        record_audit(actor=self.request.user, action=AuditLog.Action.DELETE,
                     target=self.get_object(), request=self.request)
        messages.success(self.request, "Post removed.")
        return super().form_valid(form)
