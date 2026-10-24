from django.apps import AppConfig


class SeoConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.seo"
    label = "seo"

    def ready(self):
        from django.db.models.signals import post_save

        from apps.catalog.models import Product
        from apps.categories.models import Category
        from apps.cms.models import Page

        from . import signals

        post_save.connect(signals.product_saved, sender=Product,
                          dispatch_uid="seo_indexnow_product")
        post_save.connect(signals.page_saved, sender=Page,
                          dispatch_uid="seo_indexnow_page")
        post_save.connect(signals.category_saved, sender=Category,
                          dispatch_uid="seo_indexnow_category")
