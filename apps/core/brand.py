"""White-label brands on one deployment.

The env-configured platform (``PLATFORM_HOSTS`` / ``LEGAL_*``) is the implicit
``default`` brand. Extra brands come from ``settings.PLATFORM_BRANDS``. All
brands share the database; what differs per brand is the name, logo, legal
entity, e-mail sender, landing templates and the base domain new stores'
subdomains hang off (``<slug>.<base_domain>``).

Resolution is by request ``Host`` only, so it never touches the DB and is safe
to call from any context processor.
"""

from dataclasses import dataclass

from django.conf import settings

DEFAULT_KEY = "default"


def _bare(raw):
    h = (raw or "").strip().lower().split(":")[0].rstrip(".")
    return h[4:] if h.startswith("www.") else h


@dataclass(frozen=True)
class Brand:
    key: str
    name: str
    hosts: tuple
    base_domain: str
    legal_entity: str = ""
    contact_email: str = ""
    from_email: str = ""
    logo: str = ""
    landing_template: str = "marketing/landing.jinja"
    ad_template: str = "marketing/ad_landing.jinja"
    partners_template: str = "marketing/partners.jinja"

    @property
    def primary_host(self):
        return self.hosts[0] if self.hosts else self.base_domain

    @property
    def is_default(self):
        return self.key == DEFAULT_KEY

    def owns(self, host):
        host = _bare(host)
        if not host:
            return False
        if host in self.hosts:
            return True
        return bool(self.base_domain) and (
            host == self.base_domain or host.endswith("." + self.base_domain)
        )


def _extra_brands():
    out = []
    for raw in getattr(settings, "PLATFORM_BRANDS", None) or []:
        if not isinstance(raw, dict):
            continue
        raw_hosts = raw.get("hosts") or []
        if isinstance(raw_hosts, str):
            raw_hosts = [raw_hosts]
        hosts = tuple(h for h in (_bare(x) for x in raw_hosts) if h)
        key = (raw.get("key") or "").strip().lower()
        if not key or key == DEFAULT_KEY or not hosts:
            continue
        out.append(Brand(
            key=key,
            name=raw.get("name") or key.title(),
            hosts=hosts,
            base_domain=_bare(raw.get("base_domain")) or hosts[0],
            legal_entity=raw.get("legal_entity") or raw.get("name") or key.title(),
            contact_email=raw.get("contact_email", ""),
            from_email=raw.get("from_email", ""),
            logo=raw.get("logo", ""),
            landing_template=raw.get("landing_template") or Brand.landing_template,
            ad_template=raw.get("ad_template") or Brand.ad_template,
            partners_template=raw.get("partners_template") or Brand.partners_template,
        ))
    return out


def default_brand():
    extra_hosts = {h for b in _extra_brands() for h in b.hosts}
    hosts = tuple(
        h for h in (getattr(settings, "PLATFORM_HOSTS", None) or []) if h not in extra_hosts
    )
    base = _bare(getattr(settings, "PLATFORM_BASE_DOMAIN", "")) or (hosts[0] if hosts else "")
    name = getattr(settings, "LEGAL_ENTITY_NAME", "") or "shopinaday"
    return Brand(
        key=DEFAULT_KEY,
        name=name,
        hosts=hosts,
        base_domain=base,
        legal_entity=name,
        contact_email=getattr(settings, "LEGAL_CONTACT_EMAIL", "") or "",
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "") or "",
    )


def all_brands():
    return [default_brand(), *_extra_brands()]


def brand_by_key(key):
    key = (key or "").strip().lower()
    for b in _extra_brands():
        if b.key == key:
            return b
    return default_brand()


def brand_for_host(host):
    """The brand whose hosts / base domain ``host`` belongs to; default otherwise."""
    for b in _extra_brands():
        if b.owns(host):
            return b
    return default_brand()


def brand_for_request(request):
    cached = getattr(request, "_brand", None)
    if cached is None:
        cached = brand_for_host(request.get_host())
        request._brand = cached
    return cached


def platform_domains():
    """Every apex a platform page or store subdomain can live on."""
    out = set()
    for b in all_brands():
        out.update(b.hosts)
        if b.base_domain:
            out.add(b.base_domain)
    out.discard("")
    return out
