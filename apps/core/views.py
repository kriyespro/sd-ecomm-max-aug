from django.contrib import messages
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from apps.core.models import AuditLog
from apps.core.services import record_audit

# Marketing-partner commission band + the sales gate to reach the top rate.
PARTNER_COMMISSION_MIN = 20
PARTNER_COMMISSION_MAX = 30
PARTNER_SALES_TO_QUALIFY = 3

# Fallback theme names when the Skin table is empty (fresh install) — these are
# the built-in skin folders under templates/shopfront/skins/.
_BUILTIN_SKINS = [
    "Default", "Kapiva", "Coral", "Marble", "Noir", "Ornza", "Grove",
    "Sunbaked", "Cobalt", "Bloom", "Neon", "Mono", "Impact", "Linen",
]


def root(request):
    """Site root.

    - ``Host`` resolves to a store (verified custom domain or a project's
      ``primary_domain``) -> send visitors to the storefront under ``/app/``
      (the skin middleware keys off that prefix).
    - Otherwise -> the platform marketing landing page.
    """
    if getattr(request, "project", None):
        return redirect("/app/")
    return render(request, "marketing/landing.jinja", _landing_context())


def _landing_context():
    from django.utils import timezone

    from apps.billing.models import Plan

    plans = list(
        Plan.objects.filter(is_active=True, is_public=True).order_by(
            "sort_order", "price_monthly"
        )
    )
    # Highlight a "most popular" tier — the middle one when there are 3+.
    popular_code = plans[len(plans) // 2].code if len(plans) >= 3 else ""

    return {
        "plans": plans,
        "popular_code": popular_code,
        "stats": _landing_stats(),
        "skins": _landing_skins(),
        "now_year": timezone.now().year,
    }


def _landing_stats():
    try:
        from apps.catalog.models import Product, ProductStatus
        from apps.projects.models import Project

        return {
            "stores": Project.objects.count(),
            "products": Product.objects.filter(status=ProductStatus.ACTIVE).count(),
            "themes": _skin_count(),
        }
    except Exception:  # noqa: BLE001 — the landing page must never 500 over a stat
        return {}


def _skin_count():
    try:
        from apps.cms.models import Skin

        return Skin.objects.filter(is_active=True).count() or len(_BUILTIN_SKINS)
    except Exception:  # noqa: BLE001
        return len(_BUILTIN_SKINS)


def _landing_skins():
    try:
        from apps.cms.models import Skin

        names = list(
            Skin.objects.filter(is_active=True)
            .order_by("label")
            .values_list("label", flat=True)[:14]
        )
        if names:
            return names
    except Exception:  # noqa: BLE001
        pass
    return _BUILTIN_SKINS


# --- marketing partners -------------------------------------------------

def _partner_context(form=None):
    from apps.core.forms import PartnerApplicationForm

    return {
        "form": form or PartnerApplicationForm(),
        "commission_min": PARTNER_COMMISSION_MIN,
        "commission_max": PARTNER_COMMISSION_MAX,
        "sales_to_qualify": PARTNER_SALES_TO_QUALIFY,
        "now_year": timezone.now().year,
    }


def partners(request):
    """Public marketing-partner (DGC) programme page + application form."""
    from apps.core.forms import PartnerApplicationForm

    if getattr(request, "project", None):
        # a store's own domain never serves the platform partner page
        return redirect("/app/")

    if request.method == "POST":
        form = PartnerApplicationForm(request.POST)
        if form.is_valid():
            app = form.save()
            record_audit(
                actor=request.user if request.user.is_authenticated else None,
                action=AuditLog.Action.CREATE, target=app,
                changes={"email": app.email}, request=request,
            )
            messages.success(
                request,
                "Application received. We'll email you once it's reviewed.",
            )
            return redirect(f"{reverse('partners')}#apply")
        return render(request, "marketing/partners.jinja", _partner_context(form), status=400)

    return render(request, "marketing/partners.jinja", _partner_context())
