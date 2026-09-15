"""Easy-mode dashboard checklist — the handful of things a brand-new store
needs before its first real sale. Shown only when Profile.ui_mode == "easy"
(see apps.control.context_processors.control)."""

from django.urls import reverse


def quick_launch_steps(project):
    """[{label, done, url}, ...] in the order a new owner should do them."""
    from apps.catalog.models import Product
    from apps.cms.models import StoreProfile
    from apps.payments.models import PaymentProviderConfig
    from apps.shipping.models import ShippingMethod

    store_profile = StoreProfile.objects.filter(project=project).first()
    profile_done = bool(
        store_profile and (store_profile.logo or store_profile.support_email)
    )
    has_product = Product.objects.filter(project=project, status="active").exists()
    has_payment = PaymentProviderConfig.objects.filter(
        project=project, is_enabled=True,
    ).exists()
    has_shipping = ShippingMethod.objects.filter(project=project, is_active=True).exists()

    return [
        {"label": "Add your store details", "done": profile_done,
         "url": reverse("control:cms_store_profile")},
        {"label": "Add your first product", "done": has_product,
         "url": reverse("control:product_create")},
        {"label": "Connect a payment method", "done": has_payment,
         "url": reverse("control:payment_providers")},
        {"label": "Set up shipping", "done": has_shipping,
         "url": reverse("control:shipping_zones")},
    ]
