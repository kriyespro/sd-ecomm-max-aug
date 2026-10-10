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
    from apps.core.brand import brand_for_request

    return render(request, brand_for_request(request).landing_template, _landing_context())


LEGAL_UPDATED = "1 October 2026"


def legal_page(request, kind):
    """Public /privacy/ and /terms/ — required by Google/Meta ad review and
    linked from the marketing + ad landing pages. Platform host only."""
    from django.conf import settings
    from django.http import Http404

    if kind not in ("privacy", "terms"):
        raise Http404
    if getattr(request, "project", None):
        return redirect("/app/")
    try:
        from apps.billing.models import BillingSettings

        trial_days = BillingSettings.load().self_signup_trial_days
    except Exception:  # noqa: BLE001
        trial_days = 7
    from apps.core.brand import brand_for_request

    brand = brand_for_request(request)
    return render(request, f"legal/{kind}.jinja", {
        "active": kind,
        "entity": brand.legal_entity,
        "contact_email": brand.contact_email,
        "updated": LEGAL_UPDATED,
        "trial_days": trial_days,
        "now_year": timezone.now().year,
        "meta": (
            f"How {brand.name} collects, uses and protects your data."
            if kind == "privacy" else
            f"The terms for using the {brand.name} online store platform."
        ),
    })


def _faq_jsonld(faq):
    """FAQPage structured data, safe to drop inside a <script> tag."""
    import json

    data = {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {"@type": "Question", "name": q,
             "acceptedAnswer": {"@type": "Answer", "text": a}}
            for q, a in faq
        ],
    }
    return json.dumps(data).replace("</", "<\\/")


def ad_landing(request, slug):
    """Paid-traffic landing page (see ``apps.core.landing_pages``). Platform
    host only; a store's own domain has no business serving it. Reads no
    session/cookie so it stays CDN-cacheable."""
    from django.http import Http404

    from apps.billing.models import BillingSettings, Plan

    from .landing_pages import COMMON_FEATURES, PAGES, STEPS, cta_params, signup_href

    page = PAGES.get(slug)
    if page is None:
        raise Http404
    if getattr(request, "project", None):
        return redirect("/app/")
    from apps.core.brand import brand_for_request

    brand = brand_for_request(request)
    if not brand.is_default:
        page = _rebrand(page, brand.name)
    cheapest = (
        Plan.objects.filter(is_active=True, is_public=True, price_monthly__gt=0)
        .order_by("price_monthly").first()
    )
    try:
        trial_days = BillingSettings.load().self_signup_trial_days
    except Exception:  # noqa: BLE001 — never 500 an ad landing page
        trial_days = 7
    ctx = _landing_context()
    ctx.update({
        "page": page,
        "slug": slug,
        "cta_href": signup_href(reverse("accounts:signup"), request.GET, slug),
        "cta_action": reverse("accounts:signup"),
        "cta_params": cta_params(request.GET, slug),
        "faq_jsonld": _faq_jsonld(page["faq"]),
        "trial_days": trial_days,
        "from_price": int(cheapest.price_monthly) if cheapest else None,
        "features": COMMON_FEATURES if brand.is_default else _rebrand(COMMON_FEATURES, brand.name),
        "steps": STEPS,
    })
    return render(request, brand.ad_template, ctx)


def _rebrand(obj, name):
    """Copy of the (str / tuple / list / dict) copy ``obj`` with the default
    platform name swapped for ``name`` — lets one set of ad-page copy serve
    every brand."""
    if isinstance(obj, str):
        return obj.replace("shopinaday", name)
    if isinstance(obj, tuple):
        return tuple(_rebrand(x, name) for x in obj)
    if isinstance(obj, list):
        return [_rebrand(x, name) for x in obj]
    if isinstance(obj, dict):
        return {k: _rebrand(v, name) for k, v in obj.items()}
    return obj


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
        "live_stores": _landing_live_stores(),
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


def _landing_live_stores(limit=12):
    try:
        from django.db.models import Q
        from apps.projects.models import Project

        qs = (
            Project.objects.filter(
                showcase_status=Project.ShowcaseStatus.APPROVED,
                status=Project.Status.ACTIVE,
            )
            # reachable somehow: a primary_domain, OR any verified Domain
            # (covers stores that live on a *.<platform> subdomain).
            .filter(Q(primary_domain__gt="") | Q(domains__is_verified=True))
            .distinct()
            .prefetch_related("domains")
            .order_by("-showcase_reviewed_at", "-updated_at")
        )
        return [p for p in qs[: limit * 2] if p.public_url][:limit]
    except Exception:  # noqa: BLE001 — the landing page must never 500 over this
        return []


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

    from apps.core.brand import brand_for_request

    tpl = brand_for_request(request).partners_template
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
        return render(request, tpl, _partner_context(form), status=400)

    return render(request, tpl, _partner_context())
