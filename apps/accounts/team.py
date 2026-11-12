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
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import get_random_string

from apps.core.models import AuditLog
from apps.core.services import absolute_url, record_audit

from .models import Membership, PendingVerification, StoreRole
from .permissions import (
    has_store_role,
    is_platform_admin,
    is_platform_staff,
    store_role,
)

INVITE_WINDOW = timezone.timedelta(days=7)

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
    # A platform admin, or a real owner membership, manages the whole team.
    if is_platform_admin(actor) or store_role(actor, project) == StoreRole.OWNER:
        return True, set(TEAM_ROLES)
    role = store_role(actor, project)
    if role == StoreRole.MANAGER:
        return False, {StoreRole.STAFF}
    # A DGC (platform manager) who runs this store for a client can manage the
    # client's *staff* — but not owner/manager roles, and never escalate
    # themselves into a membership that outlives the engagement. Manager
    # reassignment doesn't revoke memberships, so this cap is the guard.
    if is_platform_staff(actor) and has_store_role(actor, project, {StoreRole.OWNER}):
        return False, {StoreRole.STAFF}
    raise PermissionDenied("You cannot manage this store's team.")


def can_grant_privileged(actor, project) -> bool:
    """UI hint: may this actor assign the owner / manager roles?"""
    try:
        can_priv, _ = _actor_caps(actor, project)
    except PermissionDenied:
        return False
    return can_priv


def can_reset_password(actor, project) -> bool:
    """May this actor generate a new one-time password for ANY team member
    (owner, manager or staff)? Broader than ``can_grant_privileged`` — a DGC
    running a client's store may reset any of that store's logins (they're
    the one fielding "I'm locked out" calls) even though they can't reassign
    the owner/manager roles themselves. A manager still resets staff only
    (checked separately in ``reset_password``); platform admins and a real
    owner always can.
    """
    if is_platform_admin(actor):
        return True
    if store_role(actor, project) == StoreRole.OWNER:
        return True
    if is_platform_staff(actor) and has_store_role(actor, project, {StoreRole.OWNER}):
        return True
    return False


def _clean_role(role):
    role = (role or "").strip()
    if role not in TEAM_ROLES:
        raise TeamError("Pick a role: owner, manager or staff.")
    return role


def _active_owner_count(project, *, exclude_pk=None, lock=False):
    """Count of active owners on ``project``.

    ``lock=True`` (used inside change_role/remove_member, both already
    @transaction.atomic) row-locks the matching Membership rows first, so two
    concurrent "demote the other owner" calls can't both read count=1 and both
    proceed — closing a race that could otherwise zero out a store's owners.
    select_for_update() can't be combined with .count() (Postgres rejects FOR
    UPDATE with an aggregate), so the locked rows are counted in Python.
    """
    qs = Membership.objects.filter(
        project=project, role=StoreRole.OWNER, is_active=True
    )
    if exclude_pk is not None:
        qs = qs.exclude(pk=exclude_pk)
    if lock:
        return len(list(qs.select_for_update()))
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


def _split_name(name):
    first, _, last = (name or "").strip().partition(" ")
    return first, last


def _attach_member(*, actor, project, user, role, request=None):
    """Give ``user`` the ``role`` team membership on ``project`` (reactivating a
    dormant row if there is one), enforce the plan seat cap on a net add, sync
    ``is_staff`` and write the audit row. Returns the ``Membership``."""
    existing = Membership.objects.filter(project=project, user=user).first()
    already_on_team = bool(existing and existing.is_active and existing.role in TEAM_ROLES)

    if not already_on_team:
        # Lock the project's current team rows first — otherwise two
        # concurrent adds (two managers, or a double click) can both read the
        # same under-cap count and both pass, pushing the team past the
        # plan's seat limit. A blocked FOR UPDATE re-reads on unblock, so the
        # second caller's subsequent count (in check_can_add_staff) sees the
        # first caller's now-committed row.
        list(
            Membership.objects.select_for_update().filter(
                project=project, is_active=True, role__in=TEAM_ROLES
            )
        )
        # net add -> check the plan's team-size limit
        try:
            from apps.billing.limits import check_can_add_staff

            check_can_add_staff(project)
        except ImportError:
            pass

    if already_on_team:
        raise TeamError(f"{user.email} is already on the team.")

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
        target=membership, changes={"email": user.email, "role": role},
        request=request,
    )
    return membership


