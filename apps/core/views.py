from django.shortcuts import redirect, render


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
    from apps.billing.models import Plan

    plans = list(
        Plan.objects.filter(is_active=True, is_public=True).order_by(
            "sort_order", "price_monthly"
        )
    )
    return {"plans": plans}
