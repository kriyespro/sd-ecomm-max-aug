"""Backfill for stores that signed up through an affiliate link in the short
window between the affiliate-program commit (which wrongly wrote the credit
onto `manager` — granting store access) and the fix that moved it to
`referred_by` (commission only, no access).

`affiliate_ref` is only ever written by the self-signup referral path, so any
row that has it set *and* still has `manager` set is a leftover from the old
code, not a real DGC-provisioned or admin-assigned store — move the credit
from `manager` to `referred_by` and clear `manager`.
"""

from django.db import migrations, models


def backfill(apps, schema_editor):
    Subscription = apps.get_model("billing", "Subscription")
    Subscription.objects.filter(
        affiliate_ref__gt="", manager__isnull=False, referred_by__isnull=True,
    ).update(referred_by=models.F("manager"), manager=None)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0010_subscription_referred_by"),
    ]

    operations = [
        migrations.RunPython(backfill, noop),
    ]
