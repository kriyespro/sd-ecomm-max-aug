"""Browser-side snippets for each tracking provider.

Pure string builders — no Django, no I/O. The storefront middleware calls
``head_snippet`` (base tag, injected before ``</head>``) and ``event_snippet``
(one conversion event, injected before ``</body>``) once per enabled provider.

The view sets ``request._tracking = (name, data, event_id)`` in Meta-canonical
terms — ``ViewContent`` / ``InitiateCheckout`` / ``Purchase`` with Meta-style
``data`` keys (``content_ids``, ``content_name``, ``value``, ``currency``,
``num_items``, ``order_id``). Each provider remaps that to its own vocabulary.
``PageView`` is emitted by every base tag itself, never as an event.
"""

import json

IMPLEMENTED = ("meta", "ga4", "tiktok")

# --- base tags -----------------------------------------------------------
# ``__ID__`` / ``__PIXEL__`` are replaced with a validated id (digits, ``G-…``
# or alnum) — never with free text — so a plain str.replace is safe here.

_META_BASE = """<script>!function(f,b,e,v,n,t,s)
{if(f.fbq)return;n=f.fbq=function(){n.callMethod?
n.callMethod.apply(n,arguments):n.queue.push(arguments)};
if(!f._fbq)f._fbq=n;n.push=n;n.loaded=!0;n.version='2.0';
n.queue=[];t=b.createElement(e);t.async=!0;
t.src=v;s=b.getElementsByTagName(e)[0];
s.parentNode.insertBefore(t,s)}(window,document,'script',
'https://connect.facebook.net/en_US/fbevents.js');
fbq('init','__PIXEL__');fbq('track','PageView');</script>
<noscript><img height="1" width="1" style="display:none"
src="https://www.facebook.com/tr?id=__PIXEL__&ev=PageView&noscript=1"/></noscript>"""

_GA4_BASE = """<script async src="https://www.googletagmanager.com/gtag/js?id=__ID__"></script>
<script>window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}
gtag('js',new Date());gtag('config','__ID__');</script>"""

_TT_BASE = (
    '<script>!function (w, d, t) {w.TiktokAnalyticsObject=t;var ttq=w[t]=w[t]||[];'
    'ttq.methods=["page","track","identify","instances","debug","on","off","once",'
    '"ready","alias","group","enableCookie","disableCookie","holdConsent",'
    '"revokeConsent","grantConsent"];ttq.setAndDefer=function(t,e){t[e]=function(){'
    't.push([e].concat(Array.prototype.slice.call(arguments,0)))}};'
    'for(var i=0;i<ttq.methods.length;i++)ttq.setAndDefer(ttq,ttq.methods[i]);'
    'ttq.instance=function(t){for(var e=ttq._i[t]||[],n=0;n<ttq.methods.length;n++)'
    'ttq.setAndDefer(e,ttq.methods[n]);return e};ttq.load=function(e,n){'
    'var r="https://analytics.tiktok.com/i18n/pixel/events.js";ttq._i=ttq._i||{},'
    'ttq._i[e]=[],ttq._i[e]._u=r,ttq._t=ttq._t||{},ttq._t[e]=+new Date,'
    'ttq._o=ttq._o||{},ttq._o[e]=n||{};var s=document.createElement("script");'
    's.type="text/javascript",s.async=!0,s.src=r+"?sdkid="+e+"&lib="+t;'
    'var a=document.getElementsByTagName("script")[0];a.parentNode.insertBefore(s,a)};'
    'ttq.load("__ID__");ttq.page();}(window,document,"ttq");</script>'
)


def _js(value):
    return json.dumps(value, separators=(",", ":"))


def head_snippet(provider, cfg):
    pid = cfg["pixel_id"]
    if provider == "meta":
        return _META_BASE.replace("__PIXEL__", pid)
    if provider == "ga4":
        return _GA4_BASE.replace("__ID__", pid)
    if provider == "tiktok":
        return _TT_BASE.replace("__ID__", pid)
    return ""


# --- per-page conversion events ---------------------------------------------

_META_EVENTS = {"ViewContent", "InitiateCheckout", "Purchase"}
_GA4_EVENTS = {"ViewContent": "view_item", "InitiateCheckout": "begin_checkout",
               "Purchase": "purchase"}
_TT_EVENTS = {"ViewContent": "ViewContent", "InitiateCheckout": "InitiateCheckout",
              "Purchase": "CompletePayment"}


def _ga4_params(name, data, event_id):
    p = {}
    if data.get("value") is not None:
        p["value"] = data["value"]
    if data.get("currency"):
        p["currency"] = data["currency"]
    ids = data.get("content_ids") or []
    if ids:
        p["items"] = [{"item_id": str(i)} for i in ids]
        if data.get("content_name") and len(ids) == 1:
            p["items"][0]["item_name"] = data["content_name"]
    if name == "Purchase":
        p["transaction_id"] = str(data.get("order_id") or event_id or "")
    return p


def _tt_props(data):
    props = {}
    if data.get("value") is not None:
        props["value"] = data["value"]
    if data.get("currency"):
        props["currency"] = data["currency"]
    ids = data.get("content_ids") or []
    if ids:
        props["contents"] = [{"content_id": str(i)} for i in ids]
        props["content_type"] = data.get("content_type") or "product"
    return props


def event_snippet(provider, cfg, name, data, event_id):
    data = data or {}
    if provider == "meta":
        if name not in _META_EVENTS:
            return ""
        opts = {"eventID": str(event_id)} if event_id else {}
        return (f"<script>window.fbq&&fbq('track',{_js(name)},"
                f"{_js(data)},{_js(opts)});</script>")
    if provider == "ga4":
        ev = _GA4_EVENTS.get(name)
        if not ev:
            return ""
        return (f"<script>window.gtag&&gtag('event',{_js(ev)},"
                f"{_js(_ga4_params(name, data, event_id))});</script>")
    if provider == "tiktok":
        ev = _TT_EVENTS.get(name)
        if not ev:
            return ""
        opts = {"event_id": str(event_id)} if event_id else {}
        return (f"<script>window.ttq&&ttq.track({_js(ev)},"
                f"{_js(_tt_props(data))},{_js(opts)});</script>")
    return ""
