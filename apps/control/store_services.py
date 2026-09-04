"""Provision a store: create the Project, its owner account, and wire the
subscription. Used by the Mission Control "New store" flow (platform staff)."""

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from apps.accounts import team as team_svc
from apps.accounts.models import Membership, PlatformRole, Profile, StoreRole
from apps.accounts.permissions import is_platform_admin
from apps.billing.models import BillingPeriod
from apps.core.models import AuditLog
from apps.core.services import record_audit
from apps.projects.models import Project

User = get_user_model()


def _get_or_create_staff_user(email, name="", password=None):
    email = email.strip().lower()
    user = User.objects.filter(email__iexact=email).first()
    created = user is None
    if created:
        first, _, last = (name or "").strip().partition(" ")
        user = User.objects.create_user(
            username=email[:150], email=email, first_name=first, last_name=last,
        )
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.is_staff = True
        user.save()
    elif not user.is_staff:
        user.is_staff = True
        user.save(update_fields=["is_staff"])
    Profile.objects.get_or_create(user=user)
    return user, created


@transaction.atomic
def create_store(*, name, owner_email, plan, actor, request=None,
                 primary_domain="", currency="INR", country="IN",
                 owner_name="", period=BillingPeriod.MONTHLY, manager=None,
                 owner_password=None):
    name = (name or "").strip()
    if not name:
        raise ValidationError("Store name is required.")
    if not (owner_email or "").strip():
        raise ValidationError("Owner email is required.")

    domain = (primary_domain or "").strip().lower().rstrip(".")
    if domain and Project.objects.filter(primary_domain=domain).exists():
        raise ValidationError(f"The domain {domain} is already assigned to a store.")

    project = Project.objects.create(
        name=name, primary_domain=domain or None,
        currency=currency or "INR", country=country or "IN",
        status=Project.Status.ACTIVE,
    )

    # A trial subscription was just created by the post_save signal — point it
    # at the chosen plan / manager.
    sub = project.subscription
    sub.plan = plan
    sub.period = period if period in dict(BillingPeriod.choices) else BillingPeriod.MONTHLY
    sub.manager = manager
    sub.save(update_fields=["plan", "period", "manager", "updated_at"])

    owner, created_owner = _get_or_create_staff_user(owner_email, owner_name, owner_password)
    Membership.objects.update_or_create(
        user=owner, project=project,
        defaults={"role": StoreRole.OWNER, "is_active": True},
    )

    # No custom domain given -> hand the store a platform subdomain so it's
    # reachable straight away (the owner can rename it in the setup wizard).
    if not domain:
        try:
            from apps.projects import subdomains

            if subdomains.base_domain():
                slug = subdomains.unique_slug(owner_email.split("@")[0] or name)
                subdomains.assign(project, slug)
        except Exception:  # noqa: BLE001
            import logging

            logging.getLogger(__name__).exception("subdomain assignment failed")

    record_audit(actor=actor, project=project, action=AuditLog.Action.CREATE,
                 target=project, changes={"owner": owner.email,
                                          "plan": plan.code}, request=request)

    # Fill the storefront with editable demo content (text everywhere, images
    # left blank so the skins show sized placeholders). Never block store
    # creation on it.
    transaction.on_commit(lambda: _seed_demo(project.pk))

    return project, owner, created_owner


def _seed_demo(project_id):
    from apps.control.starter_content import seed_starter_content
    from apps.projects.models import Project

    try:
        seed_starter_content(Project.objects.get(pk=project_id))
    except Exception:  # noqa: BLE001
        import logging

        logging.getLogger(__name__).exception(
            "starter content seeding failed for project %s", project_id
        )


def add_member(*, project, email, name, role, actor, request=None, password=None):
    """Add (creating the account if needed) an owner / manager / staff member."""
    _get_or_create_staff_user(email, name, password)  # ensure the account exists
    return team_svc.add_member(
        actor=actor, project=project, email=email, role=role, request=request,
    )


def set_store_manager(*, project, manager, actor, request=None):
    """Assign (or clear, ``manager=None``) the DGC credited/commissioned for
    this store. Platform-admin only — a store can be reassigned between DGCs
    at any time, independent of who created it."""
    if not is_platform_admin(actor):
        raise PermissionDenied("Only a platform admin can reassign a store's manager.")
    sub = getattr(project, "subscription", None)
    if sub is None:
        raise ValidationError("This store has no subscription to assign.")
    if manager is not None and getattr(manager.profile, "platform_role", None) != PlatformRole.MANAGER:
        raise ValidationError("Choose a Digital Growth Consultant (DGC) account.")

    old_manager = sub.manager
    if old_manager == manager:
        return sub
    sub.manager = manager
    sub.save(update_fields=["manager", "updated_at"])
    record_audit(
        actor=actor, project=project, action=AuditLog.Action.UPDATE, target=sub,
        changes={"manager": [
            old_manager.email if old_manager else None,
            manager.email if manager else None,
        ]}, request=request,
    )
    return sub


def _require_superuser(actor):
    if not actor.is_superuser:
        raise PermissionDenied("Only a superadmin can do that.")


def archive_store(*, project, actor, request=None):
    """Take a store offline (storefront closes; Mission Control stays open for
    the owner). Reversible via ``unarchive_store``. Superadmin only."""
    _require_superuser(actor)
    if project.status == Project.Status.ARCHIVED:
        return project
    old = project.status
    project.status = Project.Status.ARCHIVED
    project.save(update_fields=["status", "updated_at"])
    record_audit(
        actor=actor, project=project, action=AuditLog.Action.UPDATE, target=project,
        changes={"status": [old, project.status]}, request=request,
    )
    return project


def unarchive_store(*, project, actor, request=None):
    """Reopen an archived store."""
    _require_superuser(actor)
    if project.status != Project.Status.ARCHIVED:
        return project
    project.status = Project.Status.ACTIVE
    project.save(update_fields=["status", "updated_at"])
    record_audit(
        actor=actor, project=project, action=AuditLog.Action.UPDATE, target=project,
        changes={"status": [Project.Status.ARCHIVED, project.status]}, request=request,
    )
    return project


def delete_store(*, project, actor, confirm_name, request=None):
    """Permanently delete a store and every row that belongs to it (products,
    orders, customers, ... — everything hangs off ``Project`` by FK cascade).
    There is no undo. Superadmin only, and the caller must retype the store's
    exact name so a stray click can't wipe a tenant."""
    _require_superuser(actor)
    if (confirm_name or "").strip() != project.name:
        raise ValidationError("Type the store's exact name to confirm deletion.")

    name, pk = project.name, project.pk
    # AuditLog.project is SET_NULL, so this row outlives the project it names.
    record_audit(
        actor=actor, project=None, action=AuditLog.Action.DELETE, target=None,
        changes={"deleted_store": name, "project_id": pk}, request=request,
    )
    project.delete()
