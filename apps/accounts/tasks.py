"""Platform-level account email — team invites and self-signup verification.

Keeps SMTP out of the request path. Unlike apps.notifications.tasks, these
send through apps.notifications.providers.DjangoEmailProvider directly rather
than the tenant notification pipeline — there's no Project to scope a
NotificationLog to yet (self-signup) or the email isn't a per-store
transactional event (a team invite).
"""

from celery import shared_task

from apps.notifications.providers import DjangoEmailProvider


@shared_task(name="apps.accounts.tasks.send_team_invite_email")
def send_team_invite_email(invite_id, confirm_url):
    from .models import PendingVerification

    invite = (
        PendingVerification.objects.filter(
            pk=invite_id, kind=PendingVerification.Kind.TEAM_INVITE, consumed_at__isnull=True,
        )
        .select_related("project", "invited_by")
        .first()
    )
    if invite is None:
        return
    inviter = (
        invite.invited_by.get_full_name() or invite.invited_by.email
        if invite.invited_by else "A team owner"
    )
    DjangoEmailProvider().send(
        to=invite.email,
        subject=f"You're invited to join {invite.project.name}",
        body=(
            f"{inviter} invited you to join {invite.project.name} as "
            f"{invite.get_role_display()}.\n\nAccept the invite:\n{confirm_url}\n\n"
            "This link expires in 7 days. If you weren't expecting this, ignore it."
        ),
    )


@shared_task(name="apps.accounts.tasks.send_signup_verification_email")
def send_signup_verification_email(pending_id, confirm_url):
    from .models import PendingVerification

    pending = PendingVerification.objects.filter(
        pk=pending_id, kind=PendingVerification.Kind.SELF_SIGNUP, consumed_at__isnull=True,
    ).first()
    if pending is None:
        return
    store_name = pending.payload.get("store_name", "your store")
    DjangoEmailProvider().send(
        to=pending.email,
        subject="Confirm your email to activate your store",
        body=(
            f"Confirm your email to finish setting up {store_name}:\n\n{confirm_url}\n\n"
            "This link expires in 48 hours. If you didn't request this, ignore it."
        ),
    )
