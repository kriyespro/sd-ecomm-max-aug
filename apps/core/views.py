from django.http import Http404
from django.shortcuts import redirect


def root(request):
    """Site root.

    The storefront lives under ``/app/`` (skin binding keys off that prefix).
    When the ``Host`` resolves to a store — a verified custom domain or a
    project's ``primary_domain`` — send visitors there so ``https://shop.tld/``
    lands on the shop instead of a 404. Unknown hosts keep 404ing.
    """
    if getattr(request, "project", None):
        return redirect("/app/")
    raise Http404("No store is configured for this domain.")
