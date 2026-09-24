from django.apps import AppConfig


class ReferralsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.referrals"
    verbose_name = "Referral program"

    def ready(self):
        from . import signals  # noqa: F401
