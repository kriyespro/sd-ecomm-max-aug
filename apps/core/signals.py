"""Bust the cached Host->store and project->skin bindings when their inputs change.

See :mod:`apps.core.store_resolver`.
"""

from django.db.models.signals import m2m_changed, post_delete, post_save
from django.dispatch import receiver

from apps.categories.models import Category
from apps.cms.models import Banner, Page, Skin, ThemeSettings
from apps.projects.models import Domain, Project
from apps.shipping.models import ShippingMethod

from .store_resolver import bust_host, bust_project_chrome, bust_project_skin


@receiver(post_save, sender=Domain, dispatch_uid="core_bust_domain_save")
@receiver(post_delete, sender=Domain, dispatch_uid="core_bust_domain_delete")
def _bust_on_domain(sender, instance, **kwargs):
    bust_host(instance.host)


@receiver(post_save, sender=Project, dispatch_uid="core_bust_project_save")
def _bust_on_project(sender, instance, **kwargs):
    # primary_domain may have changed; allowed_skins handled by m2m below.
    if instance.primary_domain:
        bust_host(instance.primary_domain)
    bust_project_skin(instance.pk)


@receiver(post_save, sender=ThemeSettings, dispatch_uid="core_bust_theme_save")
@receiver(post_delete, sender=ThemeSettings, dispatch_uid="core_bust_theme_delete")
def _bust_on_theme(sender, instance, **kwargs):
    bust_project_skin(instance.project_id)
    bust_project_chrome(instance.project_id)


@receiver(post_save, sender=Category, dispatch_uid="core_bust_category_save")
@receiver(post_delete, sender=Category, dispatch_uid="core_bust_category_delete")
@receiver(post_save, sender=Page, dispatch_uid="core_bust_page_save")
@receiver(post_delete, sender=Page, dispatch_uid="core_bust_page_delete")
@receiver(post_save, sender=Banner, dispatch_uid="core_bust_banner_save")
@receiver(post_delete, sender=Banner, dispatch_uid="core_bust_banner_delete")
@receiver(post_save, sender=ShippingMethod, dispatch_uid="core_bust_ship_save")
@receiver(post_delete, sender=ShippingMethod, dispatch_uid="core_bust_ship_delete")
def _bust_chrome(sender, instance, **kwargs):
    bust_project_chrome(instance.project_id)


@receiver(post_save, sender=Skin, dispatch_uid="core_bust_skin_save")
@receiver(post_delete, sender=Skin, dispatch_uid="core_bust_skin_delete")
def _bust_on_skin(sender, instance, **kwargs):
    # A skin's active/default/approval state feeds every project's resolution;
    # the per-project keys carry a short TTL, so clear the one we can pin and
    # let the rest lapse.
    if instance.project_id:
        bust_project_skin(instance.project_id)


@receiver(m2m_changed, sender=Project.allowed_skins.through,
          dispatch_uid="core_bust_allowed_skins")
def _bust_on_allowed_skins(sender, instance, action, **kwargs):
    if action in ("post_add", "post_remove", "post_clear"):
        bust_project_skin(getattr(instance, "pk", None))
