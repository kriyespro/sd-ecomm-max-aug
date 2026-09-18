"""New pricing for the three public plans: yearly is the number the
business actually chose; monthly = yearly / 12 * 1.20 (a 20% surcharge for
paying month to month instead of committing to a year), quantized to the
cent. DGC wholesale prices follow the same monthly-from-yearly formula.

    Plan     Owner yearly   Owner monthly   DGC yearly   DGC monthly
    basic    24999          2499.90         14999        1499.90
    growth   34999          3499.90         24999        2499.90
    pro      64999          6499.90         54999        5499.90

Limits, features and everything else about these three plans are untouched
— this migration only ever writes to the four price fields.
"""

from decimal import ROUND_HALF_UP, Decimal

from django.db import migrations

_CENT = Decimal("0.01")


def _monthly_from_yearly(yearly: Decimal) -> Decimal:
    return (yearly / 12 * Decimal("1.20")).quantize(_CENT, rounding=ROUND_HALF_UP)


PRICES = {
    "basic": {"yearly": Decimal("24999"), "dgc_yearly": Decimal("14999")},
    "growth": {"yearly": Decimal("34999"), "dgc_yearly": Decimal("24999")},
    "pro": {"yearly": Decimal("64999"), "dgc_yearly": Decimal("54999")},
}


def apply(apps, schema_editor):
    Plan = apps.get_model("billing", "Plan")
    for code, prices in PRICES.items():
        plan = Plan.objects.filter(code=code).first()
        if plan is None:
            continue
        plan.price_yearly = prices["yearly"]
        plan.price_monthly = _monthly_from_yearly(prices["yearly"])
        plan.dgc_price_yearly = prices["dgc_yearly"]
        plan.dgc_price_monthly = _monthly_from_yearly(prices["dgc_yearly"])
        plan.save(update_fields=[
            "price_yearly", "price_monthly", "dgc_price_yearly", "dgc_price_monthly",
        ])


def revert(apps, schema_editor):
    Plan = apps.get_model("billing", "Plan")
    Plan.objects.filter(code__in=PRICES).update(
        dgc_price_yearly=Decimal("0"), dgc_price_monthly=Decimal("0"),
    )
    # Owner price_monthly/price_yearly intentionally left at the new values on
    # revert — the pre-migration numbers (1499/2999/6999 monthly,
    # 14990/29990/69990 yearly) aren't recoverable from this migration alone
    # without risking clobbering a price a super admin already edited since.


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0013_plan_dgc_price_monthly_plan_dgc_price_yearly_and_more"),
    ]

    operations = [migrations.RunPython(apply, revert)]
