"""On-page guided tours — a short spotlight walkthrough for the handful of
screens where a new person most needs orienting. Keyed by the view's
``url_name`` (``request.resolver_match.url_name``), same identifier
navigation.py uses.

Each step targets a real element via ``data-tour="<target>"`` in that page's
template — see ``static/site/tour.js`` for the engine. Master switch:
``Profile.show_guides``. Not gated by Easy/Expert mode — guides are for
every role, tours replay on every visit while the switch is on (no per-page
"seen" tracking — one switch is easier to reason about than fifty).
"""

TOURS = {
    "dashboard": [
        {"target": "quick-launch", "title": "Your checklist",
         "text": "Work through this list — each item links straight to "
                 "where you finish it."},
        {"target": "today-stats", "title": "Today's numbers",
         "text": "Once you're live, sales, orders and low stock show up "
                 "here every day."},
    ],
    "product_list": [
        {"target": "product-new-btn", "title": "Add a product",
         "text": "Start here — a title, price and one photo is all you "
                 "need to publish an item."},
        {"target": "product-import-btn", "title": "Bulk import",
         "text": "Selling a lot of items? Import them from a spreadsheet "
                 "instead of adding them one by one."},
    ],
    "cms_store_profile": [
        {"target": "profile-logo", "title": "Your logo",
         "text": "Shown in your storefront header and footer — a square "
                 "image works best."},
        {"target": "profile-contact", "title": "Contact details",
         "text": "Customers see this in your storefront footer and on "
                 "order emails."},
    ],
    "payment_providers": [
        {"target": "payment-add", "title": "Connect a payment method",
         "text": "Cash on delivery is the fastest way to start — add "
                 "Razorpay later to accept online payments too."},
    ],
    "shipping_zones": [
        {"target": "shipping-new", "title": "Add a shipping zone",
         "text": "A zone is a set of regions (e.g. \"All India\") — add one, "
                 "then attach a rate or a free-shipping minimum to it."},
    ],
    "order_list": [
        {"target": "order-filters", "title": "Filter your orders",
         "text": "Jump straight to what needs packing, shipping or a "
                 "refund."},
        {"target": "order-row", "title": "Open an order",
         "text": "Click any row for the full order — items, address, "
                 "payment and fulfilment actions."},
    ],
    "coupon_list": [
        {"target": "coupon-new", "title": "Create a coupon",
         "text": "Percent off, a flat amount, or free shipping — set a code "
                 "customers type at checkout."},
    ],
    "domains": [
        {"target": "domain-add", "title": "Connect your own domain",
         "text": "Paste a hostname you own — you'll get DNS records to add "
                 "at your registrar, then verify."},
    ],
    "team": [
        {"target": "team-add", "title": "Add a team member",
         "text": "Enter their email and pick a role — they get a one-time "
                 "password to sign in with."},
    ],
    "cms_theme": [
        {"target": "theme-colors", "title": "Your brand colours",
         "text": "These drive every button, link and accent across your "
                 "storefront — no code required."},
        {"target": "theme-section-order", "title": "Reorder your home page",
         "text": "Move sections up or down to change what shoppers see "
                 "first."},
    ],
    "stores": [
        {"target": "stores-add", "title": "Add a store",
         "text": "Set up a new store for a client — you become its DGC "
                 "and earn commission on it."},
        {"target": "stores-list", "title": "Your stores",
         "text": "Everything you manage lives here — click one to switch "
                 "into it."},
    ],
}


def tour_for(url_name):
    return TOURS.get(url_name)
