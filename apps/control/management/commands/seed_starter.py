"""Seed (or remove) demo storefront content for a store.

    python manage.py seed_starter <project id or domain> [--force] [--remove]

New stores get this automatically via ``create_store``; this command is for
back-filling stores that predate it, re-seeding after edits (``--force``), or
wiping the demo rows (``--remove``).
"""

from django.core.management.base import BaseCommand, CommandError

from apps.control.starter_content import (
    is_seeded,
    remove_starter_content,
    reset_and_seed,
    seed_starter_content,
)
from apps.projects.models import Project


class Command(BaseCommand):
    help = "Seed or remove demo storefront content for a store."

    def add_arguments(self, parser):
        parser.add_argument("project", help="Project id or primary domain")
        parser.add_argument("--force", action="store_true",
                            help="Re-seed even if already seeded")
        parser.add_argument("--remove", action="store_true",
                            help="Delete the seeded demo rows instead")
        parser.add_argument("--reset", action="store_true",
                            help="DESTRUCTIVE: wipe all storefront content first, "
                                 "then seed a fresh demo set")

    def handle(self, *args, **opts):
        ident = opts["project"]
        qs = Project.objects.filter(pk=ident) if ident.isdigit() else \
            Project.objects.filter(primary_domain=ident.lower())
        project = qs.first()
        if project is None:
            raise CommandError(f"No project matching {ident!r}")

        if opts["remove"]:
            remove_starter_content(project)
            self.stdout.write(self.style.SUCCESS(f"Removed demo content from {project.name}"))
            return

        if opts["reset"]:
            ref = reset_and_seed(project)
            counts = ", ".join(f"{len(v)} {k}" for k, v in ref.items())
            self.stdout.write(self.style.WARNING(
                f"Wiped storefront content and re-seeded {project.name}: {counts}"
            ))
            return

        if is_seeded(project) and not opts["force"]:
            self.stdout.write(f"{project.name} is already seeded — use --force to re-seed.")
            return

        ref = seed_starter_content(project, force=opts["force"])
        counts = ", ".join(f"{len(v)} {k}" for k, v in ref.items())
        self.stdout.write(self.style.SUCCESS(f"Seeded {project.name}: {counts}"))
