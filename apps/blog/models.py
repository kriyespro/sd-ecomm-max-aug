"""Platform marketing blog — content for the apex marketing site
(shopinaday.com/blog/), not tenant-scoped. One shared library, edited by
platform staff in Mission Control (posts) or Django admin (categories/tags/
authors — low-churn lookup data, not worth their own CRUD screens).
"""

from django.db import models
from django.utils import timezone
from django.utils.html import strip_tags
from django.utils.text import slugify

from apps.core.html import sanitize_html
from apps.core.models import SeoFieldsModel, TimeStampedModel


class BlogAuthor(TimeStampedModel):
    name = models.CharField(max_length=120)
    role = models.CharField(max_length=120, blank=True, help_text='e.g. "Founder", "Growth lead".')
    bio = models.TextField(blank=True)
    avatar = models.ImageField(upload_to="blog/authors/", blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        from apps.media.services import shrink_image_field

        shrink_image_field(self.avatar, target_kb=40, max_edge=400)
        super().save(*args, **kwargs)


class BlogCategory(TimeStampedModel):
    name = models.CharField(max_length=80)
    slug = models.SlugField(max_length=90, unique=True, blank=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "blog categories"

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)[:90]
        super().save(*args, **kwargs)


class BlogTag(TimeStampedModel):
    name = models.CharField(max_length=60)
    slug = models.SlugField(max_length=70, unique=True, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)[:70]
        super().save(*args, **kwargs)


class BlogPost(TimeStampedModel, SeoFieldsModel):
    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True, blank=True)
    excerpt = models.CharField(
        max_length=300, blank=True,
        help_text="Shown on the blog list + used as the meta description fallback.",
    )
    body = models.TextField(blank=True)
    cover_image = models.ImageField(upload_to="blog/covers/", blank=True)

    category = models.ForeignKey(
        BlogCategory, on_delete=models.SET_NULL, null=True, blank=True, related_name="posts",
    )
    tags = models.ManyToManyField(BlogTag, blank=True, related_name="posts")
    author = models.ForeignKey(
        BlogAuthor, on_delete=models.SET_NULL, null=True, blank=True, related_name="posts",
    )

    is_published = models.BooleanField(default=False)
    published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-published_at", "-created_at"]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        from apps.media.services import shrink_image_field

        if not self.slug:
            base = slugify(self.title)[:220] or "post"
            slug = base
            i = 2
            while BlogPost.objects.exclude(pk=self.pk).filter(slug=slug).exists():
                slug = f"{base}-{i}"[:220]
                i += 1
            self.slug = slug
        if self.is_published and not self.published_at:
            self.published_at = timezone.now()
        self.body = sanitize_html(self.body)
        shrink_image_field(self.cover_image, target_kb=150, max_edge=1600)
        super().save(*args, **kwargs)

    @property
    def is_live(self):
        return self.is_published and bool(self.published_at) and self.published_at <= timezone.now()

    @property
    def reading_minutes(self):
        words = len(strip_tags(self.body).split())
        return max(1, round(words / 200))

    def related(self, limit=3):
        qs = BlogPost.objects.filter(is_published=True).exclude(pk=self.pk)
        if self.category_id:
            same_category = list(qs.filter(category_id=self.category_id)[:limit])
            if len(same_category) >= limit:
                return same_category
            rest = list(qs.exclude(pk__in=[p.pk for p in same_category])[:limit - len(same_category)])
            return same_category + rest
        return list(qs[:limit])


def published_posts():
    return BlogPost.objects.filter(is_published=True, published_at__lte=timezone.now())
