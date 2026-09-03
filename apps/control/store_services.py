"""Provision a store: create the Project, its owner account, and wire the
subscription. Used by the Mission Control "New store" flow (platform staff)."""

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction

from apps.accounts import team as team_svc
from apps.accounts.models import Membership, Profile, StoreRole
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
