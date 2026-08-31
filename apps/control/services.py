"""Mission Control business logic (project.md section 6).

Views stay thin; everything here. GET never mutates — all mutating helpers are
called only from POST views.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth import login as auth_login
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import PlatformRole
from apps.core.models import AuditLog
from apps.core.services import record_audit
from apps.projects.models import Project

User = get_user_model()

IMPERSONATE_SESSION_KEY = "impersonate_original_user_id"


# --- Dashboard ---------------------------------------------------------

def dashboard_stats():
    now = timezone.now()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return {
        "total_users": User.objects.count(),
        "signups_today": User.objects.filter(date_joined__gte=today).count(),
        "active_users_7d": User.objects.filter(last_login__gte=now - timedelta(days=7)).count(),
        "total_projects": Project.objects.count(),
        "active_projects": Project.objects.filter(status=Project.Status.ACTIVE).count(),
        # Revenue wiring lands with the orders app (Phase 5).
        "revenue_today": 0,
    }


def recent_activity(limit=20):
    return (
        AuditLog.objects.select_related("actor", "project").all()[:limit]
    )


# --- User manager -----------------------------------------------------

def search_users(query="", limit=50):
    qs = User.objects.select_related("profile").order_by("-date_joined")
    query = (query or "").strip()
    if query:
        qs = qs.filter(
            Q(username__icontains=query)
            | Q(email__icontains=query)
            | Q(first_name__icontains=query)
            | Q(last_name__icontains=query)
        )
    return qs[:limit]


def _is_platform_admin(user):
    profile = getattr(user, "profile", None)
    return bool(user.is_superuser or (profile and profile.is_platform_admin))


def create_platform_user(*, actor, email, raw_password, first_name="",
                         last_name="", platform_role=PlatformRole.NONE, request=None):
    """Platform-admin creates a standalone account. No store needed — the user
    can be assigned to a store later. Authority + audit enforced here; password
    strength is validated in the form."""
    if not _is_platform_admin(actor):
        raise PermissionDenied("Only a platform admin can create users.")
    email = (email or "").strip().lower()
    if not email:
        raise ValidationError("Email is required.")
    if User.objects.filter(email__iexact=email).exists():
        raise ValidationError("A user with that email already exists.")
    if platform_role not in dict(PlatformRole.choices):
        platform_role = PlatformRole.NONE
    if platform_role == PlatformRole.OWNER and not actor.is_superuser:
        raise PermissionDenied("Only a superuser can create a Platform Owner.")

    user = User.objects.create_user(
        username=email[:150], email=email,
        first_name=(first_name or "").strip(), last_name=(last_name or "").strip(),
    )
    user.set_password(raw_password)
    # Platform roles need Mission Control; a "none" account gets is_staff only
    # once it is added to a store team (apps.accounts.team._sync_staff_flag).
    user.is_staff = platform_role != PlatformRole.NONE
    user.save()

    profile = user.profile
    profile.platform_role = platform_role
    profile.save(update_fields=["platform_role", "updated_at"])

    record_audit(
        actor=actor, action=AuditLog.Action.CREATE, target=user,
        changes={"email": email, "platform_role": platform_role}, request=request,
    )
    return user


def set_user_password(*, actor, target, raw_password, request=None):
    """Platform-admin password reset for another account. Strength validation
    happens in the form; this enforces authority and writes the audit row."""
    if not _is_platform_admin(actor):
        raise PermissionDenied("Only a platform admin can reset passwords.")
    if target.is_superuser and not actor.is_superuser:
        raise PermissionDenied("Only a superuser can reset a superuser's password.")
    target.set_password(raw_password)
    target.save(update_fields=["password"])
    record_audit(
        actor=actor,
        action=AuditLog.Action.UPDATE,
        target=target,
        changes={"password": "reset"},
        request=request,
    )
    return target


def set_user_banned(*, actor, target, banned, request=None):
    if not _is_platform_admin(actor):
        raise PermissionDenied("Only a platform admin can ban users.")
    if target.is_superuser:
        raise PermissionDenied("Cannot ban a superuser.")
    profile = target.profile
    profile.is_banned = banned
    profile.save(update_fields=["is_banned", "updated_at"])
    if banned:
        target.is_active = False
    else:
        target.is_active = True
    target.save(update_fields=["is_active"])
    record_audit(
        actor=actor,
        action=AuditLog.Action.UPDATE,
        target=target,
        changes={"is_banned": banned},
        request=request,
    )
    return target


# --- Impersonation --------------------------------------------------

def can_impersonate(actor, target):
    if actor.pk == target.pk:
        return False
    if target.is_superuser:
        return False
    return actor.is_superuser or getattr(actor.profile, "is_platform_admin", False)


def start_impersonation(*, request, target):
    actor = request.user
    if not can_impersonate(actor, target):
        raise PermissionDenied("Not allowed to impersonate this user.")
    original_id = request.session.get(IMPERSONATE_SESSION_KEY, actor.pk)
    record_audit(
        actor=actor,
        action=AuditLog.Action.IMPERSONATE,
        target=target,
        changes={"impersonate": "start"},
        request=request,
    )
    auth_login(request, target, backend="django.contrib.auth.backends.ModelBackend")
    request.session[IMPERSONATE_SESSION_KEY] = original_id


def stop_impersonation(*, request):
    original_id = request.session.pop(IMPERSONATE_SESSION_KEY, None)
    if not original_id:
        return None
    original = User.objects.filter(pk=original_id).first()
    if original is None:
        return None
    auth_login(request, original, backend="django.contrib.auth.backends.ModelBackend")
    record_audit(
        actor=original,
        action=AuditLog.Action.IMPERSONATE,
        target=original,
        changes={"impersonate": "stop"},
        request=request,
    )
    return original
