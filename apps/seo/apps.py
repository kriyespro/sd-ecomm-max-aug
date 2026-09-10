from django.apps import AppConfig


class SeoConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.seo"
    label = "seo"

    def ready(self):
        from django.db.models.signals import post_save

        from apps.catalog.models import Product

        from . import signals

        post_save.connect(signals.product_saved, sender=Product,
                          dispatch_uid="seo_indexnow_product")
