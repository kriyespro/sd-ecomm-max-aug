from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.core"
    label = "core"

    def ready(self):
        from . import signals  # noqa: F401
        self._restrict_django_admin()

    @staticmethod
    def _restrict_django_admin():
        """The public self-signup sets ``is_staff=True`` (Mission Control keys
        off it), which by default also grants Django admin (/sd/) login. Narrow
        /sd/ to platform admins so an ordinary merchant can't reach it."""
        from django.contrib import admin

        def has_permission(request):
            user = request.user
            if not (user.is_active and user.is_staff):
                return False
            if user.is_superuser:
                return True
            profile = getattr(user, "profile", None)
            return bool(profile and profile.is_platform_admin)

        admin.site.has_permission = has_permission
