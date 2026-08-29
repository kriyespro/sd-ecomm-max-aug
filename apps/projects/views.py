"""Public, host-scoped endpoints for custom-domain onboarding."""

from django.http import HttpResponse

from .domains import domain_token_for_host


def domain_check(request):
    """``GET /.well-known/sd-domain-check`` — echo this host's verification token.

    The custom-domain verifier fetches this over the public internet: if the
    hostname is proxied (Cloudflare) to this server, the token comes back and
    proves the domain routes here. 404 when no Domain row claims the host.
    """
    token = domain_token_for_host(request.get_host().split(":")[0])
    if not token:
        return HttpResponse(status=404)
    resp = HttpResponse(token, content_type="text/plain")
    resp["Cache-Control"] = "no-store"
    return resp
