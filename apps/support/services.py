"""Ticket state transitions — kept out of the views so both the store-side
thread and the admin queue apply the same rules."""

from django.utils import timezone

from .models import CLOSED_STATUSES, ReporterRole, Ticket, TicketMessage, TicketStatus, TicketVote


def add_message(ticket: Ticket, *, author, author_role: str, body: str,
                is_internal_note: bool = False) -> TicketMessage:
    """Post a reply. A reporter-side reply on a resolved/closed ticket reopens
    it — an internal note never changes status or is visible to the reporter."""
    msg = TicketMessage.objects.create(
        ticket=ticket, author=author, author_role=author_role,
        body=body, is_internal_note=is_internal_note,
    )
    if not is_internal_note and author_role != ReporterRole.ADMIN and ticket.status in CLOSED_STATUSES:
        ticket.status = TicketStatus.OPEN
        ticket.resolved_at = None
        ticket.save(update_fields=["status", "resolved_at", "updated_at"])
    return msg


def set_status(ticket: Ticket, status: str) -> Ticket:
    ticket.status = status
    ticket.resolved_at = timezone.now() if status in CLOSED_STATUSES else None
    ticket.save(update_fields=["status", "resolved_at", "updated_at"])
    return ticket


def toggle_vote(ticket: Ticket, user) -> bool:
    """Returns True if the user now has a vote on it, False if it was removed."""
    vote, created = TicketVote.objects.get_or_create(ticket=ticket, user=user)
    if created:
        return True
    vote.delete()
    return False
