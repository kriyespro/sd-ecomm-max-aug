"""Seed 10 SEO-targeted launch posts for the platform blog.

Idempotent — matches on title, so re-running (e.g. after editing copy here)
updates existing rows instead of duplicating them. Run locally or on the
server:

    python manage.py seed_blog_posts
"""

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.utils.text import slugify

from apps.blog.models import BlogAuthor, BlogCategory, BlogPost, BlogTag

AUTHOR = {
    "name": "Shopinaday Team",
    "role": "Editorial",
    "bio": "Notes from the team building Shopinaday — practical guides for "
           "Indian sellers, agencies and DGCs running stores on the platform.",
}

CATEGORIES = ["Getting Started", "Payments & Operations", "Growth & Marketing",
              "For Agencies", "Compliance"]

POSTS = [
    {
        "title": "How to Start an Online Store in India in 2026 (Step-by-Step)",
        "category": "Getting Started",
        "tags": ["ecommerce-india", "getting-started"],
        "excerpt": "A practical, no-fluff walkthrough of launching an online store in India — "
                   "from picking a product to taking your first payment.",
        "seo_description": "Step-by-step guide to starting an online store in India in 2026: "
                            "product, domain, payments, and your first sale — no coding needed.",
        "body": """
<p>Starting an online store in India used to mean hiring a developer, wrestling with a payment
gateway integration, and waiting weeks before you could take a single order. That's no longer
true. Here's the realistic, current path — the one most new sellers actually follow.</p>

<h2>1. Pick a product you can fulfil reliably</h2>
<p>Before anything technical, nail down what you're selling and how you'll ship it. The stores
that struggle in month one almost always have a sourcing or fulfilment gap, not a technology one.
If you're not sure yet, start narrow — one category, 10–20 SKUs — and expand once orders start
coming in.</p>

<h2>2. Choose a platform that doesn't need a developer</h2>
<p>You want: a storefront theme you can swap without rebuilding, your own payment gateway account
(so payouts land directly in your bank, not a third party's), and a dashboard for orders, stock
and customers. <a href="/pricing/">Shopinaday</a> gives you all three from day one, with a
7-day free trial and no card required to start.</p>

<h2>3. Add your products and set up your storefront</h2>
<p>Upload products individually or bulk-import a CSV/Excel sheet if you already have a catalogue
elsewhere. Pick a theme from the built-in skin library, add your logo and store details, and your
storefront is live on a free subdomain immediately.</p>

<h2>4. Connect your own domain and payments</h2>
<p>Point your own domain at your store — it verifies automatically and HTTPS is handled for you.
Connect your own Razorpay account so payments and payouts go straight to your bank account, with
UPI, cards, netbanking and COD available to shoppers out of the box.</p>

<h2>5. Take your first order</h2>
<p>Share your store link, run a small ad, or post it to your WhatsApp Status. The real learning
starts once real orders come in — delivery timelines, return requests, repeat customers. Everything
else is a template; this part is yours to get right.</p>

<p>Ready to see it running? <a href="/accounts/signup/">Start your free trial</a> — most sellers
have a live, shareable store link within the hour.</p>
""",
    },
    {
        "title": "Shopify Alternative for India: Why Sellers Are Switching to Shopinaday",
        "category": "Getting Started",
        "tags": ["shopify-alternative", "ecommerce-india"],
        "excerpt": "Shopify is built for a global, USD-first market. Here's what an India-first "
                   "alternative gets right that general platforms don't.",
        "seo_description": "Looking for a Shopify alternative built for India? Compare pricing, "
                            "Razorpay-native payments, COD, and WhatsApp selling before you decide.",
        "body": """
<p>Shopify is a fine platform — if you're selling in dollars, to a market with mature card
penetration, and happy to add third-party apps for things that should be built in. For a seller
whose customers pay by UPI and COD, and whose growth channel is WhatsApp, the fit is less obvious.
Here's the honest comparison.</p>

<h2>Payments: native vs. bolted-on</h2>
<p>On Shopify, Indian sellers typically add Razorpay or similar as a third-party payment app,
paying platform fees on top of gateway fees. On <a href="/pricing/">Shopinaday</a>, Razorpay is
the checkout — you connect your own account, keys live in your dashboard, and payouts go straight
to your bank. There's no markup on a payment method that's already the default for most of your
customers.</p>

<h2>Cash on Delivery, properly supported</h2>
<p>COD still accounts for a large share of orders in many categories in India. It isn't an
afterthought here — it's a first-class checkout option alongside prepaid, with order management
built around the reality that some orders get returned at the door.</p>

<h2>WhatsApp as a selling channel, not just a support tool</h2>
<p>Plenty of Indian sellers run their entire business through WhatsApp Catalogue before they ever
need a full storefront. Shopinaday's catalogue-only mode lets you run exactly that — products with
an enquiry button, no cart required — and upgrade to full checkout later without starting over.</p>

<h2>Pricing that makes sense for a starting seller</h2>
<p>General-purpose platforms price for a global market. See the <a href="/pricing/">pricing
page</a> for plans built around what an Indian store actually needs at each stage — and start with
a 7-day trial, no card required.</p>

<h2>What you'd give up</h2>
<p>Shopify's app ecosystem is enormous — if you need a very specific niche integration, it's more
likely to already exist there. For most sellers whose needs are "sell products, take Indian
payments, run WhatsApp marketing, use my own domain," that trade-off isn't worth the extra
cost and friction.</p>

<p><a href="/accounts/signup/">Try Shopinaday free for 7 days</a> and compare it against your
current setup directly.</p>
""",
    },
    {
        "title": "How Much Does It Cost to Start an Ecommerce Store in India?",
        "category": "Getting Started",
        "tags": ["ecommerce-india", "pricing"],
        "excerpt": "A realistic cost breakdown for launching an online store in India — platform, "
                   "domain, payment gateway, and the costs people forget.",
        "seo_description": "What does it really cost to start an online store in India? Platform "
                            "fees, domain, payment gateway charges, and hidden costs, explained.",
        "body": """
<p>"How much will this cost me?" is the first question every new seller asks, and the honest
answer is: less than most people expect for the software, and more than most people expect for
everything around it. Here's the full picture.</p>

<h2>Platform cost</h2>
<p>A hosted platform like Shopinaday charges a monthly subscription instead of a one-time build
cost — see the exact tiers on the <a href="/pricing/">pricing page</a>. Compare that against
hiring a developer to build and maintain a custom site, which costs far more upfront and still
needs ongoing maintenance.</p>

<h2>Domain name</h2>
<p>A custom domain (yourstore.com) typically costs a small annual fee from any registrar. You can
also launch on a free subdomain first and add your own domain later — it's a one-click connection
once you're ready, with HTTPS handled automatically.</p>

<h2>Payment gateway fees</h2>
<p>Razorpay and similar gateways charge a percentage per transaction — this is standard across
every platform, not specific to any one of them. Because you connect your own account, you see
exactly what you're paying with no additional markup from the storefront platform.</p>

<h2>The costs people forget</h2>
<ul>
<li><strong>Product photography</strong> — often the single biggest early investment, and the
one that most affects conversion.</li>
<li><strong>Packaging</strong> — branded packaging costs more per order but materially changes
repeat-purchase rates.</li>
<li><strong>Shipping and returns</strong> — courier rates, and the cost of handling COD return
rates, which can be significant depending on category.</li>
<li><strong>Marketing</strong> — even organic growth (WhatsApp, Instagram) takes time that has a
real cost; paid ads are an additional line item once you're ready to scale.</li>
</ul>

<h2>The realistic minimum</h2>
<p>You can have a live, payment-ready store for the cost of one month's subscription plus a
domain — everything else (photography, stock, marketing) scales with how seriously you're
running the business, not with the software. <a href="/accounts/signup/">Start a free 7-day
trial</a> to see the real dashboard before committing to anything.</p>
""",
    },
    {
        "title": "WhatsApp Catalogue Store: Sell on WhatsApp Without Building a Website First",
        "category": "Growth & Marketing",
        "tags": ["whatsapp-selling", "catalogue-mode"],
        "excerpt": "You don't need a full storefront to start selling. Here's how catalogue-only, "
                   "enquiry-first selling on WhatsApp works — and when to upgrade.",
        "seo_description": "How to run a WhatsApp catalogue store: enquiry-only selling, no cart "
                            "needed, and how to upgrade to full checkout when you're ready.",
        "body": """
<p>A huge number of Indian sellers start on WhatsApp — a product photo in a Status update, a
customer DM, a manual order written down somewhere. It works, until it doesn't scale. The
question isn't "website or WhatsApp" — it's how to combine both properly.</p>

<h2>What catalogue-only mode actually does</h2>
<p>Switch a Shopinaday store into catalogue mode and the Add to Cart / Buy Now buttons disappear
everywhere, replaced by a WhatsApp Enquiry button, pre-filled with the product name, price and a
link back to the product page. Customers browse a real storefront — categories, search, photos —
then enquire the way they already prefer to.</p>

<h2>Why this beats a plain WhatsApp catalogue</h2>
<ul>
<li><strong>Search and categories</strong> — a customer scrolling 200 WhatsApp catalogue items
one by one will give up; a searchable storefront won't lose them.</li>
<li><strong>Shareable, SEO-indexable product pages</strong> — each product has its own URL that
can rank on Google and be shared directly, something a closed WhatsApp catalogue can't offer.</li>
<li><strong>A record of every enquiry</strong> — instead of losing track of DMs, every enquiry
traces back to a specific product and customer.</li>
</ul>

<h2>Price-on-request, if you need it</h2>
<p>Some categories — custom jewellery, bespoke furniture, bulk orders — genuinely need a
conversation before a price is quoted. Catalogue mode supports hiding price entirely, so the
enquiry starts the conversation instead of a number doing it.</p>

<h2>When to turn checkout back on</h2>
<p>The moment your products have a fixed, non-negotiable price and repeat demand, full checkout
(Razorpay + COD) usually converts better than a manual back-and-forth for every order. The switch
is a single toggle — you don't rebuild the store to make it.</p>

<p><a href="/accounts/signup/">Start free</a> and try catalogue mode on your own store today —
no card required for the first 7 days.</p>
""",
    },
    {
        "title": "Best Payment Gateway for Small Businesses in India: What to Actually Compare",
        "category": "Payments & Operations",
        "tags": ["razorpay", "payments"],
        "excerpt": "Transaction fees aren't the whole story. Here's what actually matters when "
                   "choosing a payment gateway for a small online store in India.",
        "seo_description": "Choosing a payment gateway in India? Compare settlement speed, UPI "
                            "support, COD handling, and who actually owns your payment account.",
        "body": """
<p>Every payment gateway comparison online leads with the headline transaction fee percentage,
which is the least useful number for a small seller to optimise. Here's what actually changes
your day-to-day experience.</p>

<h2>Who owns the account — you, or the platform?</h2>
<p>Some ecommerce platforms route payments through their own merchant account and pay you out
later, adding a delay and a dependency. On Shopinaday, you connect your own Razorpay account
directly — payouts settle straight to your bank on Razorpay's own schedule, with no platform
sitting in between.</p>

<h2>UPI support isn't optional anymore</h2>
<p>For most Indian consumer categories, UPI is now the default payment method, ahead of cards.
Any gateway you pick needs to support it natively and reliably — not as an afterthought bolted
onto a card-first checkout.</p>

<h2>Cash on Delivery handling</h2>
<p>COD isn't really a "payment gateway" feature, but how your storefront manages COD orders —
confirmation calls, return/RTO tracking, reconciling what actually got collected — matters as
much as the online payment experience. Look for a platform where COD orders live in the same
dashboard as prepaid ones, not a separate workflow.</p>

<h2>Settlement speed</h2>
<p>Ask specifically how fast money lands in your bank account after a sale, not just what the fee
is. A slightly higher fee with next-day settlement can be worth more to a cash-flow-sensitive
small business than a marginally lower fee with a week's delay.</p>

<h2>Setting it up on Shopinaday</h2>
<p>Add your Razorpay Key ID and Key Secret in <strong>Settings → Payments</strong> — no raw JSON,
no code. Both online payment and COD are available to shoppers from the moment it's saved.
<a href="/accounts/signup/">Start your trial</a> to see the dashboard before connecting a live
account.</p>
""",
    },
    {
        "title": "How to Add a Custom Domain to Your Online Store (Step-by-Step)",
        "category": "Payments & Operations",
        "tags": ["custom-domain", "getting-started"],
        "excerpt": "Move your store off a subdomain and onto yourstore.com — here's exactly how, "
                   "and why it matters for trust and SEO.",
        "seo_description": "How to connect a custom domain to your online store: DNS setup, "
                            "verification, and HTTPS — a step-by-step guide for non-technical sellers.",
        "body": """
<p>A store on <code>yourbrand.shopinaday.com</code> works perfectly well technically — but a
customer trusts <code>yourbrand.com</code> more, and it's the version worth printing on packaging
and sharing on Instagram. Here's how to make the switch.</p>

<h2>1. Buy the domain, if you haven't already</h2>
<p>Any domain registrar works. If your brand name is taken as a <code>.com</code>, a
<code>.in</code> or a close variant is a perfectly reasonable fallback for an India-focused
store.</p>

<h2>2. Add it in your store dashboard</h2>
<p>Under <strong>Settings → Domains</strong>, add the domain you bought. You'll get a DNS record
to add — either a simple A record, a TXT record, or a one-click Cloudflare-proxy option if your
domain is already on Cloudflare.</p>

<h2>3. Add the DNS record at your registrar</h2>
<p>This is the only slightly technical step, and it's a five-minute job even for a first-timer:
log in to your registrar (GoDaddy, Namecheap, Cloudflare, whoever you bought the domain from),
find DNS settings, and paste in the record exactly as shown.</p>

<h2>4. Wait for auto-verification</h2>
<p>Verification runs automatically every few minutes — no support ticket needed. Once it
verifies, HTTPS is provisioned for you, so your store is live on your domain with a secure padlock
with no extra steps.</p>

<h2>Why this matters beyond "it looks nicer"</h2>
<ul>
<li><strong>Trust</strong> — shoppers are more cautious about entering card details on an unknown
subdomain than on a branded domain.</li>
<li><strong>SEO</strong> — a domain you own and control compounds in search value over time, in a
way a shared platform subdomain doesn't.</li>
<li><strong>Portability</strong> — your domain is yours regardless of which platform you use
behind it.</li>
</ul>

<p>Haven't started your store yet? <a href="/accounts/signup/">Begin your free trial</a> — domain
setup takes minutes once you're in.</p>
""",
    },
    {
        "title": "Cash on Delivery vs Prepaid: What Indian Store Owners Actually See in the Data",
        "category": "Payments & Operations",
        "tags": ["cod", "ecommerce-india"],
        "excerpt": "COD drives orders but also drives returns. Here's how to think about the "
                   "trade-off instead of just picking one.",
        "seo_description": "COD vs prepaid for Indian ecommerce: conversion rates, return rates, "
                            "and a practical approach to offering both without losing money.",
        "body": """
<p>New sellers often ask whether to offer COD at all, worried about return rates. The better
question isn't "COD or prepaid" — almost every successful Indian store offers both. The real
skill is managing each well.</p>

<h2>Why COD still drives volume</h2>
<p>For first-time buyers from an unfamiliar store, COD removes the single biggest trust barrier:
paying before receiving anything. Categories like fashion, where fit and look matter, see
especially strong COD demand — customers want to see the product before committing money.</p>

<h2>Why prepaid still matters</h2>
<p>Prepaid orders have meaningfully lower return and RTO (return-to-origin) rates, and they're
cash-flow positive the moment the order is placed rather than on delivery. Incentivising
prepaid — a small discount, free shipping over prepaid orders — nudges the mix in your favour
without removing COD entirely.</p>

<h2>Managing COD return risk</h2>
<ul>
<li><strong>Confirm high-value COD orders</strong> — a quick WhatsApp confirmation before
dispatch filters out a large share of fake/impulse orders.</li>
<li><strong>Track RTO by courier and pincode</strong> — return patterns are rarely random; some
areas and couriers consistently perform worse.</li>
<li><strong>Cap COD order value</strong> — many stores cap COD above a certain order value and
require prepaid beyond it, reducing exposure on the orders that would hurt most if returned.</li>
</ul>

<h2>How this works on Shopinaday</h2>
<p>COD and Razorpay checkout sit side by side at checkout — shoppers choose, and every order
lands in the same order dashboard regardless of payment method, with status and delivery tracking
in one place. <a href="/pricing/">See plans</a> or <a href="/accounts/signup/">start your free
trial</a> to try it with your own catalogue.</p>
""",
    },
    {
        "title": "How Freelancers and Agencies Can Build and Manage Stores for Clients",
        "category": "For Agencies",
        "tags": ["agencies", "dgc"],
        "excerpt": "If you build websites or run marketing for local businesses, reselling stores "
                   "is a recurring-revenue service you can add this month.",
        "seo_description": "A guide for freelancers and agencies: how to build, manage and resell "
                            "online stores to clients with one login and your own margin.",
        "body": """
<p>If you already build websites, run social media, or manage ads for local businesses, you're
one step away from a recurring-revenue service most of your clients actually need: a proper
online store. Here's the model that works.</p>

<h2>One login, every client store</h2>
<p>Rather than juggling separate logins and separate invoices per client platform, a single
Shopinaday agency account lets you create and manage a branded store for each client from one
dashboard — switch between client stores without logging out and back in.</p>

<h2>Your own margin, invisible to the client</h2>
<p>You get your own wholesale rate on the subscription; the client sees only the plan and price
you set them at. There's no "powered by" exposure of your supplier relationship — to their
customers and to them, it's simply their store.</p>

<h2>What this actually looks like as a service</h2>
<ul>
<li><strong>Setup package</strong> — store build, catalogue upload, domain connection, payment
gateway setup, charged as a one-time fee.</li>
<li><strong>Monthly retainer</strong> — ongoing catalogue updates, promotions, basic support,
billed recurring on top of the subscription margin.</li>
<li><strong>Growth add-ons</strong> — WhatsApp marketing setup, ad campaign management, social
auto-posting — each a further line item once the base store is live.</li>
</ul>

<h2>Why this is a better fit than building custom sites</h2>
<p>A custom-built site means every client update runs through you personally, forever. A store
built on a maintained platform means the client gets ongoing feature improvements — new themes,
new payment options, performance work — without you lifting a finger, while you keep the
recurring relationship and margin.</p>

<p>See how the agency model works end to end on the <a href="/agencies/">Agencies page</a>, or
<a href="/accounts/signup/">start a free trial</a> to build your first client store today.</p>
""",
    },
    {
        "title": "GST and Billing Basics for Indian Online Sellers (What to Set Up Early)",
        "category": "Compliance",
        "tags": ["gst", "compliance"],
        "excerpt": "Compliance isn't exciting, but getting invoicing and registration right from "
                   "day one saves real pain later. A plain-language starting point.",
        "seo_description": "GST basics for new online sellers in India: registration thresholds, "
                            "invoicing, and what to set up in your store from the start.",
        "body": """
<p>This is a starting point for understanding GST as a new online seller, not a substitute for
advice from a qualified CA — rules, thresholds and exemptions change, and your specific situation
(turnover, state, product category) affects what applies to you. Always confirm specifics with a
professional before you rely on them.</p>

<h2>Registration — when it applies</h2>
<p>GST registration requirements depend on your annual turnover and, for many online sellers, on
whether you sell through a marketplace that mandates registration regardless of turnover. If
you're running your own branded store rather than selling on a third-party marketplace, the
turnover-based thresholds generally apply — but confirm the current threshold and any
category-specific exceptions with a CA before assuming either way.</p>

<h2>Invoicing from day one</h2>
<p>Whether or not you're registered yet, get into the habit of issuing a proper invoice for every
order — order number, date, itemised products, amount, and your business details. It makes
eventual registration easier (clean historical records) and looks more professional to customers
regardless.</p>

<h2>Keep business and personal finances separate</h2>
<p>A dedicated business bank account, even before formal registration, makes bookkeeping,
eventual GST filing, and simply understanding whether you're profitable far easier than mixing
funds with personal spending.</p>

<h2>What to set up in your store</h2>
<ul>
<li><strong>Business details on every invoice</strong> — name, address, and GSTIN once
registered, consistently applied.</li>
<li><strong>Accurate product pricing</strong> — know whether your listed price is inclusive or
exclusive of tax, and be consistent across your catalogue.</li>
<li><strong>Order records you can export</strong> — for whenever filing season arrives, having
clean, exportable order history saves hours.</li>
</ul>

<p>None of this should block you from starting — plenty of sellers begin pre-registration and
formalise as they grow. <a href="/accounts/signup/">Start your store</a> and get your invoicing
habits right from the first order.</p>
""",
    },
    {
        "title": "10 Features Every Indian Online Store Needs in 2026",
        "category": "Growth & Marketing",
        "tags": ["ecommerce-india", "getting-started"],
        "excerpt": "A checklist of the features that actually move the needle for an Indian "
                   "online store — beyond just having a storefront live.",
        "seo_description": "10 must-have features for an Indian online store in 2026: payments, "
                            "WhatsApp, custom domain, SEO, and more — a practical checklist.",
        "body": """
<p>Having a storefront online is step one. Here's the checklist of what actually separates stores
that grow from stores that stall, specific to what matters for Indian sellers in particular.</p>

<h2>1. Your own custom domain</h2>
<p>Beyond looking professional, a domain you own compounds in SEO value over time — see our
<a href="/blog/how-to-add-a-custom-domain-to-your-online-store-step-by-step/">custom domain
guide</a> for the setup.</p>

<h2>2. Razorpay checkout with UPI</h2>
<p>UPI is the default payment expectation now, not a nice-to-have. Make sure it's front and
centre at checkout, not buried behind a card-first flow.</p>

<h2>3. Cash on Delivery, managed properly</h2>
<p>Offer it, but track return/RTO rates by courier and pincode so it doesn't quietly eat your
margin.</p>

<h2>4. A WhatsApp selling channel</h2>
<p>Whether full catalogue-only mode or just an enquiry button alongside checkout, WhatsApp is
where a large share of Indian buyers actually make their final decision.</p>

<h2>5. Mobile-first storefront design</h2>
<p>The overwhelming majority of Indian ecommerce traffic is mobile. If your theme isn't fast and
clean on a phone, you're losing sales before a customer even sees your products.</p>

<h2>6. Basic on-page SEO on every product and category page</h2>
<p>Unique titles, meta descriptions, and structured data (so Google can show rich results) on
every product page — not just the homepage.</p>

<h2>7. Customer reviews</h2>
<p>Social proof matters more for a new or unfamiliar brand than an established one. Make it easy
for happy customers to leave a review, and display it prominently.</p>

<h2>8. A functioning sitemap and robots.txt</h2>
<p>Invisible to shoppers, essential for being found on Google at all — make sure your platform
handles this automatically rather than requiring manual setup.</p>

<h2>9. Order tracking customers can self-serve</h2>
<p>A visible order-status/tracking page cuts down "where is my order" support volume
significantly.</p>

<h2>10. A referral or affiliate programme</h2>
<p>Your happiest customers are your cheapest acquisition channel — a simple referral link with a
commission or discount turns word-of-mouth into a trackable channel.</p>

<p>Most of this is built in rather than bolted on when you start on <a href="/pricing/">Shopinaday</a>.
<a href="/accounts/signup/">Start your free trial</a> and check items off this list in an
afternoon.</p>
""",
    },
]