def _already_on_team(project, email):
    return Membership.objects.filter(
        project=project, user__email__iexact=email, is_active=True, role__in=TEAM_ROLES
    ).exists()


def _create_invite(*, project, email, role, invited_by, invited_name, request):
    """Replace any prior unconsumed invite for this (project, email) and send
    the confirmation email on commit. Returns the new ``PendingVerification``."""
    PendingVerification.objects.filter(
        kind=PendingVerification.Kind.TEAM_INVITE, project=project,
        email=email, consumed_at__isnull=True,
    ).delete()
    invite = PendingVerification.objects.create(
        kind=PendingVerification.Kind.TEAM_INVITE, project=project, email=email,
        role=role, invited_by=invited_by, invited_name=invited_name,
        expires_at=timezone.now() + INVITE_WINDOW,
    )
    record_audit(
        actor=invited_by, project=project, action=AuditLog.Action.CREATE,
        target=invite, changes={"email": email, "role": role, "invite": "sent"},
        request=request,
    )
    confirm_url = absolute_url(request, reverse("accounts:invite_accept", args=[invite.token]))
    transaction.on_commit(lambda: _send_invite_email(invite.pk, confirm_url))
    return invite


def _send_invite_email(invite_id, confirm_url):
    from .tasks import send_team_invite_email

    send_team_invite_email.delay(invite_id, confirm_url)


@transaction.atomic
def add_member(*, actor, project, email, role, request=None):
    """Attach someone who already has an account to the team, instantly — no
    confirmation step.

    Unlike ``provision_member`` (the Team screen's untrusted "type in any
    email" surface, now invite-gated — see ``confirm_invite``), every caller
    of this function is itself already a trusted, narrow surface: today
    that's ``apps.control.store_services.add_member``, used only by a
    platform admin fixing a store's ownership (``transfer_store_owner``),
    which creates the account itself first. Adding a confirmation gate here
    too would block that emergency admin tool from working synchronously.
    """
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
    return _attach_member(
        actor=actor, project=project, user=user, role=role, request=request
    )


@transaction.atomic
def provision_member(*, actor, project, email, role, name="", request=None):
    """Invite a team member by email, creating their login only once they
    confirm.

    The owner (or a manager, for staff) enters a name, email and role.
    Nothing is attached, and no account is created, yet — a confirmation link
    is emailed to that address; attaching the Membership instantly (or
    creating a brand-new account with a password only the inviter ever saw)
    let anyone type in an email they didn't own and gain visibility into it,
    or hold credentials to an account nominally owned by someone else. See
    ``confirm_invite`` for what happens once they click it.

    Returns the ``PendingVerification`` invite.
    """
    role = _clean_role(role)
    _, assignable = _actor_caps(actor, project)
    if role not in assignable:
        raise PermissionDenied(
            "You can only add staff members." if StoreRole.STAFF in assignable
            else "You cannot assign that role."
        )

    email = (email or "").strip().lower()
    if not email:
        raise TeamError("Enter an email address.")
    if "@" not in email:
        raise TeamError("Enter a valid email address.")
    if _already_on_team(project, email):
        raise TeamError(f"{email} is already on the team.")

    # Not a reservation — just avoids sending a doomed invite; the seat cap
    # is re-checked for real inside _attach_member when the invite is confirmed.
    if not Membership.objects.filter(project=project, user__email__iexact=email).exists():
        try:
            from apps.billing.limits import check_can_add_staff

            check_can_add_staff(project)
        except ImportError:
            pass

    return _create_invite(
        project=project, email=email, role=role, invited_by=actor,
        invited_name=(name or "").strip(), request=request,
    )


def get_invite(token):
    return PendingVerification.objects.filter(
        kind=PendingVerification.Kind.TEAM_INVITE, token=token,
    ).select_related("project", "invited_by").first()


