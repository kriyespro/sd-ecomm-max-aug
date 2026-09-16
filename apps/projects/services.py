"""Project access helpers."""

from django.db import models

from .models import Project


def projects_for_user(user):
    """Projects a user may administer in Mission Control.

    * Superuser / Platform Owner — every store.
    * Platform Manager — stores they are the subscription manager of, plus any
      they hold a membership in.
    * Everyone else — stores they hold an active membership in.
    """
    if not user.is_authenticated:
        return Project.objects.none()

    profile = getattr(user, "profile", None)
    if user.is_superuser or (profile and profile.is_platform_admin):
        return Project.objects.all()

    member_q = models.Q(memberships__user=user, memberships__is_active=True)
    if profile and profile.is_platform_staff:
        return Project.objects.filter(member_q | models.Q(subscription__manager=user)).distinct()

    return Project.objects.filter(member_q).distinct()


def owner_notification_email(project):
    """Best address to reach this store's owner at, for a platform-initiated
    notice (low stock, abandoned cart, etc — never a customer-facing one).

    Falls back down a chain so this works out of the box with no settings
    screen to fill in first: an explicit override in
    ``Project.notification_config["owner_email"]``, then the storefront's own
    public support address, then the account email of whoever holds the
    OWNER membership."""
    from apps.accounts.models import StoreRole

    override = (project.notification_config or {}).get("owner_email", "")
    if override:
        return override

    try:
        from apps.cms.models import StoreProfile

        profile = StoreProfile.objects.filter(project=project).values_list(
            "support_email", flat=True
        ).first()
        if profile:
            return profile
    except Exception:  # noqa: BLE001 - never let a notification lookup 500 anything
        pass

    return (
        project.memberships.filter(role=StoreRole.OWNER, is_active=True)
        .exclude(user__email="")
        .values_list("user__email", flat=True)
        .first()
        or ""
    )
