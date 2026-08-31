"""Collapse the plan line-up to three public tiers: Basic, Growth, Pro.

Starter / Advanced / Enterprise are retired (deactivated + unpublished, not
deleted — existing subscriptions keep their PROTECTed FK). Basic / Growth / Pro
are rewritten with the current pricing, limits and feature lists.
"""

from decimal import Decimal

from django.db import migrations

RETIRE = ["starter", "advanced", "enterprise"]

PLANS = [
    dict(
        code="basic", name="Basic", sort_order=1, is_active=True, is_public=True,
        tagline="Everything you need to run your first online store.",
        price_monthly="1499", price_yearly="14990",
        max_products=250, max_staff=3, max_custom_domains=1, storage_gb=5,
        allow_skin_upload=False, remove_platform_branding=False, priority_support=False,
        transaction_fee_pct="1.5",
        features=[
            "Full storefront", "250 products", "1 custom domain", "3 staff",
            "COD + UPI", "Discount codes", "Abandoned cart recovery",
            "WhatsApp notifications", "Basic customer management", "Basic reports",
            "SSL", "Platform branding",
        ],
    ),
    dict(
        code="growth", name="Growth", sort_order=2, is_active=True, is_public=True,
        tagline="For brands ready to grow sales and automate operations.",
        price_monthly="2999", price_yearly="29990",
        max_products=None, max_staff=5, max_custom_domains=2, storage_gb=20,
        allow_skin_upload=True, remove_platform_branding=True, priority_support=False,
        transaction_fee_pct="1.0",
        features=[
            "Unlimited products", "Custom storefront themes / skins", "5 staff",
            "2 custom domains", "Remove platform branding", "Advanced analytics",
            "Professional reports", "Customer segmentation",
            "Automated WhatsApp / email campaigns", "Abandoned-cart automation",
            "Coupons & promotional campaigns", "Product variants",
            "Bulk product import / export", "SEO controls",
            "Pixel / analytics integrations",
        ],
    ),
    dict(
        code="pro", name="Pro", sort_order=3, is_active=True, is_public=True,
        tagline="For established stores with teams and serious sales volume.",
        price_monthly="6999", price_yearly="69990",
        max_products=None, max_staff=15, max_custom_domains=5, storage_gb=100,
        allow_skin_upload=True, remove_platform_branding=True, priority_support=True,
        transaction_fee_pct="0.5",
        features=[
            "Everything in Growth", "15 staff", "5 domains",
            "Advanced report builder", "Custom dashboards",
            "Advanced customer segmentation", "Advanced automation workflows",
            "Multiple warehouses", "Advanced inventory", "Staff permissions",
            "Audit logs", "API access", "Webhooks", "Priority support",
            "Lower transaction fee", "Bulk operations",
        ],
    ),
]


def apply(apps, schema_editor):
    Plan = apps.get_model("billing", "Plan")
    Plan.objects.filter(code__in=RETIRE).update(is_active=False, is_public=False)
    for spec in PLANS:
        spec = dict(spec)
        code = spec.pop("code")
        for k in ("price_monthly", "price_yearly", "transaction_fee_pct"):
            spec[k] = Decimal(spec[k])
        Plan.objects.update_or_create(code=code, defaults=spec)


def revert(apps, schema_editor):
    apps.get_model("billing", "Plan").objects.filter(code__in=RETIRE).update(
        is_active=True, is_public=True
    )


class Migration(migrations.Migration):
    dependencies = [
        ("billing", "0004_alter_billingsettings_default_commission_monthly_pct_and_more"),
    ]
    operations = [migrations.RunPython(apply, revert)]