@transaction.atomic
def confirm_invite(*, invite, password=None, request=None):
    """The invited person clicked their confirmation link. Attach the
    Membership now (creating their account, with their own chosen password,
    if they don't have one yet) — this is the only place a team invite
    actually takes effect. Returns ``(membership, user)``."""
    if invite.is_used:
        raise TeamError("This invite has already been used.")
    if invite.is_expired:
        raise TeamError("This invite has expired — ask them to resend it.")

    project, role, actor = invite.project, invite.role, invite.invited_by
    try:
        _, assignable = _actor_caps(actor, project)
        if role not in assignable:
            raise PermissionDenied
    except PermissionDenied:
        raise TeamError(
            "This invite is no longer valid — ask the store owner to resend it."
        ) from None

    user = User.objects.filter(email__iexact=invite.email).first()
    if user is None:
        if not password:
            raise TeamError("Choose a password to finish joining.")
        first, last = _split_name(invite.invited_name)
        user = User.objects.create_user(
            username=invite.email[:150], email=invite.email,
            first_name=first, last_name=last,
        )
        user.set_password(password)
        user.is_active = True
        user.save(update_fields=["password", "is_active"])

    try:
        membership = _attach_member(
            actor=actor, project=project, user=user, role=role, request=request
        )
    except PermissionDenied:
        # The plan's seat cap was hit by someone else between the invite
        # being sent and this click — not the role/caps check above, which
        # already passed; a real, if rare, race.
        raise TeamError(
            "This store is at its plan's seat limit — ask the owner to "
            "upgrade or free a seat before accepting."
        ) from None
    invite.consumed_at = timezone.now()
    invite.save(update_fields=["consumed_at", "updated_at"])
    return membership, user


@transaction.atomic
def resend_invite(*, actor, project, invite, request=None):
    """Owner/manager only — rotate the token + expiry and re-send."""
    _actor_caps(actor, project)
    if invite.project_id != project.pk:
        raise TeamError("That invite is not on this store.")
    if invite.is_used:
        raise TeamError("That invite has already been used.")
    from .models import _new_verification_token

    invite.token = _new_verification_token()
    invite.expires_at = timezone.now() + INVITE_WINDOW
    invite.save(update_fields=["token", "expires_at", "updated_at"])
    confirm_url = absolute_url(request, reverse("accounts:invite_accept", args=[invite.token]))
    transaction.on_commit(lambda: _send_invite_email(invite.pk, confirm_url))
    return invite


@transaction.atomic
def revoke_invite(*, actor, project, invite):
    """Owner/manager only — delete an unconsumed invite outright."""
    _actor_caps(actor, project)
    if invite.project_id != project.pk:
        raise TeamError("That invite is not on this store.")
    invite.delete()


@transaction.atomic
def reset_password(*, actor, project, membership, request=None):
    """Generate a fresh one-time password for an existing member's login and
    return it. The old password stops working immediately.

    Permission: platform admin, the store's real owner, or a DGC running this
    store (via the subscription-manager bypass) may reset anyone's login. A
    manager may reset a staff login only. Nobody may reset their own — use the
    normal change-password flow for that.
    """
    if membership.project_id != project.pk:
        raise TeamError("That member is not on this store.")
    if membership.user_id == getattr(actor, "pk", None):
        raise TeamError("Use your account settings to change your own password.")

    if can_reset_password(actor, project):
        pass
    elif (store_role(actor, project) == StoreRole.MANAGER
          and membership.role == StoreRole.STAFF):
        pass
    else:
        raise PermissionDenied(
            "Only the store owner, a DGC, or a platform admin can reset this login."
        )

    new_password = get_random_string(12)
    membership.user.set_password(new_password)
    membership.user.save(update_fields=["password"])
    record_audit(
        actor=actor, project=project, action=AuditLog.Action.UPDATE,
        target=membership, changes={"password_reset": True}, request=request,
    )
    return new_password


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
        and _active_owner_count(project, exclude_pk=membership.pk, lock=True) == 0
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
        and _active_owner_count(project, exclude_pk=membership.pk, lock=True) == 0
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
