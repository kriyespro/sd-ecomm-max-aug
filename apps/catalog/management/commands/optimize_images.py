"""Backfill: optimise product images that predate the pipeline (or re-run all).

    python manage.py optimize_images                 # only un-optimised, queued
    python manage.py optimize_images --all           # every image, from master
    python manage.py optimize_images --sync          # run inline (blocks)
    python manage.py optimize_images --spacing 15    # seconds between queued tasks

Queued tasks land on the rate-limited ``images`` queue and are also spaced with
a per-task ``countdown`` so a large batch never spikes CPU.
"""

from django.core.management.base import BaseCommand

from apps.catalog.models import ProductImage
from apps.catalog.tasks import optimize_product_image


class Command(BaseCommand):
    help = "Re-encode product images to optimised WebP + responsive renditions."

    def add_arguments(self, parser):
        parser.add_argument("--all", action="store_true",
                            help="Re-process every image (resets optimized_at).")
        parser.add_argument("--sync", action="store_true",
                            help="Run in-process instead of queueing Celery tasks.")
        parser.add_argument("--spacing", type=int, default=10,
                            help="Seconds of countdown between queued tasks (default 10).")

    def handle(self, *args, **opts):
        qs = ProductImage.objects.all()
        if not opts["all"]:
            qs = qs.filter(optimized_at__isnull=True)

        ids = list(qs.values_list("pk", flat=True))
        if opts["all"]:
            ProductImage.objects.filter(pk__in=ids).update(optimized_at=None)

        spacing = max(0, opts["spacing"])
        for i, pk in enumerate(ids):
            if opts["sync"]:
                optimize_product_image(pk)
                self.stdout.write(f"  image {pk} done")
            else:
                optimize_product_image.apply_async(
                    (pk,), countdown=min(i * spacing, 3600)
                )
                self.stdout.write(f"  image {pk} queued (+{min(i * spacing, 3600)}s)")

        self.stdout.write(self.style.SUCCESS(f"{len(ids)} image(s) {'processed' if opts['sync'] else 'queued'}."))
