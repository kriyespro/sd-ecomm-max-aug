"""Public self-serve signup: one form -> a user, a store, a short free trial,
then the setup wizard.

Partner- and platform-provisioned stores go through ``apps.control.store_services``
instead and get the longer ``BillingSettings.trial_days`` trial.
"""

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction

from apps.accounts.models import Membership, StoreRole
from apps.billing import services as billing_svc
from apps.billing.models import BillingSettings
from apps.core.models import AuditLog
from apps.core.services import record_audit
from apps.projects.models import Project

User = get_user_model()


@transaction.atomic
def self_signup(*, name, email, password, store_name, plan=None, request=None):
    """Create the account + store. Returns ``(project, user, user_was_created)``.

    Raises ``ValidationError`` if the email already belongs to a usable account.
    """
    email = (email or "").strip().lower()
    store_name = (store_name or "").strip()
    if not email:
        raise ValidationError("Email is required.")
    if not store_name:
        raise ValidationError("Store name is required.")

    existing = User.objects.filter(email__iexact=email).first()
    if existing and existing.has_usable_password():
        raise ValidationError(
            "An account with this email already exists — sign in instead."
        )

    user = existing or User(username=email[:150], email=email)
    first, _, last = (name or "").strip().partition(" ")
    user.first_name, user.last_name = first, last
    user.is_staff = True
    user.is_active = True
    user.set_password(password)
    user.save()

    project = Project.objects.create(
        name=store_name, status=Project.Status.ACTIVE, currency="INR", country="IN",
    )
    Membership.objects.update_or_create(
        user=user, project=project,
        defaults={"role": StoreRole.OWNER, "is_active": True},
    )

    # The billing post_save signal already opened a standard-length trial with
    # no manager — shorten it to the self-signup length, pin the chosen plan.
    cfg = BillingSettings.load()
    sub = getattr(project, "subscription", None)
    if sub is not None:
        if plan is not None and sub.plan_id != plan.pk:
            sub.plan = plan
            sub.save(update_fields=["plan", "updated_at"])
        billing_svc.reset_trial(sub, cfg.self_signup_trial_days)

    record_audit(
        actor=user, project=project, action=AuditLog.Action.CREATE, target=project,
        changes={"signup": "self-serve", "owner": email}, request=request,
    )

    # Fill the storefront with editable demo content, same as a staff-made store.
    from apps.control.store_services import _seed_demo

    transaction.on_commit(lambda: _seed_demo(project.pk))

    return project, user, existing is None
