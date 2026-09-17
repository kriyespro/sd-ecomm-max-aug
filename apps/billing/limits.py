"""Plan limit checks. Call from the control views that create limited resources.

Soft by design: a store with no subscription or a suspended one is treated as
the entry plan's limits, never as "blocked" (billing state gates the storefront,
not the admin — see apps.shopfront.middleware)."""

from django.core.exceptions import PermissionDenied


def _plan(project):
    sub = getattr(project, "subscription", None)
    return sub.plan if sub is not None else None


def usage(project) -> dict:
    """Current counts vs limits — for the plan screen UI."""
    plan = _plan(project)
    products = project.products.count() if hasattr(project, "products") else 0
    staff = project.memberships.filter(is_active=True, role__in=["owner", "manager", "staff"]).count()
    domains = project.domains.count() if hasattr(project, "domains") else 0
    return {
        "products": (products, plan.max_products if plan else None),
        "staff": (staff, plan.max_staff if plan else None),
        "custom_domains": (domains, plan.max_custom_domains if plan else None),
    }


NEAR_CAP_RATIO = 0.8


def is_near_cap(used, cap) -> bool:
    """True once usage crosses NEAR_CAP_RATIO of the plan's limit but
    hasn't hit it yet — an "approaching your limit" nudge, distinct from
    (and less severe than) the hard at-cap block/message. Shared by every
    screen that shows a used/cap pair (Products, Domains, Team) so the
    threshold lives in one place."""
    if cap is None or cap <= 0:
        return False
    return used < cap and used >= cap * NEAR_CAP_RATIO


def _check(project, attr, current, label):
    plan = _plan(project)
    if plan is None:
        return
    cap = getattr(plan, attr)
    if cap is not None and current >= cap:
        raise PermissionDenied(
            f"Your {plan.name} plan allows {cap} {label}. "
            f"Upgrade under Plan & billing to add more."
        )


def check_can_add_product(project):
    _check(project, "max_products", project.products.count(), "products")


def check_can_add_staff(project):
    n = project.memberships.filter(is_active=True, role__in=["owner", "manager", "staff"]).count()
    _check(project, "max_staff", n, "team members")


def check_can_add_domain(project):
    _check(project, "max_custom_domains", project.domains.count(), "custom domains")


def skin_upload_allowed(project) -> bool:
    plan = _plan(project)
    return bool(plan and plan.allow_skin_upload)
