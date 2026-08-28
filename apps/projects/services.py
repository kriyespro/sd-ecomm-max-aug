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
