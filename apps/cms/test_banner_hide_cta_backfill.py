"""Covers the 0018 data migration: text_hidden used to hide the button too
(one flag controlled the whole text+button block). Existing banners must
keep looking exactly as they did before hide_cta became a separate flag."""

import importlib

from django.apps import apps as django_apps
from django.test import TestCase

from apps.cms.models import Banner, BannerPlacement
from apps.projects.models import Project


def _run_backfill():
    mod = importlib.import_module("apps.cms.migrations.0018_banner_hide_cta_banner_hide_overlay_and_more")
    mod._backfill_hide_cta(django_apps, None)


class BannerHideCtaBackfillTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="Backfill Co", status="active")

    def test_text_hidden_banner_gets_hide_cta_backfilled(self):
        b = Banner.objects.create(
            project=self.project, placement=BannerPlacement.HERO, name="hero",
            text_hidden=True,
        )
        _run_backfill()
        b.refresh_from_db()
        self.assertTrue(b.hide_cta)

    def test_normal_banner_is_untouched(self):
        b = Banner.objects.create(
            project=self.project, placement=BannerPlacement.HERO, name="hero",
        )
        _run_backfill()
        b.refresh_from_db()
        self.assertFalse(b.hide_cta)
        self.assertFalse(b.text_hidden)
