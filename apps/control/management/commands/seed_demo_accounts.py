"""Seed 3 demo stores, each with a login-ready owner + store manager, plus a
platform-manager account.

    python manage.py seed_demo_accounts

Idempotent — re-running resets the stores, memberships and passwords. Log in at
/accounts/login/ with the **email as the username** and the printed password.
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.accounts.models import Membership, PlatformRole, Profile, StoreRole
from apps.billing.models import BillingPeriod, Plan
from apps.projects.models import Project
from apps.control import store_services

User = get_user_model()

PLATFORM_MANAGER = ("platmanager@sd.test", "Platform-mgr-2026", "Priya Menon")

STORES = [
    dict(
        name="Lumen Lighting", domain="lumen.localhost", plan="basic",
        period=BillingPeriod.MONTHLY,
        owner=("owner@lumen.test", "Lumen-owner-2026", "Owen Reed"),
        manager=("manager@lumen.test", "Lumen-mgr-2026", "Maya Kapoor"),
    ),
    dict(
        name="Trailhead Outdoors", domain="trailhead.localhost", plan="growth",
        period=BillingPeriod.YEARLY,
        owner=("owner@trailhead.test", "Trailhead-owner-2026", "Tara Singh"),
        manager=("manager@trailhead.test", "Trailhead-mgr-2026", "Marcus Vale"),
    ),
    dict(
        name="Petal & Vine Florals", domain="petal.localhost", plan="pro",
        period=BillingPeriod.MONTHLY,
        owner=("owner@petal.test", "Petal-owner-2026", "Pia Dcosta"),
        manager=("manager@petal.test", "Petal-mgr-2026", "Milo Grant"),
    ),
]


def _set_user(email, password, name, *, platform_role=PlatformRole.NONE):
    first, _, last = (name or "").partition(" ")
    user, _ = User.objects.get_or_create(
        username=email[:150], defaults={"email": email}
    )
    user.email = email
    user.first_name, user.last_name = first, last
    user.is_active = True
    user.is_staff = True
    user.set_password(password)
    user.save()
    profile, _ = Profile.objects.get_or_create(user=user)
    if profile.platform_role != platform_role:
        profile.platform_role = platform_role
        profile.save(update_fields=["platform_role", "updated_at"])
    return user


class Command(BaseCommand):
    help = "Seed 3 demo stores with login-ready owner/manager accounts."

    @transaction.atomic
    def handle(self, *args, **opts):
        actor = User.objects.filter(is_superuser=True).first()

        pm = _set_user(*PLATFORM_MANAGER, platform_role=PlatformRole.MANAGER)

        rows = [("Platform Manager", "—", PLATFORM_MANAGER[0], PLATFORM_MANAGER[1])]

        for spec in STORES:
            plan = Plan.objects.get(code=spec["plan"])
            Project.objects.filter(name=spec["name"]).delete()  # reset

            owner_email, owner_pw, owner_name = spec["owner"]
            project, owner, _ = store_services.create_store(
                name=spec["name"], primary_domain=spec["domain"],
                owner_email=owner_email, owner_name=owner_name,
                plan=plan, period=spec["period"], manager=pm, actor=actor,
            )
            _set_user(owner_email, owner_pw, owner_name)  # give a usable password

            mgr_email, mgr_pw, mgr_name = spec["manager"]
            mgr = _set_user(mgr_email, mgr_pw, mgr_name)
            Membership.objects.update_or_create(
                user=mgr, project=project,
                defaults={"role": StoreRole.MANAGER, "is_active": True},
            )

            rows.append(("Store Owner", spec["name"], owner_email, owner_pw))
            rows.append(("Store Manager", spec["name"], mgr_email, mgr_pw))

            self.stdout.write(self.style.SUCCESS(
                f"  {spec['name']:22} {plan.name}/{spec['period']}  "
                f"({spec['domain']})"
            ))

        w1 = max(len(r[0]) for r in rows)
        w2 = max(len(r[1]) for r in rows)
        w3 = max(len(r[2]) for r in rows)
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING(
            "Log in at /accounts/login/  —  username = the email below"
        ))
        self.stdout.write(f"  {'ROLE':<{w1}}  {'STORE':<{w2}}  {'USERNAME (email)':<{w3}}  PASSWORD")
        self.stdout.write(f"  {'-'*w1}  {'-'*w2}  {'-'*w3}  {'-'*18}")
        for role, store, email, pw in rows:
            self.stdout.write(f"  {role:<{w1}}  {store:<{w2}}  {email:<{w3}}  {pw}")
        self.stdout.write("")
        if actor:
            self.stdout.write(f"  Super admin already exists: {actor.username} "
                              f"(set its password with `manage.py changepassword {actor.username}`)")
