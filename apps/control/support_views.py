"""Feature requests + support tickets.

Two screens:

* ``/admin/support/`` — owner / manager / DGC raise and read tickets: a
  platform-wide feature-request board (vote instead of filing a dupe) plus
  their own store's private bug/support/billing queue.
* ``/admin/tickets/`` — the platform admin's single triage queue across every
  store, with internal notes and status/priority/assignment controls.
"""

from django import forms
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.generic import CreateView, DetailView, ListView, TemplateView, View

from apps.accounts.models import PlatformRole
from apps.accounts.permissions import OWNER_MANAGER, StoreRoleRequiredMixin
from apps.core.mixins import PlatformAdminRequiredMixin
from apps.core.models import AuditLog
from apps.core.services import record_audit
from apps.support import services as support_svc
from apps.support.models import (
    ReporterRole,
    Ticket,
    TicketKind,
    TicketMessage,
    TicketPriority,
    TicketStatus,
    TicketVote,
    reporter_role_for,
)

from .mixins import ActiveProjectMixin


class TicketForm(forms.ModelForm):
    class Meta:
        model = Ticket
        fields = ["kind", "subject", "description", "priority"]
        widgets = {"description": forms.Textarea(attrs={"rows": 5})}
        labels = {"kind": "Type"}


class TicketMessageForm(forms.ModelForm):
    class Meta:
        model = TicketMessage
        fields = ["body"]
        widgets = {"body": forms.Textarea(
            attrs={"rows": 3, "placeholder": "Write a reply…"})}
        labels = {"body": ""}


class AdminTicketMessageForm(TicketMessageForm):
    class Meta(TicketMessageForm.Meta):
        fields = ["body", "is_internal_note"]
        labels = {**TicketMessageForm.Meta.labels,
                 "is_internal_note": "Internal note (not visible to the store)"}


class AdminTicketUpdateForm(forms.Form):
    status = forms.ChoiceField(choices=TicketStatus.choices)
    priority = forms.ChoiceField(choices=TicketPriority.choices)
    assigned_to = forms.ModelChoiceField(queryset=None, required=False, label="Assigned to")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["assigned_to"].queryset = get_user_model().objects.filter(
            Q(is_superuser=True) | Q(profile__platform_role=PlatformRole.OWNER)
        ).distinct().order_by("email")


# ---------------------------------------------------------------- store side

class _SupportBase(StoreRoleRequiredMixin, ActiveProjectMixin):
    required_store_roles = OWNER_MANAGER
    role_denied_message = "Only the store owner, a manager, or your DGC can use Support."


class SupportListView(_SupportBase, TemplateView):
    template_name = "control/support/list.jinja"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ideas = (Ticket.objects.filter(kind=TicketKind.FEATURE_REQUEST)
                .select_related("project")
                .annotate(vote_count=Count("votes"))
                .order_by("-vote_count", "-created_at"))
        ctx["ideas"] = ideas[:100]
        ctx["voted_ids"] = set(
            TicketVote.objects.filter(user=self.request.user, ticket__in=ctx["ideas"])
            .values_list("ticket_id", flat=True)
        )
        ctx["my_tickets"] = (
            Ticket.objects.filter(project=self.active_project)
            .exclude(kind=TicketKind.FEATURE_REQUEST)
            .select_related("assigned_to")
        )
        return ctx


class SupportCreateView(_SupportBase, CreateView):
    form_class = TicketForm
    template_name = "control/support/form.jinja"

    def get_initial(self):
        initial = super().get_initial()
        kind = self.request.GET.get("kind")
        if kind in TicketKind.values:
            initial["kind"] = kind
        return initial

    def form_valid(self, form):
        form.instance.project = self.active_project
        form.instance.created_by = self.request.user
        form.instance.created_by_role = reporter_role_for(self.request.user, self.active_project)
        resp = super().form_valid(form)
        record_audit(actor=self.request.user, project=self.active_project,
                    action=AuditLog.Action.CREATE, target=self.object, request=self.request)
        messages.success(self.request, "Submitted. You'll hear back in this thread.")
        return resp

    def get_success_url(self):
        return reverse("control:support_detail", kwargs={"pk": self.object.pk})


class SupportDetailView(_SupportBase, DetailView):
    model = Ticket
    template_name = "control/support/detail.jinja"
    context_object_name = "ticket"

    def get_object(self, queryset=None):
        obj = get_object_or_404(
            Ticket.objects.select_related("created_by", "project", "assigned_to"),
            pk=self.kwargs["pk"],
        )
        if not obj.is_global and obj.project_id != self.active_project.pk:
            raise Http404
        return obj

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["reply_form"] = TicketMessageForm()
        ctx["thread"] = self.object.messages.filter(is_internal_note=False).select_related("author")
        ctx["vote_count"] = self.object.votes.count()
        ctx["has_voted"] = (
            self.object.kind == TicketKind.FEATURE_REQUEST
            and self.object.votes.filter(user=self.request.user).exists()
        )
        return ctx


