from django.db import migrations

TASKS = [
    dict(title="Add 5 new products with 4 photos each", icon="📸", auto_key="products_with_photos", target=5, points=30,
         detail="Fresh products keep shoppers coming back. Clear photos from 4 angles (front, back, detail, in use) sell far more.",
         cta_label="Add a product", cta_url="", sort_order=10),
    dict(title="Talk to a new supplier for a lower cost price", icon="🤝", auto_key="", target=1, points=20,
         detail="Even 5% off your cost goes straight into profit. Ask for a bulk rate, or compare two quotes.",
         cta_label="", cta_url="", sort_order=20),
    dict(title="Send all your customers an offer message", icon="📣", auto_key="", target=1, points=20,
         detail="Tell past customers about a new offer — a WhatsApp or SMS broadcast is the cheapest way to get repeat orders.",
         cta_label="Open customers", cta_url="/admin/customers/", sort_order=30),
    dict(title="Create a fresh offer coupon", icon="🎟", auto_key="coupons_created", target=1, points=15,
         detail="A limited-time code gives hesitant shoppers a reason to buy today.",
         cta_label="", cta_url="", sort_order=40),
    dict(title="Refresh 3 products — price, stock or photos", icon="✏️", auto_key="products_updated", target=3, points=10,
         detail="Up-to-date prices and stock mean fewer cancelled orders.",
         cta_label="", cta_url="", sort_order=50),
    dict(title="Pack and ship every waiting order", icon="📦", auto_key="orders_cleared", target=1, points=15,
         detail="Fast dispatch earns repeat customers and good reviews.",
         cta_label="", cta_url="", sort_order=60),
    dict(title="Share your store link on WhatsApp or Instagram", icon="🔗", auto_key="", target=1, points=10,
         detail="Post your store link in your status, groups or bio — free traffic.",
         cta_label="Open my store", cta_url="{store_url}", sort_order=70),
]

TIPS = [
    dict(title="Did you know? You can get a free sales team.", icon="🔗", sort_order=10,
         detail="Turn on the Referral program, then give friends and influencers their own ?ref=CODE link. You pay commission only when the order is actually paid.",
         cta_label="Set up referrals", cta_url="/admin/marketing/referrals/"),
    dict(title="Print your store QR code", icon="📱", sort_order=20,
         detail="Once your custom domain is connected, download the QR from the top of this page and stick it on packaging, bills and visiting cards."),
    dict(title="Four photos beat one", icon="📸", sort_order=30,
         detail="Products with 4+ photos (front, back, detail, in use) get more clicks and fewer returns."),
    dict(title="Reply to enquiries within 5 minutes", icon="⚡", sort_order=40,
         detail="Most buyers pick the first seller who answers. Keep the WhatsApp enquiry button switched on."),
    dict(title="Bundle to raise your order value", icon="🎁", sort_order=50,
         detail="Offer “buy 2 get 10% off” or free shipping above a set amount — shoppers add one more item to qualify."),
]


def seed(apps, schema_editor):
    Mission = apps.get_model("coach", "Mission")
    if Mission.objects.exists():
        return
    for row in TASKS:
        Mission.objects.create(kind="task", is_system=True, **row)
    for row in TIPS:
        Mission.objects.create(kind="tip", is_system=True, auto_key="", **row)


class Migration(migrations.Migration):
    dependencies = [("coach", "0001_initial")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
