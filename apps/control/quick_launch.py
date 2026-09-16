"""Dashboard onboarding checklist — different per role, since an owner sets
up a store, staff and a DGC don't. Owner/manager only shown in Easy mode
(see apps.control.context_processors.control); staff/DGC always show theirs
until they dismiss it (no setup actions to "complete" for those roles)."""

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


def dgc_steps():
    """A DGC manages other people's stores, not their own — orientation
    toward the platform-wide tools, not a single store's setup."""
    return [
        {"label": "Add or pick a store to manage", "done": False,
         "url": reverse("control:stores")},
        {"label": "Check your commissions", "done": False,
         "url": reverse("control:my_commissions")},
        {"label": "Review your affiliate tools", "done": False,
         "url": reverse("control:affiliate_overview")},
    ]


def quick_launch_steps(project):
    """Back-compat name used by the dashboard for the owner/manager list."""
    return owner_steps(project)
