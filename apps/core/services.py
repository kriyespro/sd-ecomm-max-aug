"""Core services. Business logic lives here, never in views or templates."""

from .models import AuditLog


def record_audit(*, actor=None, project=None, action, target=None, changes=None, request=None):
    """Write an :class:`AuditLog` row.

    ``target`` may be any model instance; its type and pk are stored.
    """
    target_type = ""
    target_id = ""
    if target is not None:
        target_type = f"{target._meta.app_label}.{target._meta.model_name}"
        target_id = str(target.pk)

    ip = None
    if request is not None:
        ip = request.META.get("REMOTE_ADDR")

    return AuditLog.objects.create(
        actor=actor,
        project=project,
        action=action,
        target_type=target_type,
        target_id=target_id,
        changes=changes or {},
        ip_address=ip,
    )
