"""Backfill: optimise product images that predate the pipeline (or re-run all).

    python manage.py optimize_images                # only un-optimised
    python manage.py optimize_images --all          # every image, from master
    python manage.py optimize_images --sync         # run inline, not via Celery
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

    def handle(self, *args, **opts):
        qs = ProductImage.objects.all()
        if not opts["all"]:
            qs = qs.filter(optimized_at__isnull=True)

        ids = list(qs.values_list("pk", flat=True))
        if opts["all"]:
            ProductImage.objects.filter(pk__in=ids).update(optimized_at=None)

        for pk in ids:
            if opts["sync"]:
                optimize_product_image(pk)
            else:
                optimize_product_image.delay(pk)
            self.stdout.write(f"  image {pk} {'done' if opts['sync'] else 'queued'}")

        self.stdout.write(self.style.SUCCESS(f"{len(ids)} image(s) processed."))
