"""Dashboard onboarding checklist — different per role. Owner and DGC both
have real one-time setup and get tracked progress (checkmarks, N/M count).
Staff has nothing to configure, so theirs stays a plain orientation list.
Shown in Easy mode (see apps.control.context_processors.control) or once
a user toggles into it — /admin/ui-mode/toggle/."""

from django.urls import reverse


def owner_steps(project):
    """10 concrete setup actions, in the order a new owner should do them."""
    from apps.accounts.models import Membership, StoreRole
    from apps.catalog.models import Product
    from apps.categories.models import Category
    from apps.cms.models import Banner, BannerPlacement, StoreProfile
    from apps.coupons.models import Coupon
    from apps.payments.models import PaymentProviderConfig
    from apps.shipping.models import ShippingMethod

    store_profile = StoreProfile.objects.filter(project=project).first()
    profile_done = bool(
        store_profile and (store_profile.logo or store_profile.support_email)
    )
    has_product = Product.objects.filter(project=project, status="active").exists()
    has_category = Category.objects.filter(project=project).exists()
    has_theme = Banner.objects.filter(
        project=project, placement=BannerPlacement.HERO, is_active=True,
    ).exists()
    has_payment = PaymentProviderConfig.objects.filter(
        project=project, is_enabled=True,
    ).exists()
    has_shipping = ShippingMethod.objects.filter(project=project, is_active=True).exists()
    has_domain = bool((project.primary_domain or "").strip())
    has_team = Membership.objects.filter(
        project=project, is_active=True,
    ).exclude(role=StoreRole.OWNER).exists()
    has_coupon = Coupon.objects.filter(project=project).exists()

    core_done = [
        profile_done, has_product, has_category, has_theme,
        has_payment, has_shipping, has_domain, has_team, has_coupon,
    ]

    return [
        {"label": "Add your store details", "done": profile_done,
         "url": reverse("control:cms_store_profile")},
        {"label": "Add your first product", "done": has_product,
         "url": reverse("control:product_create")},
        {"label": "Organize with categories", "done": has_category,
         "url": reverse("control:category_list")},
        {"label": "Customize your theme", "done": has_theme,
         "url": reverse("control:cms_theme")},
        {"label": "Connect a payment method", "done": has_payment,
         "url": reverse("control:payment_providers")},
        {"label": "Set up shipping", "done": has_shipping,
         "url": reverse("control:shipping_zones")},
        {"label": "Connect a domain", "done": has_domain,
         "url": reverse("control:domains")},
        {"label": "Invite your team", "done": has_team,
         "url": reverse("control:team")},
        {"label": "Create a launch coupon", "done": has_coupon,
         "url": reverse("control:coupon_list")},
        {"label": "Preview & share your store", "done": all(core_done),
         "url": project.public_url or reverse("control:domains")},
    ]


def staff_steps():
    """A short "find your way around" list — staff don't configure the
    store, so these are orientation links, not a progress tracker."""
    return [
        {"label": "See today's orders", "done": False,
         "url": reverse("control:order_list")},
        {"label": "Browse the product catalogue", "done": False,
         "url": reverse("control:product_list")},
        {"label": "Know where to ask for help", "done": False,
         "url": reverse("control:support")},
    ]


def dgc_steps(user):
    """A DGC manages other people's stores, not their own — but unlike
    staff, they do have real one-time setup: a store to manage, a payout
    UPI so commissions can actually be paid, and their own referral link.
    Tracked, same as the owner list. (The old build pointed the third step
    at control:affiliate_overview, which is PlatformAdminRequiredMixin —
    a DGC who isn't also a superuser got a 403. That page is a superadmin
    view of every DGC; a DGC's own link lives on their own earnings page,
    control:my_commissions, same place the payout field is.)"""
    from apps.billing.models import Subscription

    profile = user.profile
    manages_store = Subscription.objects.filter(manager=user).exists()
    has_payout = bool(profile.payout_upi)
    has_shared_link = bool(profile.affiliate_code)

    return [
        {"label": "Add or pick a store to manage", "done": manages_store,
         "url": reverse("control:stores")},
        {"label": "Set your payout UPI", "done": has_payout,
         "url": reverse("control:my_commissions")},
        {"label": "Get your affiliate link", "done": has_shared_link,
         "url": reverse("control:my_commissions")},
    ]


def quick_launch_steps(project):
    """Back-compat name used by the dashboard for the owner/manager list."""
    return owner_steps(project)
