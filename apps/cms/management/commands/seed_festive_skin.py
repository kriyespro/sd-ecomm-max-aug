"""Upsert the built-in "Festive" skin row (templates ship in the repo).

    python manage.py seed_festive_skin [--activate --project acme-store]

Idempotent. Does NOT touch other skins or any store's current choice unless
--activate is passed.
"""

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.cms.models import Skin, SkinSource, SkinStatus, ThemeSettings
from apps.projects.models import Project

SLUG = "festive"
SKINS_DIR = Path(settings.BASE_DIR) / "templates" / "shopfront" / "skins"
REQUIRED = ["base.jinja", "home.jinja", "partials/_card.jinja"]


class Command(BaseCommand):
    help = "Register the built-in 'Festive' storefront skin."

    def add_arguments(self, parser):
        parser.add_argument("--activate", action="store_true")
        parser.add_argument("--project", default="acme-store")

    def handle(self, *args, **opts):
        folder = SKINS_DIR / SLUG
        missing = [r for r in REQUIRED if not (folder / r).exists()]
        if missing:
            raise CommandError(
                f"templates/shopfront/skins/{SLUG}/ is missing: {', '.join(missing)}"
            )

        skin, created = Skin.objects.update_or_create(
            slug=SLUG,
            defaults=dict(
                label="Festive",
                description=(
                    "Fashion-jewellery look: white base, maroon and gold, "
                    "gold hairlines, trust marquee, round category row, "
                    "tabbed New/Bestsellers rail and hover-swap product cards. "
                    "Playfair Display over Jost."
                ),
                source=SkinSource.BUILTIN, is_sandboxed=False,
                status=SkinStatus.APPROVED, is_active=True, project=None,
            ),
        )
        self.stdout.write(self.style.SUCCESS(
            f"{'Created' if created else 'Updated'} skin '{SLUG}'."
        ))

        if opts["activate"]:
            try:
                project = Project.objects.get(slug=opts["project"])
            except Project.DoesNotExist:
                raise CommandError(f"No project {opts['project']!r}.")
            ts, _ = ThemeSettings.objects.get_or_create(project=project)
            ts.skin = skin
            ts.save(update_fields=["skin", "updated_at"])
            self.stdout.write(self.style.SUCCESS(f"Activated '{SLUG}' on {project.name}."))
