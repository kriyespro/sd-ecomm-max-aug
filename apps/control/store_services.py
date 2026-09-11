"""Provision a store: create the Project, its owner account, and wire the
subscription. Used by the Mission Control "New store" flow (platform staff)."""

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils.crypto import get_random_string

from apps.accounts import team as team_svc
from apps.accounts.models import Membership, PlatformRole, Profile, StoreRole
from apps.accounts.permissions import is_platform_admin
from apps.billing import services as billing_svc
from apps.billing.models import BillingPeriod
from apps.core.models import AuditLog
from apps.core.services import record_audit
from apps.projects.models import Project

User = get_user_model()


def _get_or_create_staff_user(email, name="", password=None):
    """Returns ``(user, created, temp_password)``. ``temp_password`` is set only
    when we just created the account and had to invent a password for it (the
    actor left the field blank) — the caller shows it once so it can be handed
    to the new owner; nothing generated here is ever logged or stored raw."""
    email = email.strip().lower()
    user = User.objects.filter(email__iexact=email).first()
    created = user is None
    temp_password = None
    if created:
        first, _, last = (name or "").strip().partition(" ")
        user = User.objects.create_user(
            username=email[:150], email=email, first_name=first, last_name=last,
        )
        if password:
            user.set_password(password)
        else:
            temp_password = get_random_string(12)
            user.set_password(temp_password)
        user.is_staff = True
        user.save()
    elif not user.is_staff:
        user.is_staff = True
        user.save(update_fields=["is_staff"])
    Profile.objects.get_or_create(user=user)
    return user, created, temp_password


@transaction.atomic
def create_store(*, name, owner_email, plan, actor, request=None,
                 primary_domain="", currency="INR", country="IN",
                 owner_name="", period=BillingPeriod.MONTHLY, manager=None,
                 owner_password=None, subdomain=""):
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

    # The post_save signal already tried to give the new store a trial
    # subscription, but it swallows its own errors (e.g. no public plan), so the
    # row may not exist. Create it here with the chosen plan — never assume it's
    # there — then apply the requested period / manager.
    sub = billing_svc.ensure_subscription(project, plan=plan, manager=manager)
    sub.plan = plan
    sub.period = period if period in dict(BillingPeriod.choices) else BillingPeriod.MONTHLY
    sub.manager = manager
    sub.save(update_fields=["plan", "period", "manager", "updated_at"])

    owner, created_owner, temp_password = _get_or_create_staff_user(
        owner_email, owner_name, owner_password
    )
    Membership.objects.update_or_create(
        user=owner, project=project,
        defaults={"role": StoreRole.OWNER, "is_active": True},
    )

    # No custom domain given -> hand the store a platform subdomain so it's
    # reachable straight away (the owner can rename it in the setup wizard).
    # A subdomain typed on the create form wins; otherwise derive one from the
    # owner email / store name, same as public self-signup.
    if not domain:
        try:
            from apps.projects import subdomains

            if subdomains.base_domain():
                wanted = subdomains.slugify(subdomain)
                if wanted and subdomains.is_available(wanted):
                    slug = wanted
                else:
                    slug = subdomains.unique_slug(
                        wanted or owner_email.split("@")[0] or name
                    )
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

    return project, owner, created_owner, temp_password


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
    """Add (creating the account if needed) an owner / manager / staff member.
    Returns ``(membership, temp_password | None)`` — a password is generated
    automatically for a brand-new account when the actor didn't type one."""
    _, _, temp_password = _get_or_create_staff_user(email, name, password)
    membership = team_svc.add_member(
        actor=actor, project=project, email=email, role=role, request=request,
    )
    return membership, temp_password


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


def transfer_store_owner(*, project, current_owner, new_owner_email, actor, request=None):
    """Platform-admin moves the Owner role from ``current_owner`` to another
    account, demoting ``current_owner`` to manager in the same transaction.

    Exists so a superadmin can fix "sole owner" — the block ``delete_user``
    raises — without switching their session into the store's own Team screen.
    Creates the new owner's account (with a one-time password) if they don't
    have one yet, same as the Team panel's add-member flow.
    """
    if not is_platform_admin(actor):
        raise PermissionDenied("Only a platform admin can transfer store ownership.")
    new_owner_email = (new_owner_email or "").strip().lower()
    if not new_owner_email:
        raise ValidationError("Enter the new owner's email.")
    if new_owner_email == (current_owner.email or "").strip().lower():
        raise ValidationError("Pick a different user to become owner.")

    with transaction.atomic():
        existing = Membership.objects.filter(
            project=project, user__email__iexact=new_owner_email,
            is_active=True, role__in=team_svc.TEAM_ROLES,
        ).first()
        temp_password = None
        if existing is not None:
            new_owner = existing.user
            team_svc.change_role(
                actor=actor, project=project, membership=existing,
                role=StoreRole.OWNER, request=request,
            )
        else:
            # Creates the account (with a one-time password) if the email is new,
            # or reactivates a dormant membership row — same as the Team panel.
            membership, temp_password = add_member(
                project=project, email=new_owner_email, name="",
                role=StoreRole.OWNER, actor=actor, request=request,
            )
            new_owner = membership.user

        old_membership = Membership.objects.filter(
            project=project, user=current_owner, role=StoreRole.OWNER, is_active=True,
        ).first()
        if old_membership is not None:
            team_svc.change_role(
                actor=actor, project=project, membership=old_membership,
                role=StoreRole.MANAGER, request=request,
            )

    return new_owner, temp_password


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
