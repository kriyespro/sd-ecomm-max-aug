"""Multi-domain project resolution (project.md sections 4 and 28).

Resolves ``request.project`` from the incoming ``Host`` header. The frontend is
NEVER trusted to supply a project id — resolution happens server-side from the
domain.

Security: a custom ``Domain`` only resolves a store once it is *verified*
(``is_verified=True``). Otherwise anyone could register ``victim.com`` against
their own project and, if that host ever reaches this server, be served their
store on it. ``Project.primary_domain`` is set by platform staff and is trusted.
"""

from django.conf import settings
from django.utils.functional import SimpleLazyObject


class RealClientIPMiddleware:
    """Rewrite ``REMOTE_ADDR`` from the proxy's forwarded headers so throttling,
    audit logs and rate limits key on the real visitor — not on Cloudflare's
    edge IP. No-op unless ``settings.TRUST_PROXY_HEADERS`` is set (only enable
    when the origin is reachable *only* through the trusted proxy).
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.enabled = getattr(settings, "TRUST_PROXY_HEADERS", False)
        self.hops = max(1, getattr(settings, "XFF_TRUSTED_HOPS", 1))

    def __call__(self, request):
        if self.enabled:
            real = request.META.get("HTTP_CF_CONNECTING_IP", "").strip()
            if not real:
                chain = [
                    p.strip()
                    for p in request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")
                    if p.strip()
                ]
                # The leftmost entry is client-controlled; trust only the Nth
                # value counted from the proxy end.
                if len(chain) >= self.hops:
                    real = chain[-self.hops]
            if real:
                request.META["REMOTE_ADDR"] = real
        return self.get_response(request)


def normalize_host(raw: str) -> str:
    host = (raw or "").split(":")[0].strip().lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    return host


def _resolve_project(request):
    from apps.projects.models import Project

    from .store_resolver import binding_for_host

    host = normalize_host(request.get_host())
    if not host:
        return None

    # Platform's own hostnames are never a store, even if stale data points here.
    if host in getattr(settings, "PLATFORM_HOSTS", ()):
        return None

    project_id, _ = binding_for_host(host)
    if project_id:
        # subscription is read by SubscriptionGateMiddleware on every storefront hit
        return (
            Project.objects.filter(pk=project_id)
            .select_related("subscription")
            .first()
        )

    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        memberships = list(user.memberships.select_related("project")[:2])
        if len(memberships) == 1:
            return memberships[0].project

    return None


class ProjectResolverMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.project = SimpleLazyObject(lambda: _resolve_project(request))
        return self.get_response(request)


def _host_is_known(host: str) -> bool:
    """True when ``host`` (already normalised, no port / www) belongs to the
    platform or to a store we actually serve."""
    if not host:
        return False
    platform = getattr(settings, "PLATFORM_HOSTS", ()) or ()
    if host in platform:
        return True
    base = getattr(settings, "PLATFORM_BASE_DOMAIN", "") or ""
    if base and (host == base or host.endswith("." + base)):
        return True
    from .store_resolver import binding_for_host

    return binding_for_host(host)[0] is not None


def trusted_base_url(request, project=None) -> str:
    """A ``scheme://host`` safe to embed in generated content (sitemap, robots,
    e-mail links). ``ALLOWED_HOSTS = ['*']`` lets a forged Host reach the app;
    reflecting ``request.get_host()`` into a cacheable document would let it
    poison that document for every visitor. Prefer a host tied to the store,
    then the platform, and only fall back to the request host when it checks
    out.

    Always HTTPS unless we're plainly in local/dev.
    """
    scheme = "https" if request.is_secure() else "http"
    req_host = normalize_host(request.get_host())

    project = project or getattr(request, "project", None)
    host = ""
    if project is not None:
        host = normalize_host(getattr(project, "primary_domain", "") or "")
        if not host:
            from apps.projects.models import Domain

            host = normalize_host(
                Domain.objects.filter(project=project, is_verified=True)
                .values_list("host", flat=True)
                .first()
                or ""
            )
    if not host and _host_is_known(req_host):
        host = req_host
    if not host:
        host = (
            getattr(settings, "PLATFORM_BASE_DOMAIN", "")
            or (getattr(settings, "PLATFORM_HOSTS", ()) or [""])[0]
            or req_host
        )
    return f"{scheme}://{host}"


class StorefrontHostMiddleware:
    """When the Host is a store's own domain, serve the storefront at ``/`` so
    the merchant gets clean URLs on their domain instead of the ``/app/`` prefix.

    Swaps ``request.urlconf`` to :mod:`config.storefront_urls` (storefront mounted
    at the root; legacy ``/app/…`` paths 301 to the root equivalent) and sets
    ``request.storefront_host`` for the skin / subscription-gate middleware.

    Must run before ``StorefrontSkinMiddleware``.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from .store_resolver import binding_for_host

        request.storefront_host = False
        host = normalize_host(request.get_host())
        platform = getattr(settings, "PLATFORM_HOSTS", ())
        if host and host not in platform:
            _, dedicated = binding_for_host(host)
            if dedicated:
                request.storefront_host = True
                request.urlconf = "config.storefront_urls"
        return self.get_response(request)
