"""Paid-traffic landing pages (Google Ads / Meta Ads).

One template (``marketing/ad_landing.jinja``), one content entry per slug. Every
page has exactly one call to action — "Start free trial" -> ``/accounts/signup/``
— and no outbound navigation, so a click that cost money is never leaked.

Adding a page = adding a ``PAGES`` entry; the URL conf iterates ``PAGES``.

Attribution: the CTA href carries the ad's ``utm_*`` / ``gclid`` / ``fbclid`` /
``ref`` query params plus ``lp=<slug>`` through to the signup view, which
stashes them in the session and later on ``Project.signup_source``. The landing
page itself therefore touches no session/cookie and stays CDN-cacheable.
"""

from __future__ import annotations

from urllib.parse import urlencode

# Params copied from the ad click onto the signup link and, later, the store.
ATTRIBUTION_KEYS = (
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "gclid", "fbclid", "ttclid", "msclkid",
)
_MAX_VALUE = 200

# Shared, page-agnostic proof points. Every claim here maps to a shipped feature.
COMMON_FEATURES = [
    ("Get paid online", "Razorpay checkout with UPI, cards and netbanking, plus Cash on Delivery for buyers who prefer it."),
    ("Your own domain, HTTPS included", "Connect a domain you already own or start on a free shopinaday address. SSL is automatic."),
    ("Looks great on phones", "Most of your buyers shop on mobile. Every theme is built mobile-first and loads fast on 4G."),
    ("Run it from your phone too", "Add products, take orders and see today's sales from a dashboard that works on a small screen."),
    ("WhatsApp order updates", "Confirm and update orders over WhatsApp, the channel your customers already answer."),
    ("Write product copy with AI", "Stuck on descriptions? One click drafts them for you. Edit and publish."),
]

STEPS = [
    ("Sign in with Google", "One click. No form to fill and no card needed."),
    ("Name your store", "Pick a name, add your mobile number, choose a theme."),
    ("Add products and share the link", "Upload photos or import a spreadsheet, then start taking orders."),
]

