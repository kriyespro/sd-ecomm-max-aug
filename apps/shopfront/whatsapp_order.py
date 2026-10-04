"""WhatsApp ordering: build the pre-filled message + wa.me link for an order.

The order itself is always saved first (stock, customer, analytics all run as
for any other order); this only produces the hand-off to the seller's WhatsApp.
"""

from decimal import Decimal
from urllib.parse import quote

from apps.cms.models import StoreProfile

MAX_ITEM_LINES = 20
WHATSAPP_METHOD = "whatsapp"


def ordering_profile(project):
    """The store's StoreProfile if WhatsApp checkout is usable, else None."""
    profile = StoreProfile.objects.filter(project=project).first()
    return profile if profile and profile.whatsapp_ordering_on else None


def _money(value, currency="₹"):
    try:
        return f"{currency}{Decimal(value):,.0f}"
    except Exception:  # noqa: BLE001
        return f"{currency}{value}"


def build_message(order, store_name):
    addr = order.shipping_address or {}
    cur = "₹" if order.currency == "INR" else f"{order.currency} "
    lines = [f"*New order {order.number}* — {store_name}", ""]
    items = list(order.items.all())
    for i in items[:MAX_ITEM_LINES]:
        name = i.product_title + (f" ({i.variant_name})" if i.variant_name else "")
        lines.append(f"{i.quantity} x {name} - {_money(i.line_total, cur)}")
    if len(items) > MAX_ITEM_LINES:
        lines.append(f"+ {len(items) - MAX_ITEM_LINES} more item(s)")
    lines.append("")
    if order.discount_total and order.discount_total > 0:
        lines.append(f"Discount: -{_money(order.discount_total, cur)}")
    if order.shipping_total and order.shipping_total > 0:
        lines.append(f"Shipping: {_money(order.shipping_total, cur)}")
    lines.append(f"*Total: {_money(order.grand_total, cur)}*")
    lines.append("")
    lines.append(f"Name: {addr.get('name', '')}")
    phone = addr.get("phone") or order.phone
    if phone:
        lines.append(f"Phone: {phone}")
    place = ", ".join(
        p for p in (addr.get("line1"), addr.get("line2"), addr.get("city"),
                    addr.get("state"), addr.get("postal_code")) if p
    )
    if place:
        lines.append(f"Address: {place}")
    if order.customer_note:
        lines.append(f"Note: {order.customer_note[:300]}")
    return "\n".join(lines)


def build_link(profile, order, store_name):
    return f"https://wa.me/{profile.whatsapp_digits}?text={quote(build_message(order, store_name))}"
