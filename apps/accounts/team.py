"""Per-store team management.

The store owner (and, for staff only, a manager) decides who can sign into
Mission Control for their store. Rules enforced here — views stay thin:

* Only an owner may grant / change the ``owner`` or ``manager`` role, or touch
  an existing owner / manager row. A manager may only add, re-role or remove
  ``staff``.
* A project must always keep at least one active owner.
* The person must already have an account (they register on the storefront).
  Adding them here flips ``User.is_staff`` on so they can reach ``/admin/``;
  removing the last team membership flips it back off.
* Platform admins bypass all of the above.
"""

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import transaction

from apps.core.models import AuditLog
from apps.core.services import record_audit

from .models import Membership, StoreRole
from .permissions import has_store_role, is_platform_staff, store_role

User = get_user_model()

# Roles that mean "member of staff", ordered by privilege.
TEAM_ROLES = (StoreRole.OWNER, StoreRole.MANAGER, StoreRole.STAFF)
PRIVILEGED_ROLES = frozenset({StoreRole.OWNER, StoreRole.MANAGER})


class TeamError(Exception):
    """Expected, user-facing problem (bad email, last owner, …)."""


def team_members(project):
    return (
        Membership.objects.filter(
            project=project, role__in=TEAM_ROLES, is_active=True
        )
        .select_related("user", "user__profile")
        .order_by("role", "user__email")
    )


def _actor_caps(actor, project):
    """Return ``(can_manage_privileged, assignable_roles)`` for this actor.

    Raises ``PermissionDenied`` if the actor may not manage the team at all.
    """
    # Platform staff who administer this store manage its team like an owner.
    if has_store_role(actor, project, {StoreRole.OWNER}):
        return True, set(TEAM_ROLES)
    role = store_role(actor, project)
    if role == StoreRole.MANAGER:
        return False, {StoreRole.STAFF}
    if role == StoreRole.MANAGER:
        return False, {StoreRole.STAFF}
    raise PermissionDenied("You cannot manage this store's team.")


def can_grant_privileged(actor, project) -> bool:
    """UI hint: may this actor assign the owner / manager roles?"""
    try:
        can_priv, _ = _actor_caps(actor, project)
    except PermissionDenied:
        return False
    return can_priv


def _clean_role(role):
    role = (role or "").strip()
    if role not in TEAM_ROLES:
        raise TeamError("Pick a role: owner, manager or staff.")
    return role


def _active_owner_count(project, *, exclude_pk=None):
    qs = Membership.objects.filter(
        project=project, role=StoreRole.OWNER, is_active=True
    )
    if exclude_pk is not None:
        qs = qs.exclude(pk=exclude_pk)
    return qs.count()


def _guard_target(actor, project, membership, can_priv, assignable):
    if membership.project_id != project.pk:
        raise TeamError("That member is not on this store.")
    if membership.role in PRIVILEGED_ROLES and not can_priv:
        raise PermissionDenied("Only the store owner can manage owners and managers.")
    if membership.role not in assignable and not can_priv:
        raise PermissionDenied("You can only manage staff members.")


def _sync_staff_flag(user):
    """Keep ``User.is_staff`` in step with team membership. Superusers untouched."""
    if user.is_superuser:
        return
    on_a_team = Membership.objects.filter(
        user=user, is_active=True, role__in=TEAM_ROLES
    ).exists()
    should_be_staff = on_a_team or is_platform_staff(user)
    if user.is_staff != should_be_staff:
        user.is_staff = should_be_staff
        user.save(update_fields=["is_staff"])


@transaction.atomic
def add_member(*, actor, project, email, role, request=None):
    role = _clean_role(role)
    _, assignable = _actor_caps(actor, project)
    if role not in assignable:
        raise PermissionDenied("You can only add staff members.")

    email = (email or "").strip().lower()
    if not email:
        raise TeamError("Enter an email address.")
    user = User.objects.filter(email__iexact=email).first()
    if user is None:
        raise TeamError(
            "No account with that email. Ask them to register on the store "
            "first, then add them here."
        )

    existing = Membership.objects.filter(project=project, user=user).first()
    if not (existing and existing.is_active and existing.role in TEAM_ROLES):
        # not already on the team -> this is a net add; check the plan limit
        try:
            from apps.billing.limits import check_can_add_staff

            check_can_add_staff(project)
        except ImportError:
            pass

    if existing and existing.is_active and existing.role in TEAM_ROLES:
        raise TeamError(f"{email} is already on the team.")

    if existing is not None:
        existing.role = role
        existing.is_active = True
        existing.save(update_fields=["role", "is_active", "updated_at"])
        membership = existing
    else:
        membership = Membership.objects.create(
            project=project, user=user, role=role, is_active=True
        )

    _sync_staff_flag(user)
    record_audit(
        actor=actor, project=project, action=AuditLog.Action.CREATE,
        target=membership, changes={"email": email, "role": role}, request=request,
    )
    return membership


@transaction.atomic
def change_role(*, actor, project, membership, role, request=None):
    role = _clean_role(role)
    can_priv, assignable = _actor_caps(actor, project)
    _guard_target(actor, project, membership, can_priv, assignable)
    if role not in assignable:
        raise PermissionDenied("You cannot assign that role.")
    if membership.role == role:
        return membership
    if (
        membership.role == StoreRole.OWNER
        and _active_owner_count(project, exclude_pk=membership.pk) == 0
    ):
        raise TeamError(
            "The store must keep at least one owner. Add another owner first."
        )

    old = membership.role
    membership.role = role
    membership.save(update_fields=["role", "updated_at"])
    _sync_staff_flag(membership.user)
    record_audit(
        actor=actor, project=project, action=AuditLog.Action.UPDATE,
        target=membership, changes={"role": [old, role]}, request=request,
    )
    return membership


@transaction.atomic
def remove_member(*, actor, project, membership, request=None):
    can_priv, assignable = _actor_caps(actor, project)
    _guard_target(actor, project, membership, can_priv, assignable)
    if (
        membership.role == StoreRole.OWNER
        and _active_owner_count(project, exclude_pk=membership.pk) == 0
    ):
        raise TeamError("The store must keep at least one owner.")

    user = membership.user
    record_audit(
        actor=actor, project=project, action=AuditLog.Action.DELETE,
        target=membership,
        changes={"email": user.email, "role": membership.role}, request=request,
    )
    # Hard delete: this removes any team role. A person's shopper identity lives
    # in apps.customers.Customer (keyed by project + email), not on Membership,
    # so their order history is untouched.
    membership.delete()
    _sync_staff_flag(user)
    return user
