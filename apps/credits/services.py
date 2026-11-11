"""Store credit: redeem a gift card or earned loyalty reward, spend it at
checkout, reverse it if the order is cancelled. ``_apply_ledger_entry`` is the
one place a balance ever changes -- every public function below goes through
it, same shape as ``apps.inventory.services.record_movement``.
"""

from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from django.utils.crypto import get_random_string

from .models import (
    CreditAccount, CreditKind, CreditLedger, GiftCard, GiftCardStatus,
    LoyaltyEarning, LoyaltyProgram,
)

CENTS = Decimal("0.01")


class CreditError(Exception):
    pass


def _generate_code():
    # No 0/O/1/I -- avoids a code that's ambiguous when read aloud or typed
    # (same alphabet apps.referrals.models uses for referral codes).
    return get_random_string(10, allowed_chars="ABCDEFGHJKLMNPQRSTUVWXYZ23456789")


def issue_gift_card(*, project, value, issued_to_email="", expires_at=None, actor=None):
    """Owner-issued gift card. Emails the code to the recipient (if given) —
    they redeem it from their account page once they sign up or sign in."""
    code = _generate_code()
    while GiftCard.objects.filter(project=project, code=code).exists():
        code = _generate_code()
    card = GiftCard.objects.create(
        project=project, code=code, value=value,
        issued_to_email=(issued_to_email or "").strip().lower(), expires_at=expires_at,
    )
    if card.issued_to_email:
        from apps.notifications.models import Event
        from apps.notifications.services import notify

        notify(
            project=project, event=Event.GIFT_CARD_ISSUED, to=card.issued_to_email,
            context={
                "store_name": project.name, "currency": project.currency,
                "value": str(card.value), "code": card.code,
            },
            related=card,
        )
    return card


def get_or_create_account(project, customer):
    account, _ = CreditAccount.objects.get_or_create(
        project=project, customer=customer, defaults={"balance": Decimal("0")},
    )
    return account


@transaction.atomic
def _apply_ledger_entry(account, *, kind, amount, order=None, gift_card=None, note=""):
    locked = CreditAccount.objects.select_for_update().get(pk=account.pk)
    new_balance = locked.balance + amount
    if new_balance < 0:
        raise CreditError("Not enough store credit for this.")
    locked.balance = new_balance
    locked.save(update_fields=["balance", "updated_at"])
    return CreditLedger.objects.create(
        project=locked.project, account=locked, kind=kind, amount=amount,
        balance_after=new_balance, order=order, gift_card=gift_card, note=note,
    )


@transaction.atomic
def redeem_gift_card(*, project, customer, code):
    code = (code or "").strip().upper()
    if not code:
        raise CreditError("Enter a gift card code.")
    try:
        card = GiftCard.objects.select_for_update().get(project=project, code=code)
    except GiftCard.DoesNotExist:
        raise CreditError("That gift card code is not valid.")
    if card.status != GiftCardStatus.ACTIVE or card.is_expired:
        raise CreditError("That gift card has already been used or has expired.")

    account = get_or_create_account(project, customer)
    _apply_ledger_entry(account, kind=CreditKind.GIFT_CARD, amount=card.value, gift_card=card)

    card.status = GiftCardStatus.REDEEMED
    card.redeemed_account = account
    card.redeemed_at = timezone.now()
    card.save(update_fields=["status", "redeemed_account", "redeemed_at", "updated_at"])
    return card.value


def earn_loyalty_for_payment(payment):
    order = getattr(payment, "order", None)
    if order is None:
        return None
    customer = getattr(order, "customer", None)
    if customer is None:
        return None
    program = LoyaltyProgram.objects.filter(project=order.project, is_active=True).first()
    if program is None:
        return None
    amount = program.earn_for(order.subtotal)
    if amount <= 0:
        return None

    with transaction.atomic():
        account = get_or_create_account(order.project, customer)
        earning, created = LoyaltyEarning.objects.get_or_create(
            project=order.project, order=order, defaults={"account": account, "amount": amount},
        )
        if not created:
            return earning
        _apply_ledger_entry(account, kind=CreditKind.LOYALTY_EARN, amount=amount, order=order)
    return earning


@transaction.atomic
def apply_credit_to_order(*, order, customer, amount):
    """Apply up to ``amount`` of store credit to ``order`` — silently capped
    at both the customer's balance and what's left owing on the order (so a
    simple "use my available credit" checkbox can just pass a large number).
    Only raises when nothing could be applied at all."""
    requested = Decimal(amount).quantize(CENTS)
    if requested <= 0:
        raise CreditError("Enter an amount of credit to use.")
    account = get_or_create_account(order.project, customer)
    locked = CreditAccount.objects.select_for_update().get(pk=account.pk)

    before_credit = order.subtotal - order.discount_total + order.tax_total + order.shipping_total
    amount = min(requested, locked.balance, before_credit).quantize(CENTS)
    if amount <= 0:
        raise CreditError("No store credit is available to apply to this order.")

    _apply_ledger_entry(locked, kind=CreditKind.REDEEMED, amount=-amount, order=order)
    order.credit_applied = amount
    order.save(update_fields=["credit_applied", "updated_at"])
    order.recalc_totals()
    return amount


def apply_available_credit(*, order, customer):
    """Convenience for a plain "use my store credit" checkout checkbox —
    applies as much of the customer's current balance as the order can use."""
    account = get_or_create_account(order.project, customer)
    if account.balance <= 0:
        raise CreditError("You don't have any store credit available.")
    return apply_credit_to_order(order=order, customer=customer, amount=account.balance)


def release_credit_for_cancelled_order(order):
    """Called when an order is cancelled/failed -- hand the spent credit
    back to the customer's balance."""
    if not order.credit_applied or order.credit_applied <= 0:
        return
    customer = getattr(order, "customer", None)
    if customer is None:
        return
    account = get_or_create_account(order.project, customer)
    with transaction.atomic():
        _apply_ledger_entry(
            account, kind=CreditKind.REFUND_REVERSAL, amount=order.credit_applied, order=order,
        )
        order.credit_applied = Decimal("0")
        order.save(update_fields=["credit_applied", "updated_at"])
        order.recalc_totals()
