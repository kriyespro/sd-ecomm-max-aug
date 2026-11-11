"""Marketing -> Segments (/admin/marketing/segments/): turns the already-
computed Customer.segment into an actionable WhatsApp broadcast. No new
persistence model -- each send is a one-off owner action queued straight to
Celery, not a logged campaign in this pass."""

from django.contrib import messages
from django.shortcuts import redirect
from django.views.generic import TemplateView, View

from apps.accounts.permissions import OWNER_MANAGER, StoreRoleRequiredMixin
from apps.customers.models import Customer, Segment

from .mixins import ActiveProjectMixin, StoreDataAccessMixin


class SegmentBroadcastListView(StoreDataAccessMixin, StoreRoleRequiredMixin, ActiveProjectMixin, TemplateView):
    template_name = "control/marketing/segments.jinja"
    required_store_roles = OWNER_MANAGER
    role_denied_message = "Only the store owner or a manager can message customer segments."

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        project = self.active_project
        counts = []
        for value, label in Segment.choices:
            qs = Customer.objects.filter(project=project, segment=value)
            counts.append({
                "value": value, "label": label, "total": qs.count(),
                "reachable": qs.filter(marketing_opt_in=True).exclude(phone="").count(),
            })
        ctx["segment_counts"] = counts
        return ctx


class SegmentBroadcastSendView(StoreDataAccessMixin, StoreRoleRequiredMixin, ActiveProjectMixin, View):
    required_store_roles = OWNER_MANAGER
    role_denied_message = "Only the store owner or a manager can message customer segments."

    def post(self, request):
        from apps.notifications.models import Event
        from apps.whatsapp.models import account_for
        from apps.whatsapp.services import wa_template_for

        project = self.active_project
        segment = request.POST.get("segment", "").strip()
        message = request.POST.get("message", "").strip()

        if segment not in dict(Segment.choices):
            messages.error(request, "Choose a customer segment.")
            return redirect("control:segment_broadcast")
        if not message:
            messages.error(request, "Write a message to send.")
            return redirect("control:segment_broadcast")
        if account_for(project) is None:
            messages.error(request, "Connect WhatsApp before sending a broadcast.")
            return redirect("control:segment_broadcast")
        if wa_template_for(project, Event.MARKETING_BROADCAST) is None:
            messages.error(
                request,
                "Set up a WhatsApp template for “Marketing broadcast” on the "
                "Notifications settings screen first.",
            )
            return redirect("control:segment_broadcast")

        eligible = (
            Customer.objects.filter(project=project, segment=segment, marketing_opt_in=True)
            .exclude(phone="").count()
        )
        if eligible == 0:
            messages.error(request, "No opted-in customers with a phone number in that segment.")
            return redirect("control:segment_broadcast")

        from apps.customers.tasks import send_segment_broadcast_task

        send_segment_broadcast_task.delay(project.id, segment, message)
        messages.success(request, f"Queued to {eligible} customer(s).")
        return redirect("control:segment_broadcast")
