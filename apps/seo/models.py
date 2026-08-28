"""SEO: per-store defaults, per-path overrides, and redirects
(project.md section 19).
"""

from django.db import models

from apps.core.models import TenantScopedModel


class SeoSettings(TenantScopedModel):
    title_suffix = models.CharField(max_length=120, blank=True, help_text="Appended to page titles, e.g. ' | My Store'.")
    default_description = models.CharField(max_length=320, blank=True)
    default_og_image = models.ImageField(upload_to="seo/", blank=True)
    default_robots = models.CharField(max_length=60, default="index,follow")
    twitter_handle = models.CharField(max_length=60, blank=True)
    google_site_verification = models.CharField(max_length=120, blank=True)
    facebook_app_id = models.CharField(max_length=60, blank=True)
    organization_schema = models.JSONField(default=dict, blank=True)
    sitemap_enabled = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["project"], name="uniq_seosettings_per_project"),
        ]
        verbose_name = "SEO settings"
        verbose_name_plural = "SEO settings"

    def __str__(self):
        return f"SEO<{self.project_id}>"


class SeoMeta(TenantScopedModel):
    """Override the computed meta for an arbitrary storefront path."""

    path = models.CharField(max_length=300, db_index=True)
    title = models.CharField(max_length=180, blank=True)
    description = models.CharField(max_length=320, blank=True)
    canonical = models.CharField(max_length=300, blank=True)
    og_title = models.CharField(max_length=180, blank=True)
    og_description = models.CharField(max_length=320, blank=True)
    og_image = models.ImageField(upload_to="seo/", blank=True)
    robots = models.CharField(max_length=60, blank=True)
    structured_data = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["path"]
        constraints = [
            models.UniqueConstraint(fields=["project", "path"], name="uniq_seometa_path"),
        ]

    def __str__(self):
        return self.path


class Redirect(TenantScopedModel):
    from_path = models.CharField(max_length=300, db_index=True)
    to_path = models.CharField(max_length=300)
    is_permanent = models.BooleanField(default=True, help_text="301 when on, 302 when off.")
    is_active = models.BooleanField(default=True)
    hits = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["from_path"]
        constraints = [
            models.UniqueConstraint(fields=["project", "from_path"], name="uniq_redirect_from_path"),
        ]

    def __str__(self):
        return f"{self.from_path} -> {self.to_path}"

    @property
    def status_code(self):
        return 301 if self.is_permanent else 302
