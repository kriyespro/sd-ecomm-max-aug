"""Accounts: profile extension plus platform- and store-level roles.

project.md sections 5 and 29: use Django auth, extend with a Profile, and add a
project/store-level permission layer on top of Django's own permissions.
"""

from django.conf import settings
from django.db import models

from apps.core.models import TimeStampedModel


class PlatformRole(models.TextChoices):
    NONE = "none", "None"
    # "Digital Growth Consultant" — runs multiple stores from one dashboard as
    # their owner, earns commission on those stores. DB value kept as
    # ``platform_manager`` for continuity with existing rows / code.
    MANAGER = "platform_manager", "Digital Growth Consultant (DGC)"
    OWNER = "platform_owner", "Platform Owner"
    # Super Admin maps to Django's is_superuser.


class StoreRole(models.TextChoices):
    OWNER = "owner", "Store Owner"
    MANAGER = "manager", "Store Manager"
    STAFF = "staff", "Staff"
    CUSTOMER = "customer", "Customer"


class Profile(TimeStampedModel):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile"
    )
    phone = models.CharField(max_length=20, blank=True)
    avatar = models.ImageField(upload_to="accounts/avatars/", blank=True, null=True)
    platform_role = models.CharField(
        max_length=32, choices=PlatformRole.choices, default=PlatformRole.NONE
    )
    is_banned = models.BooleanField(default=False)

    def __str__(self):
        return f"Profile<{self.user}>"

    @property
    def is_platform_admin(self):
        """Full platform authority — billing, user directory, impersonation,
        every store. Superuser or Platform Owner only."""
        return self.user.is_superuser or self.platform_role == PlatformRole.OWNER

    @property
    def is_platform_staff(self):
        """Platform Owner OR Platform Manager. A Manager can create stores and
        administer *their own* stores/commissions, nothing platform-wide."""
        return self.user.is_superuser or self.platform_role != PlatformRole.NONE

    @property
    def is_platform_manager(self):
        return self.platform_role == PlatformRole.MANAGER and not self.user.is_superuser


class Membership(TimeStampedModel):
    """A user's role within one project (project.md section 5)."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    role = models.CharField(max_length=20, choices=StoreRole.choices)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "project"], name="uniq_membership_user_project"
            )
        ]
        ordering = ["project__name", "role"]

    def __str__(self):
        return f"{self.user} @ {self.project} ({self.role})"
