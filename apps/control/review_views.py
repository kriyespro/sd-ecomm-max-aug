"""Control-panel review moderation."""

from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.views.generic import ListView, View

from apps.reviews import services as rev
from apps.reviews.models import Review, ReviewStatus

from .mixins import ActiveProjectMixin


class ReviewListView(ActiveProjectMixin, ListView):
    template_name = "control/reviews/review_list.jinja"
    context_object_name = "reviews"
    paginate_by = 30

    def get_queryset(self):
        qs = Review.objects.filter(project=self.active_project).select_related("product")
        status = self.request.GET.get("status", "pending").strip()
        if status:
            qs = qs.filter(status=status)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["status"] = self.request.GET.get("status", "pending")
        ctx["status_choices"] = ReviewStatus.choices
        ctx["pending_count"] = Review.objects.filter(
            project=self.active_project, status=ReviewStatus.PENDING
        ).count()
        return ctx


class ReviewModerateView(ActiveProjectMixin, View):
    def post(self, request, *args, **kwargs):
        review = get_object_or_404(Review, pk=kwargs["pk"])
        if review.project_id != self.active_project.pk:
            raise Http404
        status = request.POST.get("status", "").strip()
        if status not in dict(ReviewStatus.choices):
            messages.error(request, "Unknown status.")
        else:
            rev.moderate_review(
                review=review, status=status, actor=request.user,
                reply=request.POST.get("reply", "").strip(),
            )
            messages.success(request, f"Review {status}.")
        return redirect(request.META.get("HTTP_REFERER", "/admin/reviews/"))
