from django.apps import AppConfig


class MarketingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.marketing"
    verbose_name = "Marketing & tracking"

    def ready(self):
        from . import signals  # noqa: F401