class Command(BaseCommand):
    help = "Seed 10 SEO-targeted blog posts for the platform marketing blog."

    def handle(self, *args, **options):
        author, _ = BlogAuthor.objects.get_or_create(
            name=AUTHOR["name"], defaults={"role": AUTHOR["role"], "bio": AUTHOR["bio"]},
        )

        categories = {}
        for name in CATEGORIES:
            cat, _ = BlogCategory.objects.get_or_create(name=name, defaults={"slug": slugify(name)})
            categories[name] = cat

        tag_cache = {}

        def tag_for(name):
            if name not in tag_cache:
                tag_cache[name], _ = BlogTag.objects.get_or_create(name=name, defaults={"slug": slugify(name)})
            return tag_cache[name]

        now = timezone.now()
        created, updated = 0, 0
        for i, p in enumerate(POSTS):
            defaults = {
                "excerpt": p["excerpt"],
                "body": p["body"].strip(),
                "category": categories[p["category"]],
                "author": author,
                "seo_description": p["seo_description"],
                "is_published": True,
                "published_at": now - timezone.timedelta(days=(len(POSTS) - i) * 2),
            }
            post, was_created = BlogPost.objects.update_or_create(title=p["title"], defaults=defaults)
            post.tags.set([tag_for(t) for t in p["tags"]])
            created += was_created
            updated += not was_created

        self.stdout.write(self.style.SUCCESS(
            f"Blog seed done: {created} created, {updated} updated. "
            f"Author: {author.name}. Categories: {len(categories)}."
        ))
