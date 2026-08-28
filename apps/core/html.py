"""Sanitise staff-authored rich HTML before it is stored.

CMS page bodies, product descriptions, FAQ answers etc. are rendered to the
storefront with autoescaping OFF (``| safe``). A store-staff account — or a
compromised one — could otherwise store ``<script>`` and get stored XSS on the
store's own domain. Every write path runs through :func:`sanitize_html` in the
model's ``save()``, so the value in the database is always safe to emit raw.
"""

import nh3

# Tags a marketing/CMS editor legitimately needs. No <script>, <style>,
# <iframe>, <object>, <form>, <input>, <link>, <meta>, event handlers, etc.
_ALLOWED_TAGS = {
    "p", "br", "hr", "span", "div",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "strong", "b", "em", "i", "u", "s", "sub", "sup", "small", "mark",
    "blockquote", "cite", "q", "abbr", "code", "pre", "kbd",
    "ul", "ol", "li", "dl", "dt", "dd",
    "a", "img", "figure", "figcaption", "picture", "source",
    "table", "thead", "tbody", "tfoot", "tr", "th", "td", "caption",
    "colgroup", "col",
}

# `style` is deliberately NOT allowed — nh3 does not sanitise CSS, so inline
# styles are an exfiltration / legacy-expression risk. Content uses classes.
_ALLOWED_ATTRS = {
    "*": {"class", "id", "title", "dir", "lang"},
    "a": {"href", "target"},
    "img": {"src", "srcset", "alt", "width", "height", "loading", "decoding"},
    "source": {"src", "srcset", "type", "media", "sizes"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan", "scope"},
    "col": {"span"},
    "colgroup": {"span"},
    "ol": {"start", "type"},
}

# Only these URL schemes survive on href/src (blocks javascript:, data: bombs).
_ALLOWED_SCHEMES = {"http", "https", "mailto", "tel"}


def sanitize_html(value: str) -> str:
    """Return ``value`` with disallowed tags / attributes / URL schemes and all
    HTML comments stripped. Idempotent; blank input passes straight through.
    """
    if not value or not value.strip():
        return value or ""
    return nh3.clean(
        value,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRS,
        url_schemes=_ALLOWED_SCHEMES,
        link_rel="noopener nofollow ugc",
        strip_comments=True,
    )
