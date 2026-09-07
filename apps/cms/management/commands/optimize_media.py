"""Backfill: re-encode existing image uploads (logos, banners, avatars, brand
logos, category images, skin previews, SEO OG images, ...) to compact WebP.

The size trim runs automatically on every save of these models from now on;
this walks rows uploaded before that and re-saves them so the same path fires
once. Idempotent — a file already carrying the ``.sd`` marker is skipped.
"""

from django.core.management.base import BaseCommand
from django.db.models import Q

from apps.accounts.models import Profile
from apps.catalog.models import Brand
from apps.categories.models import Category
from apps.cms.models import Banner, Skin, StoreProfile
from apps.projects.models import Project
from apps.seo.models import SeoMeta, SeoSettings

# (model, [image field names]) — every field wired to shrink_image_field.
_TARGETS = [
    (StoreProfile, ["logo"]),
    (Banner, ["image", "mobile_image"]),
    (Profile, ["avatar"]),
    (Brand, ["logo"]),
    (Category, ["image", "banner", "icon"]),
    (Skin, ["preview_image"]),
    (Project, ["logo"]),
    (SeoSettings, ["default_og_image"]),
    (SeoMeta, ["og_image"]),
]


class Command(BaseCommand):
    help = "Shrink existing image uploads (logos, banners, avatars, brand/category images, skin previews, SEO OG images) to compact WebP."

    def handle(self, *args, **options):
        total = 0
        for model, fields in _TARGETS:
            q = None
            for f in fields:
                cond = ~Q(**{f: ""})
                q = cond if q is None else (q | cond)
            count = 0
            for obj in model.objects.filter(q):
                before = {f: getattr(obj, f).name for f in fields}
                obj.save()
                changed = [f for f in fields if getattr(obj, f).name != before[f]]
                if changed:
                    count += 1
                    for f in changed:
                        self.stdout.write(f"  {model.__name__}.{f} {before[f]} -> {getattr(obj, f).name}")
            total += count
            self.stdout.write(f"{model.__name__}: {count} re-encoded")

        self.stdout.write(self.style.SUCCESS(f"Done. {total} file(s) re-encoded."))
