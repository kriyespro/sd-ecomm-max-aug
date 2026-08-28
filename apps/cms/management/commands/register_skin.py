"""Register (or update) a storefront skin.

    python manage.py register_skin aurora --label "Aurora"
    python manage.py register_skin aurora --label "Aurora" --default
    python manage.py register_skin aurora --deactivate

The template folder ``templates/shopfront/skins/<slug>/`` should already exist;
the command warns (but still writes the row) if it does not.
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.text import slugify

from apps.cms.models import Skin


class Command(BaseCommand):
    help = "Create or update a Skin row for a storefront template bundle."

    def add_arguments(self, parser):
        parser.add_argument("slug")
        parser.add_argument("--label", default="")
        parser.add_argument("--description", default="")
        parser.add_argument("--default", action="store_true",
                            help="Make this the fallback skin for stores.")
        parser.add_argument("--deactivate", action="store_true",
                            help="Mark the skin unavailable to stores.")

    @transaction.atomic
    def handle(self, *args, **opts):
        slug = slugify(opts["slug"])
        if not slug:
            raise CommandError("Give a valid slug.")

        folder_found = False
        for base in settings.TEMPLATES[0]["DIRS"]:
            if (base / "shopfront" / "skins" / slug).is_dir():
                folder_found = True
                break
        if not folder_found:
            self.stdout.write(self.style.WARNING(
                f"No templates/shopfront/skins/{slug}/ folder found — "
                "the skin will fall back to the default templates until you add it."
            ))

        skin, created = Skin.objects.get_or_create(slug=slug)
        skin.label = opts["label"] or skin.label or slug.replace("-", " ").title()
        if opts["description"]:
            skin.description = opts["description"]
        skin.is_active = not opts["deactivate"]

        if opts["default"]:
            if not skin.is_active:
                raise CommandError("Cannot set an inactive skin as the default.")
            Skin.objects.filter(is_default=True).exclude(pk=skin.pk).update(is_default=False)
            skin.is_default = True

        skin.save()
        verb = "Created" if created else "Updated"
        flags = []
        if skin.is_default:
            flags.append("default")
        if not skin.is_active:
            flags.append("inactive")
        suffix = f" ({', '.join(flags)})" if flags else ""
        self.stdout.write(self.style.SUCCESS(f"{verb} skin '{skin.slug}'{suffix}."))
