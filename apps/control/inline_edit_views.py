"""Mission Control -> storefront inline editor entry point."""

from django.contrib import messages
from django.http import HttpResponseRedirect
from django.shortcuts import redirect
from django.views import View

from apps.accounts.permissions import OWNER_MANAGER, StoreRoleRequiredMixin
from apps.shopfront.inline_edit import handoff_url

from .mixins import ActiveProjectMixin


class EditStoreView(StoreRoleRequiredMixin, ActiveProjectMixin, View):
    """Open the active store's storefront in edit mode.

    Works from any host: the link carries a short-lived signed token so a
    platform admin / DGC (whose login is on the platform domain) gets an
    editor-only session on the store's own host. Owner / manager only, plus the
    platform people ``has_store_role`` already lets through.
    """

    required_store_roles = OWNER_MANAGER
    role_denied_message = "Only the store owner or a manager can edit the storefront."

    def get(self, request):
        url = handoff_url(request.user, self.active_project)
        if not url:
            messages.info(request, "Connect a domain first — then you can edit your storefront live.")
            return redirect("control:domains")
        return HttpResponseRedirect(f"{url}&edit=1")
