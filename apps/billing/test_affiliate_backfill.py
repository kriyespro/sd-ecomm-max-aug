"""Covers the 0011 data migration that repairs stores which signed up through
an affiliate link during the short window before the referred_by/manager
split shipped (they got `manager` set instead of `referred_by`)."""

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.billing import services as billing_svc
from apps.projects.models import Project

User = get_user_model()


def _run_backfill():
    import importlib

    mod = importlib.import_module("apps.billing.migrations.0011_backfill_affiliate_referred_by")
    mod.backfill(django_apps, None)


class AffiliateBackfillMigrationTests(TestCase):
    def setUp(self):
        self.dgc = User.objects.create_user("dgc", "dgc@t.test", "pw", is_staff=True)

    def test_stale_affiliate_manager_row_is_moved_to_referred_by(self):
        project = Project.objects.create(name="Stale Ref Co", status="active")
        sub = billing_svc.ensure_subscription(project)
        sub.manager = self.dgc
        sub.affiliate_ref = "STALE123"
        sub.save(update_fields=["manager", "affiliate_ref"])

        _run_backfill()

        sub.refresh_from_db()
        self.assertIsNone(sub.manager_id)
        self.assertEqual(sub.referred_by_id, self.dgc.pk)
        self.assertEqual(sub.affiliate_ref, "STALE123")

    def test_a_real_dgc_managed_store_without_affiliate_ref_is_untouched(self):
        project = Project.objects.create(name="Real DGC Co", status="active")
        sub = billing_svc.ensure_subscription(project)
        sub.manager = self.dgc
        sub.save(update_fields=["manager"])

        _run_backfill()

        sub.refresh_from_db()
        self.assertEqual(sub.manager_id, self.dgc.pk)
        self.assertIsNone(sub.referred_by_id)

    def test_row_with_both_already_set_correctly_is_untouched(self):
        project = Project.objects.create(name="Already Fixed Co", status="active")
        sub = billing_svc.ensure_subscription(project)
        sub.referred_by = self.dgc
        sub.affiliate_ref = "OK123"
        sub.save(update_fields=["referred_by", "affiliate_ref"])

        _run_backfill()

        sub.refresh_from_db()
        self.assertIsNone(sub.manager_id)
        self.assertEqual(sub.referred_by_id, self.dgc.pk)
