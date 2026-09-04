"""What each store role can do in Mission Control — a reference for the Team
screen.

This is derived from the access rules in the code, kept here as one list so the
UI and the docs agree:

* Everything under ``ActiveProjectMixin`` (catalog, orders, inventory,
  customers, coupons, CMS, shipping, SEO, reviews, webhooks) is open to any
  active member — **staff** included.
* Views mixed with ``StoreRoleRequiredMixin`` / ``required_store_roles =
  OWNER_MANAGER`` (payment providers, plan & billing, custom domains, skin
  upload, the onboarding wizard) need **manager** or **owner**.
* Granting or changing the ``owner`` / ``manager`` role, and anything that would
  drop the store below one owner, is **owner** only
  (``apps.accounts.team._actor_caps``).

If you change a mixin on a control view, update the matching bullet here.
"""

from .models import StoreRole

# Ordered most-privileged first, matching TEAM_ROLES.
ROLE_ACCESS = {
    StoreRole.OWNER: {
        "label": "Owner",
        "summary": "Full control of the store and its team.",
        "can": [
            "Everything a Manager can do",
            "Add, remove and re-role Owners and Managers",
            "Own the plan & billing relationship",
            "Transfer or close the store",
        ],
        "cannot": [],
    },
    StoreRole.MANAGER: {
        "label": "Manager",
        "summary": "Runs the store day to day and handles its setup.",
        "can": [
            "Everything a Staff member can do",
            "Set up payment providers",
            "Add and verify custom domains",
            "Upload and switch custom themes",
            "View and change the plan & billing",
            "Add, remove and re-role Staff",
            "Run the onboarding wizard",
        ],
        "cannot": [
            "Add or manage Owners and other Managers",
            "Close or transfer the store",
        ],
    },
    StoreRole.STAFF: {
        "label": "Staff",
        "summary": "Day-to-day store operations.",
        "can": [
            "Products, variants, collections and inventory",
            "Orders, fulfilment and refunds",
            "Customers and customer groups",
            "Coupons and discounts",
            "Pages, banners and navigation (CMS)",
            "Shipping methods and zones",
            "SEO settings and redirects",
            "Moderate product reviews",
            "Configure outbound webhooks",
        ],
        "cannot": [
            "Payment provider credentials",
            "Plan & billing",
            "Custom domains",
            "Upload a custom theme",
            "Add or remove team members",
        ],
    },
}

# For templates: a plain ordered list of {role, label, summary, can, cannot}.
TEAM_ROLE_ACCESS = [
    {"role": role, **data} for role, data in ROLE_ACCESS.items()
]


def role_access(role):
    return ROLE_ACCESS.get(role)
