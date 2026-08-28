"""Seed 6 Shopify-style plans + the billing-settings singleton.

Prices are placeholder INR — a super admin edits everything under
Mission Control → Billing → Plans.
"""

from decimal import Decimal

from django.db import migrations

PLANS = [
    dict(code="starter", name="Starter", sort_order=1,
         tagline="Sell on social and via a link — no full storefront.",
         price_monthly="399", price_yearly="3990",
         max_products=25, max_staff=1, max_custom_domains=0, storage_gb=1,
         allow_skin_upload=False, remove_platform_branding=False, priority_support=False,
         transaction_fee_pct="2.0",
         features=["Link-in-bio store", "25 products", "COD + UPI", "Basic analytics"]),
    dict(code="basic", name="Basic", sort_order=2,
         tagline="A full storefront for a new business.",
         price_monthly="1499", price_yearly="14990",
         max_products=1000, max_staff=2, max_custom_domains=1, storage_gb=5,
         allow_skin_upload=False, remove_platform_branding=False, priority_support=False,
         transaction_fee_pct="1.5",
         features=["Full storefront", "1 custom domain", "2 staff", "Discount codes", "Abandoned cart"]),
    dict(code="growth", name="Growth", sort_order=3,
         tagline="For stores finding their stride.",
         price_monthly="3499", price_yearly="34990",
         max_products=None, max_staff=5, max_custom_domains=2, storage_gb=20,
         allow_skin_upload=True, remove_platform_branding=True, priority_support=False,
         transaction_fee_pct="1.0",
         features=["Unlimited products", "Upload custom skins", "5 staff", "2 domains",
                   "Remove platform branding", "Professional reports"]),
    dict(code="pro", name="Pro", sort_order=4,
         tagline="Scaling stores with a team.",
         price_monthly="6999", price_yearly="69990",
         max_products=None, max_staff=10, max_custom_domains=5, storage_gb=100,
         allow_skin_upload=True, remove_platform_branding=True, priority_support=True,
         transaction_fee_pct="0.5",
         features=["Everything in Growth", "10 staff", "5 domains", "Priority support",
                   "Advanced report builder", "Lower transaction fee"]),
    dict(code="advanced", name="Advanced", sort_order=5,
         tagline="High-volume stores.",
         price_monthly="14999", price_yearly="149990",
         max_products=None, max_staff=25, max_custom_domains=15, storage_gb=500,
         allow_skin_upload=True, remove_platform_branding=True, priority_support=True,
         transaction_fee_pct="0.2",
         features=["Everything in Pro", "25 staff", "15 domains", "Custom report attributes",
                   "Lowest transaction fee"]),
    dict(code="enterprise", name="Enterprise", sort_order=6, is_public=False,
         tagline="Custom pricing — talk to us.",
         price_monthly="49999", price_yearly="499990",
         max_products=None, max_staff=None, max_custom_domains=None, storage_gb=None,
         allow_skin_upload=True, remove_platform_branding=True, priority_support=True,
         transaction_fee_pct="0.0",
         features=["Unlimited everything", "Dedicated support", "SLA", "0% transaction fee"]),
]


def seed(apps, schema_editor):
    Plan = apps.get_model("billing", "Plan")
    BillingSettings = apps.get_model("billing", "BillingSettings")
    for p in PLANS:
        p = dict(p)
        for k in ("price_monthly", "price_yearly", "transaction_fee_pct"):
            p[k] = Decimal(p[k])
        Plan.objects.update_or_create(code=p.pop("code"), defaults=p)
    BillingSettings.objects.get_or_create(pk=1)


def unseed(apps, schema_editor):
    apps.get_model("billing", "Plan").objects.filter(
        code__in=[p["code"] for p in PLANS]
    ).delete()


class Migration(migrations.Migration):
    dependencies = [("billing", "0001_initial")]
    operations = [migrations.RunPython(seed, unseed)]
