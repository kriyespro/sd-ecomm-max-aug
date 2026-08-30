"""Backfill: re-encode existing store logos and banner images to compact WebP.

The size trim runs automatically on every ``StoreProfile`` / ``Banner`` save from
now on; this walks rows uploaded before that and re-saves them so the same path
fires once. Idempotent — a file already carrying the ``.sd`` marker is skipped.
"""

from django.core.management.base import BaseCommand

from apps.cms.models import Banner, StoreProfile


class Command(BaseCommand):
    help = "Shrink existing store logos and banner images."

    def handle(self, *args, **options):
        logos = 0
        for sp in StoreProfile.objects.exclude(logo=""):
            before = sp.logo.name
            sp.save()
            if sp.logo.name != before:
                logos += 1
                self.stdout.write(f"  logo {before} -> {sp.logo.name}")

        banners = 0
        for b in Banner.objects.exclude(image="").exclude(image__isnull=True):
            before = b.image.name
            b.save()
            if b.image.name != before:
                banners += 1
                self.stdout.write(f"  banner {before} -> {b.image.name}")

        self.stdout.write(self.style.SUCCESS(f"Done. {logos} logo(s), {banners} banner(s) re-encoded."))
