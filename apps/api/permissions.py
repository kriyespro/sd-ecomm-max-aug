"""API permissions.

The storefront project is always resolved server-side from the Host header
(``request.project``) — the client never supplies it. ``request.project`` is a
lazy object, so it must be coerced before truth-testing.
"""

from rest_framework.permissions import SAFE_METHODS, BasePermission


def resolved_project(request):
    """Return the real Project instance or None (forces the lazy object)."""
    project = getattr(request, "project", None)
    if project is None:
        return None
    try:
        return project or None  # evaluates SimpleLazyObject; None -> falsy -> None
    except Exception:
        return None


class HasStore(BasePermission):
    message = "Store could not be resolved from the request host."

    def has_permission(self, request, view):
        return resolved_project(request) is not None


class IsStoreStaff(BasePermission):
    """Authenticated staff with an active membership in the resolved project."""

    def has_permission(self, request, view):
        user = request.user
        project = resolved_project(request)
        if not (user and user.is_authenticated and user.is_staff and project):
            return False
        if user.is_superuser or getattr(getattr(user, "profile", None), "is_platform_admin", False):
            return True
        return user.memberships.filter(project=project, is_active=True).exists()


class ReadOnly(BasePermission):
    def has_permission(self, request, view):
        return request.method in SAFE_METHODS
