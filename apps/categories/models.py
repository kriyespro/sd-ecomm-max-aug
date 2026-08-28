"""Nested product categories, scoped per project (project.md section 8)."""

from django.db import models
from django.utils.text import slugify

from apps.core.models import SeoFieldsModel, TenantScopedModel


class Category(TenantScopedModel, SeoFieldsModel):
    parent = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="children",
    )
    name = models.CharField(max_length=140)
    slug = models.SlugField(max_length=160, blank=True)
    description = models.TextField(blank=True)

    image = models.ImageField(upload_to="categories/images/", blank=True, null=True)
    banner = models.ImageField(upload_to="categories/banners/", blank=True, null=True)
    icon = models.ImageField(upload_to="categories/icons/", blank=True, null=True)

    is_active = models.BooleanField(default=True)
    is_featured = models.BooleanField(default=False)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name_plural = "categories"
        ordering = ["order", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "slug"], name="uniq_category_project_slug"
            )
        ]

    def __str__(self):
        return self.path

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.name)[:150] or "category"
            slug = base
            i = 2
            siblings = Category.objects.filter(project=self.project).exclude(pk=self.pk)
            while siblings.filter(slug=slug).exists():
                slug = f"{base}-{i}"
                i += 1
            self.slug = slug
        super().save(*args, **kwargs)

    @property
    def path(self):
        names = [self.name]
        node = self.parent
        seen = {self.pk}
        while node is not None and node.pk not in seen:
            names.append(node.name)
            seen.add(node.pk)
            node = node.parent
        return " / ".join(reversed(names))

    @property
    def depth(self):
        d = 0
        node = self.parent
        seen = {self.pk}
        while node is not None and node.pk not in seen:
            d += 1
            seen.add(node.pk)
            node = node.parent
        return d
