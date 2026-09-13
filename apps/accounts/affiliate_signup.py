"""Public self-serve affiliate signup: anyone can become a DGC instantly,
no application or admin approval — that curated path already exists at
/partners/ for merchants who want to be vetted first. This is the open one.
"""

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction

from apps.accounts.models import PlatformRole, Profile
from apps.core.models import AuditLog
from apps.core.services import record_audit

User = get_user_model()


@transaction.atomic
def join_as_affiliate(*, name, email, request=None):
    """Create (or upgrade) the account and grant it the DGC role. Returns
    ``(user, was_created, was_upgraded)``.

    An existing user who's already staff/store-owner/superuser just gets the
    Manager role added on top (a merchant can also be an affiliate) — never
    downgrades a Platform Owner. A banned account is rejected outright.
    """
    email = (email or "").strip().lower()
    if not email:
        raise ValidationError("Email is required.")

    user = User.objects.filter(email__iexact=email).first()
    created = user is None
    if created:
        user = User(username=email[:150], email=email)
        first, _, last = (name or "").strip().partition(" ")
        user.first_name, user.last_name = first, last
        user.is_staff = True
        user.is_active = True
        user.set_unusable_password()
        user.save()

    profile = getattr(user, "profile", None) or Profile.objects.get_or_create(user=user)[0]
    if profile.is_banned:
        raise ValidationError("This account can't join the affiliate programme.")

    upgraded = False
    if profile.platform_role == PlatformRole.NONE:
        profile.platform_role = PlatformRole.MANAGER
        profile.save(update_fields=["platform_role", "updated_at"])
        upgraded = True

    record_audit(
        actor=user, action=AuditLog.Action.CREATE if created else AuditLog.Action.UPDATE,
        target=profile, changes={"affiliate_join": True, "was_created": created},
        request=request,
    )
    return user, created, upgraded