class SupportReplyView(_SupportBase, View):
    def post(self, request, pk):
        ticket = get_object_or_404(Ticket, pk=pk)
        if not ticket.is_global and ticket.project_id != self.active_project.pk:
            raise Http404
        form = TicketMessageForm(request.POST)
        if form.is_valid():
            support_svc.add_message(
                ticket, author=request.user,
                author_role=reporter_role_for(request.user, self.active_project),
                body=form.cleaned_data["body"],
            )
            messages.success(request, "Reply posted.")
        else:
            messages.error(request, "Write something before sending.")
        return redirect("control:support_detail", pk=pk)


class SupportVoteView(_SupportBase, View):
    def post(self, request, pk):
        ticket = get_object_or_404(Ticket, pk=pk, kind=TicketKind.FEATURE_REQUEST)
        support_svc.toggle_vote(ticket, request.user)
        nxt = request.POST.get("next")
        return redirect(nxt or "control:support")


# --------------------------------------------------------------- admin queue

class SupportQueueListView(PlatformAdminRequiredMixin, ListView):
    template_name = "control/support/admin_list.jinja"
    context_object_name = "tickets"
    paginate_by = 50

    def get_queryset(self):
        qs = (Ticket.objects.select_related("project", "created_by", "assigned_to")
             .annotate(vote_count=Count("votes")))
        status = self.request.GET.get("status")
        kind = self.request.GET.get("kind")
        if status in TicketStatus.values:
            qs = qs.filter(status=status)
        if kind in TicketKind.values:
            qs = qs.filter(kind=kind)
        if self.request.GET.get("mine"):
            qs = qs.filter(assigned_to=self.request.user)
        if self.request.GET.get("unassigned"):
            qs = qs.filter(assigned_to__isnull=True)
        return qs.order_by("-created_at")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["status_choices"] = TicketStatus.choices
        ctx["kind_choices"] = TicketKind.choices
        ctx["current_status"] = self.request.GET.get("status", "")
        ctx["current_kind"] = self.request.GET.get("kind", "")
        ctx["open_count"] = Ticket.objects.exclude(
            status__in=[TicketStatus.RESOLVED, TicketStatus.CLOSED]
        ).count()
        return ctx


class SupportQueueDetailView(PlatformAdminRequiredMixin, DetailView):
    model = Ticket
    template_name = "control/support/admin_detail.jinja"
    context_object_name = "ticket"

    def get_queryset(self):
        return Ticket.objects.select_related("project", "created_by", "assigned_to")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["reply_form"] = AdminTicketMessageForm()
        ctx["update_form"] = AdminTicketUpdateForm(initial={
            "status": self.object.status,
            "priority": self.object.priority,
            "assigned_to": self.object.assigned_to_id,
        })
        ctx["thread"] = self.object.messages.select_related("author")
        ctx["vote_count"] = self.object.votes.count()
        return ctx


class SupportQueueReplyView(PlatformAdminRequiredMixin, View):
    def post(self, request, pk):
        ticket = get_object_or_404(Ticket, pk=pk)
        form = AdminTicketMessageForm(request.POST)
        if form.is_valid():
            support_svc.add_message(
                ticket, author=request.user, author_role=ReporterRole.ADMIN,
                body=form.cleaned_data["body"],
                is_internal_note=form.cleaned_data["is_internal_note"],
            )
            if not ticket.assigned_to_id:
                ticket.assigned_to = request.user
                ticket.save(update_fields=["assigned_to", "updated_at"])
            messages.success(request, "Reply posted.")
        else:
            messages.error(request, "Write something before sending.")
        return redirect("control:support_queue_detail", pk=pk)


class SupportQueueUpdateView(PlatformAdminRequiredMixin, View):
    def post(self, request, pk):
        ticket = get_object_or_404(Ticket, pk=pk)
        form = AdminTicketUpdateForm(request.POST)
        if form.is_valid():
            ticket.priority = form.cleaned_data["priority"]
            ticket.assigned_to = form.cleaned_data["assigned_to"]
            ticket.save(update_fields=["priority", "assigned_to", "updated_at"])
            if form.cleaned_data["status"] != ticket.status:
                support_svc.set_status(ticket, form.cleaned_data["status"])
            record_audit(actor=request.user, project=ticket.project,
                        action=AuditLog.Action.UPDATE, target=ticket, request=request)
            messages.success(request, "Ticket updated.")
        else:
            messages.error(request, "Couldn't update the ticket.")
        return redirect("control:support_queue_detail", pk=pk)
