"""Store-level role checks for Mission Control views.

Layer on top of ``ActiveProjectMixin`` (which already enforces staff + an active
membership in the resolved store). These helpers narrow an action to a subset of
store roles — e.g. only the owner/manager may touch payment credentials or the
custom-domain config.

Platform admins (superuser or ``Profile.platform_role``) always pass.
"""

from django.core.exceptions import PermissionDenied

from .models import StoreRole

OWNER = StoreRole.OWNER
MANAGER = StoreRole.MANAGER
STAFF = StoreRole.STAFF

# Common bundles.
OWNER_MANAGER = frozenset({OWNER, MANAGER})
ANY_STORE_STAFF = frozenset({OWNER, MANAGER, STAFF})


def is_platform_admin(user) -> bool:
    """Superuser or Platform Owner — full platform authority."""
    if not (user and user.is_authenticated):
        return False
    if user.is_superuser:
        return True
    profile = getattr(user, "profile", None)
    return bool(profile and profile.is_platform_admin)


def is_platform_staff(user) -> bool:
    """Platform Owner or Platform Manager — may provision + run their stores."""
    if not (user and user.is_authenticated):
        return False
    if user.is_superuser:
        return True
    profile = getattr(user, "profile", None)
    return bool(profile and profile.is_platform_staff)


def managed_projects(user):
    """Projects a platform person administers: everything for an admin, only the
    stores they are the subscription manager of for a Platform Manager."""
    from apps.projects.models import Project

    if is_platform_admin(user):
        return Project.objects.all()
    if is_platform_staff(user):
        return Project.objects.filter(subscription__manager=user).distinct()
    return Project.objects.none()


def store_role(user, project):
    """The user's active role in ``project``, or ``None``."""
    if not (user and user.is_authenticated and project):
        return None
    return (
        project.memberships.filter(user=user, is_active=True)
        .values_list("role", flat=True)
        .first()
    )


def has_store_role(user, project, allowed) -> bool:
    # Platform staff who administer this store get every store-role capability.
    if is_platform_admin(user):
        return True
    if is_platform_staff(user) and project is not None:
        if project.__class__.objects.filter(
            pk=project.pk, subscription__manager=user
        ).exists():
            return True
    return store_role(user, project) in set(allowed)


def assert_store_role(user, project, allowed,
                      message="You do not have access to this action."):
    if not has_store_role(user, project, allowed):
        raise PermissionDenied(message)


class StoreRoleRequiredMixin:
    """Mix in *before* ``ActiveProjectMixin`` on a control view.

    Restricts the view to ``required_store_roles``. Platform admins always pass.
    Relies on ``ActiveProjectMixin.check_active_project_access`` running the hook
    after ``self.active_project`` is resolved but before the request handler.
    """

    required_store_roles = OWNER_MANAGER
    role_denied_message = "Only the store owner or a manager can do this."

    def check_active_project_access(self, request):
        parent = super().check_active_project_access(request)
        if parent is not None:
            return parent
        assert_store_role(
            request.user, self.active_project,
            self.required_store_roles, self.role_denied_message,
        )
        return None