# Optional per page: "testimonials": [(quote, name, role), ...]. Left empty on
# purpose — add only real, permissioned quotes; the section hides itself when
# there are none.
PAGES: dict[str, dict] = {
    "online-store-builder": {
        "title": "Online Store Builder for India | Start Free Trial | shopinaday",
        "description": "Build your online store in a day. Razorpay payments, your own domain, ready-made themes and an easy dashboard. Start your free trial. No card needed.",
        "eyebrow": "Online store builder for India",
        "headline": "Build your online store",
        "accent": "in a day, not a month",
        "sub": "Add your products, connect payments and share your store link today. No developer, no servers, no setup fees.",
        "checks": ["Razorpay + Cash on Delivery", "Free HTTPS store address", "Use your own domain"],
        "spot_title": "Everything you need to start selling",
        "spot_sub": "One platform instead of five tools stitched together.",
        "spot": [
            ("Ready-made themes", "Pick a look, add your logo and colours, and you are live. Switch themes any time without losing products."),
            ("Bulk import", "Moving from another platform or a spreadsheet? Import products from CSV or Excel in minutes."),
            ("Coupons and discounts", "Run festive offers and first-order codes without installing anything."),
            ("Sell with confidence", "Order tracking, customer accounts and a clean checkout that buyers trust."),
        ],
        "faq": [
            ("Is the trial really free?", "Yes. Start with no card. You only pick a plan if you want to keep going after the trial."),
            ("Do I need a developer or coding skills?", "No. If you can use WhatsApp and upload a photo, you can run your store."),
            ("Can I use my own domain name?", "Yes. Connect a domain you already own, or start on a free address and add one later."),
            ("How do I receive payments?", "Connect your Razorpay account in a few minutes. You can also accept Cash on Delivery."),
        ],
        "final": "Your store could be live before dinner.",
        "mock": "storefront",
        "store_hint": "Aarav Fashions",
        "pains_title": "Why building a store feels so hard",
        "pains": [
            ("Quotes and delays from developers", "Pick a theme and add products yourself. Live the same day."),
            ("Payments, hosting and SSL to piece together", "Razorpay, COD, hosting and HTTPS are already included."),
            ("Every small change costs money or time", "Edit prices, photos and pages yourself in minutes."),
        ],
        "compare_head": ("Hiring a developer", "shopinaday"),
        "compare": [
            ("Time to launch", "Weeks of back-and-forth", "Same day"),
            ("Upfront cost", "Developer fees", "Free trial, no card"),
            ("Payments", "Integrate and test yourself", "Razorpay + COD ready"),
            ("Hosting and HTTPS", "You arrange and renew", "Included, automatic"),
            ("Making changes", "Wait for a developer", "Do it yourself"),
        ],
    },
    "instagram-sellers": {
        "title": "Online Store for Instagram Sellers | Start Free Trial | shopinaday",
        "description": "Selling on Instagram DMs? Put your products in a real store with a link for your bio. Payments, COD and order tracking. Free trial, no card.",
        "eyebrow": "For Instagram sellers",
        "headline": "Stop selling from DMs.",
        "accent": "Put a store in your bio.",
        "sub": "Give customers one link to browse, pay and track orders. No more screenshots of prices, no more chasing payment.",
        "checks": ["A store link for your bio", "UPI + Cash on Delivery", "Orders and WhatsApp updates"],
        "spot_title": "Built for the way Instagram sellers work",
        "spot_sub": "Fewer DMs to answer. More orders that actually get paid.",
        "spot": [
            ("One link, whole catalogue", "Share your store in your bio and stories. Buyers pick the item, size and colour themselves."),
            ("Get paid before you ship", "UPI and cards at checkout cut the 'will pay tomorrow' chats. Keep COD for buyers who need it."),
            ("Show your Instagram feed", "Show your latest posts inside your store so it feels like your brand, not a stranger's website."),
            ("Track every order", "See who ordered what, what is paid and what to ship, all in one list instead of scrolling chats."),
        ],
        "faq": [
            ("Can I use this instead of taking orders in DMs?", "Yes. Customers order and pay on your store, and you get every order in one dashboard."),
            ("Do my followers need to sign up?", "No. They can check out as guests in a few taps."),
            ("Will it work on Instagram's in-app browser?", "Yes. Stores are built mobile-first and load fast on regular mobile data."),
            ("Is it free to try?", "Yes. Start free, no card needed, and bring your products over when you are ready."),
        ],
        "final": "Turn your followers into paying customers.",
        "mock": "bio",
        "store_hint": "Ria's Closet",
        "pains_title": "DM selling is costing you orders",
        "pains": [
            ("Answering 'price?' and 'size?' fifty times a day", "Prices, sizes and photos live on your store. Buyers pick for themselves."),
            ("'Will pay tomorrow' and screenshots that never arrive", "UPI and cards at checkout, or COD when a buyer needs it."),
            ("Orders buried in chats", "Every order lands in one list with its payment status."),
        ],
        "compare_head": ("Selling in DMs", "Your shopinaday store"),
        "compare": [
            ("Taking orders", "Back-and-forth chats", "Customers order themselves"),
            ("Getting paid", "Screenshots and follow-ups", "UPI, cards or COD at checkout"),
            ("Catalogue", "Scroll the grid to find a price", "Searchable, with sizes and colours"),
            ("Tracking", "Notes and memory", "All orders in one dashboard"),
            ("Brand", "Looks informal", "Your own store link"),
        ],
    },
    "ecommerce-website-india": {
        "title": "Ecommerce Website for India | Razorpay, COD, UPI | shopinaday",
        "description": "Launch an ecommerce website built for India: Razorpay and UPI, Cash on Delivery, your own domain with HTTPS, rupee pricing. Free trial, no card.",
        "eyebrow": "Ecommerce website for India",
        "headline": "An ecommerce website",
        "accent": "built for Indian buyers",
        "sub": "UPI, cards and Cash on Delivery at checkout, prices in rupees, and a store that loads fast on mobile data.",
        "checks": ["UPI, cards and COD", "Prices in rupees", "Your domain, HTTPS included"],
        "spot_title": "Made for how India shops",
        "spot_sub": "The details that decide whether a visitor becomes a buyer.",
        "spot": [
            ("Pay the way buyers prefer", "Razorpay brings UPI, cards and netbanking. Cash on Delivery stays one tap away."),
            ("Fast on 4G", "Lightweight pages and optimised images so your store opens quickly, even on a slow connection."),
            ("Found on Google", "Titles, descriptions, sitemap and structured data are set up for you."),
            ("Know your numbers", "Today's sales, orders to ship and low-stock items on your dashboard, plus live visitor counts."),
        ],
        "faq": [
            ("Does it support UPI and Cash on Delivery?", "Yes. Connect Razorpay for UPI, cards and netbanking, and switch on Cash on Delivery if you want it."),
            ("Can I connect my own .com or .in domain?", "Yes, with HTTPS included. We walk you through the DNS step."),
            ("How long does setup take?", "Most sellers have a store with products live the same day."),
            ("What does it cost after the trial?", "Plans start low and you can see them before you commit. The trial needs no card."),
        ],
        "final": "Launch your ecommerce website today.",
        "mock": "checkout",
        "store_hint": "Kaveri Handlooms",
        "pains_title": "Generic stores miss how India buys",
        "pains": [
            ("Buyers drop off when their payment method is missing", "UPI, cards, netbanking and COD at checkout."),
            ("Slow pages on mobile data", "Light, mobile-first pages with optimised images."),
            ("Customers asking 'where is my order?'", "WhatsApp order updates keep them informed."),
        ],
        "compare_head": ("A generic global store", "shopinaday"),
        "compare": [
            ("Checkout", "Cards first, local methods extra", "UPI, cards, netbanking, COD"),
            ("Prices", "Currency settings to configure", "Rupees by default"),
            ("Order updates", "Email only", "WhatsApp updates"),
            ("Mobile speed", "Depends on the plugins", "Built mobile-first"),
            ("Support for India", "Generic", "Built around Indian sellers"),
        ],
    },
    "shopify-alternative": {
        "title": "Shopify Alternative for India | Free Trial | shopinaday",
        "description": "Looking for a Shopify alternative made for India? Rupee pricing, Razorpay, COD, WhatsApp updates and local support. Try free, no card needed.",
        "eyebrow": "A Shopify alternative made for India",
        "headline": "The online store platform",
        "accent": "that speaks India",
        "sub": "Rupee pricing, Razorpay, UPI, COD and WhatsApp updates are part of the platform, not a pile of paid add-ons.",
        "checks": ["Priced in rupees", "India payments built in", "Switch with a bulk import"],
        "spot_title": "Why sellers in India switch",
        "spot_sub": "Same idea, built around how Indian stores actually run.",
        "spot": [
            ("India-first checkout", "UPI, cards and Cash on Delivery with Razorpay, ready from day one."),
            ("WhatsApp in the flow", "Order confirmations and updates go where your customers actually read them."),
            ("Bring your catalogue", "Import products from a CSV or Excel export and match images from your media library."),
            ("Simple dashboard", "A trimmed Easy mode shows only what a new seller needs. Switch to the full view when you grow."),
        ],
        "faq": [
            ("Can I move my existing products over?", "Yes. Export your products to CSV or Excel and import them in bulk."),
            ("Will my customers see a difference?", "They see a fast, mobile-friendly store with the same familiar cart and checkout."),
            ("Do I need a credit card to try it?", "No. The trial is free and needs no card."),
            ("Can I keep my domain?", "Yes. Connect the domain you already own once you are ready to go live."),
        ],
        "final": "Try it free alongside your current store.",
        "mock": "storefront",
        "store_hint": "Urban Weave",
        "pains_title": "What to check before you pick a platform",
        "pains": [
            ("Payments and COD that need extra setup", "Razorpay and Cash on Delivery are ready from day one."),
            ("A long list of add-ons for basics", "Bulk import, coupons, SEO, pixels and an AI writer are built in."),
            ("A dashboard that overwhelms new sellers", "Easy mode shows only what you need. Switch to the full view later."),
        ],
        "compare_head": ("Check any platform for", "shopinaday"),
        "compare": [
            ("UPI + Razorpay checkout", "Ask", "Included"),
            ("Cash on Delivery", "Ask", "Included"),
            ("WhatsApp order updates", "Ask", "Included"),
            ("Meta, Google and TikTok pixels", "Ask", "Included"),
            ("Bulk import from CSV or Excel", "Ask", "Included"),
        ],
    },
    "fashion-store": {
        "title": "Start a Fashion Store Online | Free Trial | shopinaday",
        "description": "Launch your fashion or clothing brand online. Size and colour variants, fashion-ready themes, UPI and COD, your own domain. Free trial, no card.",
        "eyebrow": "For fashion and clothing brands",
        "headline": "Launch your fashion store",
        "accent": "with sizes, colours and style",
        "sub": "Fashion-ready themes, size and colour options, and checkout your buyers already trust. Be live this week.",
        "checks": ["Size and colour variants", "Fashion-ready themes", "UPI + Cash on Delivery"],
        "spot_title": "Made for clothing, boutiques and jewellery",
        "spot_sub": "The features fashion stores need, without the clutter.",
        "spot": [
            ("Size and colour variants", "Set up S to XXL and every colour in one product. Buyers pick, you track stock per option."),
            ("Themes with a fashion look", "Editorial layouts, lookbook banners and festive themes that make your photos the hero."),
            ("Collections that sell", "Shop by category, budget or festive edit, and highlight new arrivals on your home page."),
            ("Jewellery trust tools", "Upload lab certificates and let buyers verify them with a code on your store."),
        ],
        "faq": [
            ("Can I sell products with sizes and colours?", "Yes. Add sizes and colours once and each combination is tracked separately."),
            ("Can I show lookbooks and offers on the home page?", "Yes. Use banners, collections and promo sections to feature your latest edit."),
            ("Is it suitable for jewellery?", "Yes. There is a certificate verification page for lab-certified pieces."),
            ("How do I start?", "Sign in with Google, name your store and add your first product. The trial is free."),
        ],
        "final": "Your next collection deserves its own store.",
        "mock": "fashion",
        "store_hint": "Nisha Boutique",
        "pains_title": "Selling clothes online is harder than it looks",
        "pains": [
            ("A separate listing for every size and colour", "One product with all its sizes and colours."),
            ("Not knowing which sizes are left", "Stock tracked per size and colour."),
            ("A generic template that hides your photos", "Fashion-ready themes that put your photos first."),
        ],
        "compare_head": ("A basic store", "shopinaday for fashion"),
        "compare": [
            ("Sizes and colours", "Separate listings", "One product, all options"),
            ("Stock", "Guesswork", "Tracked per option"),
            ("Look", "Generic template", "Fashion-ready themes"),
            ("Offers", "Manual", "Coupons and festive banners"),
            ("Jewellery trust", "Not covered", "Certificate verification page"),
        ],
    },
}


def attribution_from(querydict) -> dict:
    """Pick the whitelisted ad params off a QueryDict, trimmed and non-empty."""
    out = {}
    for key in ATTRIBUTION_KEYS:
        val = (querydict.get(key) or "").strip()[:_MAX_VALUE]
        if val:
            out[key] = val
    return out


def signup_href(base: str, querydict, slug: str) -> str:
    """The CTA target: signup URL carrying attribution, ``ref`` and ``lp``."""
    params = attribution_from(querydict)
    ref = (querydict.get("ref") or "").strip()[:16]
    if ref:
        params["ref"] = ref
    params["lp"] = slug
    return f"{base}?{urlencode(params)}"


def cta_params(querydict, slug: str) -> dict:
    """The same attribution as hidden form fields for the hero's store-name form."""
    params = attribution_from(querydict)
    ref = (querydict.get("ref") or "").strip()[:16]
    if ref:
        params["ref"] = ref
    params["lp"] = slug
    return params
