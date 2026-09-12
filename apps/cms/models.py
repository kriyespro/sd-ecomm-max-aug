"""CMS: pages, reusable content blocks, banners, FAQs, menus, theme
(project.md sections 15, 16).

The storefront is headless, so everything a marketing page needs is authored
here and served as data. ``body`` holds rendered HTML; ``blocks`` is an ordered
list of section dicts for a block-based page builder — the frontend decides how
to render each ``type``.
"""

import re

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.text import slugify

from apps.core.html import sanitize_html
from apps.core.models import SeoFieldsModel, TenantScopedModel, TimeStampedModel

_YOUTUBE_ID_RE = re.compile(
    r"(?:youtube(?:-nocookie)?\.com/(?:shorts/|live/|embed/|watch\?(?:\S*&)?v=)"
    r"|youtu\.be/)([A-Za-z0-9_-]{11})"
)


def _extract_youtube_id(url):
    match = _YOUTUBE_ID_RE.search(url or "")
    return match.group(1) if match else ""


class PublishStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    PUBLISHED = "published", "Published"
    SCHEDULED = "scheduled", "Scheduled"


class PageKind(models.TextChoices):
    HOME = "home", "Home"
    ABOUT = "about", "About"
    CONTACT = "contact", "Contact"
    PRIVACY = "privacy", "Privacy policy"
    TERMS = "terms", "Terms & conditions"
    RETURN_POLICY = "return_policy", "Return policy"
    SHIPPING_POLICY = "shipping_policy", "Shipping policy"
    FAQ = "faq", "FAQ"
    CUSTOM = "custom", "Custom page"


class Page(TenantScopedModel, SeoFieldsModel):
    kind = models.CharField(max_length=20, choices=PageKind.choices, default=PageKind.CUSTOM)
    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, blank=True)
    excerpt = models.CharField(max_length=300, blank=True)
    body = models.TextField(blank=True)
    blocks = models.JSONField(default=list, blank=True)

    status = models.CharField(max_length=12, choices=PublishStatus.choices, default=PublishStatus.DRAFT, db_index=True)
    published_at = models.DateTimeField(null=True, blank=True)
    show_in_sitemap = models.BooleanField(default=True)
    template_key = models.CharField(max_length=60, blank=True, help_text="Frontend layout hint.")

    class Meta:
        ordering = ["title"]
        constraints = [
            models.UniqueConstraint(fields=["project", "slug"], name="uniq_page_slug_per_project"),
            models.UniqueConstraint(
                fields=["project", "kind"], condition=~models.Q(kind="custom"),
                name="uniq_page_kind_per_project",
            ),
        ]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.title)[:220] or self.kind
        if self.status == PublishStatus.PUBLISHED and not self.published_at:
            self.published_at = timezone.now()
        self.body = sanitize_html(self.body)
        super().save(*args, **kwargs)

    @property
    def is_live(self):
        if self.status == PublishStatus.PUBLISHED:
            return True
        if self.status == PublishStatus.SCHEDULED and self.published_at:
            return self.published_at <= timezone.now()
        return False


class BlockType(models.TextChoices):
    HERO = "hero", "Hero"
    RICHTEXT = "richtext", "Rich text"
    IMAGE = "image", "Image"
    VIDEO = "video", "Video"
    BUTTON = "button", "Button"
    SECTION = "section", "Section"
    GRID = "grid", "Grid"
    HTML = "html", "Raw HTML"


class ContentBlock(TenantScopedModel):
    key = models.SlugField(max_length=80, help_text="Referenced by the frontend, e.g. 'home-hero'.")
    name = models.CharField(max_length=120)
    block_type = models.CharField(max_length=20, choices=BlockType.choices, default=BlockType.RICHTEXT)
    data = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["key"]
        constraints = [
            models.UniqueConstraint(fields=["project", "key"], name="uniq_contentblock_key"),
        ]

    def __str__(self):
        return self.key


class BannerPlacement(models.TextChoices):
    HERO = "hero", "Hero"
    PROMO = "promo", "Promotional"
    CATEGORY = "category", "Category"
    PRODUCT = "product", "Product"
    POPUP = "popup", "Popup"
    ANNOUNCEMENT = "announcement", "Announcement bar"


class Banner(TenantScopedModel):
    name = models.CharField(max_length=120)
    placement = models.CharField(max_length=20, choices=BannerPlacement.choices, default=BannerPlacement.HERO)
    image = models.ImageField(upload_to="banners/", blank=True)
    mobile_image = models.ImageField(upload_to="banners/", blank=True)
    heading = models.CharField(max_length=200, blank=True)
    subheading = models.CharField(max_length=300, blank=True)
    cta_label = models.CharField(max_length=60, blank=True)
    cta_url = models.CharField(max_length=300, blank=True)

    category = models.ForeignKey(
        "categories.Category", on_delete=models.SET_NULL, null=True, blank=True, related_name="banners",
    )
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    priority = models.PositiveIntegerField(default=100)
    is_active = models.BooleanField(default=True)
    text_hidden = models.BooleanField(
        default=False,
        help_text="Show only the image — hide the heading, text and button.",
    )

    class Meta:
        ordering = ["placement", "priority", "id"]

    def __str__(self):
        return f"{self.name} ({self.placement})"

    def save(self, *args, **kwargs):
        from apps.media.services import shrink_image_field

        shrink_image_field(self.image, target_kb=170, max_edge=1920)
        shrink_image_field(self.mobile_image, target_kb=90, max_edge=900)
        super().save(*args, **kwargs)

    @property
    def is_live(self):
        if not self.is_active:
            return False
        now = timezone.now()
        if self.starts_at and now < self.starts_at:
            return False
        if self.ends_at and now > self.ends_at:
            return False
        return True


class UGCVideo(TenantScopedModel):
    """A short, vertical (portrait) YouTube (Shorts) video for the
    storefront's "Shorts" reel. Owner, manager and staff (and any DGC
    managing this store) can all add these (plain ``ActiveProjectMixin``
    scoping, same as Banner — no extra role gate)."""

    youtube_url = models.URLField(
        max_length=300, help_text="A YouTube Shorts (or any YouTube video) link.",
    )
    youtube_id = models.CharField(max_length=20, blank=True, editable=False)
    caption = models.CharField(max_length=200, blank=True)
    link_url = models.CharField(
        max_length=300, blank=True,
        help_text="Optional — where tapping the video sends a shopper, e.g. a product page.",
    )
    priority = models.PositiveIntegerField(default=100)
    is_active = models.BooleanField(default=True)
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )

    class Meta:
        ordering = ["priority", "id"]
        verbose_name = "UGC / Shorts video"

    def __str__(self):
        return self.caption or f"Short video #{self.pk}"

    def clean(self):
        if not _extract_youtube_id(self.youtube_url):
            raise ValidationError({"youtube_url": "Enter a valid YouTube link."})

    def save(self, *args, **kwargs):
        self.youtube_id = _extract_youtube_id(self.youtube_url)
        super().save(*args, **kwargs)

    @property
    def embed_url(self):
        if self.youtube_id:
            return f"https://www.youtube.com/embed/{self.youtube_id}?playsinline=1&modestbranding=1&rel=0"
        return ""

    @property
    def thumbnail_url(self):
        if self.youtube_id:
            return f"https://i.ytimg.com/vi/{self.youtube_id}/hqdefault.jpg"
        return ""


class BudgetBand(TenantScopedModel):
    """A "Shop by budget" tile — a price band the storefront links to as a
    filtered listing (``/shop/?min=&max=``). Owner-managed, same shape as a
    category tile."""

    label = models.CharField(max_length=80, help_text='e.g. "Under ₹499"')
    image = models.ImageField(upload_to="budget_bands/", blank=True)
    min_price = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        help_text="Lowest price in this band. Leave blank for no lower bound.",
    )
    max_price = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        help_text="Highest price in this band. Leave blank for no upper bound.",
    )
    order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["order", "id"]
        verbose_name = "budget band"

    def __str__(self):
        return self.label

    def save(self, *args, **kwargs):
        from apps.media.services import shrink_image_field

        shrink_image_field(self.image, target_kb=120, max_edge=900)
        super().save(*args, **kwargs)

    @property
    def query(self):
        parts = []
        if self.min_price is not None:
            parts.append(f"min={self.min_price:.0f}")
        if self.max_price is not None:
            parts.append(f"max={self.max_price:.0f}")
        return "&".join(parts)


class BenefitItem(TenantScopedModel):
    """One tile in the home page "Why it works" / benefit-band section —
    an icon glyph, a short title and a one-line description. Owner-managed;
    an empty list falls back to the skin's built-in defaults so an existing
    store's home page never goes blank."""

    icon = models.CharField(
        max_length=4, blank=True, default="✦",
        help_text="A single emoji or symbol, e.g. \U0001F33F or ✦.",
    )
    title = models.CharField(max_length=80)
    description = models.CharField(max_length=200, blank=True)
    order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["order", "id"]
        verbose_name = "benefit item"

    def __str__(self):
        return self.title


class InstagramItem(TenantScopedModel):
    """One image pulled from (or uploaded for) the store's Instagram feed
    section. ``source_url`` is the public post link the image came from —
    ``apps.cms.instagram.fetch_post_image`` populates ``image`` from its
    ``og:image``."""

    image = models.ImageField(upload_to="instagram/", blank=True)
    source_url = models.URLField(
        max_length=400, blank=True, help_text="The public Instagram post URL.",
    )
    caption = models.CharField(max_length=200, blank=True)
    link_url = models.CharField(
        max_length=400, blank=True,
        help_text="Where a tap sends a shopper. Defaults to the post URL.",
    )
    order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )

    class Meta:
        ordering = ["order", "id"]
        verbose_name = "Instagram item"

    def __str__(self):
        return self.caption or f"Instagram #{self.pk}"

    def save(self, *args, **kwargs):
        from apps.media.services import shrink_image_field

        shrink_image_field(self.image, target_kb=110, max_edge=1080)
        super().save(*args, **kwargs)

    @property
    def href(self):
        return self.link_url or self.source_url or ""


class FAQ(TenantScopedModel):
    group = models.CharField(max_length=80, blank=True, help_text="Optional grouping heading.")
    question = models.CharField(max_length=300)
    answer = models.TextField()
    order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["group", "order", "id"]
        verbose_name = "FAQ"

    def __str__(self):
        return self.question

    def save(self, *args, **kwargs):
        self.answer = sanitize_html(self.answer)
        super().save(*args, **kwargs)


class MenuLocation(models.TextChoices):
    MAIN = "main", "Main menu"
    FOOTER = "footer", "Footer menu"
    MOBILE = "mobile", "Mobile menu"
    CATEGORY = "category", "Category menu"


class Menu(TenantScopedModel):
    name = models.CharField(max_length=120)
    location = models.CharField(max_length=20, choices=MenuLocation.choices, default=MenuLocation.MAIN)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["location", "name"]

    def __str__(self):
        return f"{self.name} ({self.location})"


class MenuLinkType(models.TextChoices):
    URL = "url", "Internal URL / path"
    PAGE = "page", "CMS page"
    CATEGORY = "category", "Category"
    EXTERNAL = "external", "External URL"


class MenuItem(TimeStampedModel):
    menu = models.ForeignKey(Menu, on_delete=models.CASCADE, related_name="items")
    parent = models.ForeignKey("self", on_delete=models.CASCADE, null=True, blank=True, related_name="children")
    label = models.CharField(max_length=120)
    link_type = models.CharField(max_length=20, choices=MenuLinkType.choices, default=MenuLinkType.URL)
    url = models.CharField(max_length=400, blank=True)
    page = models.ForeignKey(Page, on_delete=models.SET_NULL, null=True, blank=True, related_name="menu_items")
    category = models.ForeignKey(
        "categories.Category", on_delete=models.SET_NULL, null=True, blank=True, related_name="menu_items",
    )
    open_in_new_tab = models.BooleanField(default=False)
    order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return self.label

    def resolved_url(self):
        # Storefront-relative (served at "/" on a store's own domain). The
        # rendered nav resolves these through reverse() — see
        # apps.shopfront.context._menu_href — this is the admin-preview value.
        if self.link_type == MenuLinkType.PAGE and self.page_id:
            return f"/page/{self.page.slug}/"
        if self.link_type == MenuLinkType.CATEGORY and self.category_id:
            return f"/shop/?category={self.category.slug}"
        return self.url


def _skin_asset_path(instance, filename):
    return f"skins/{instance.skin_id}/assets/{instance.path}"


class SkinSource(models.TextChoices):
    BUILTIN = "builtin", "Built-in (developer)"
    UPLOAD = "upload", "Uploaded"


class SkinStatus(models.TextChoices):
    PENDING = "pending", "Pending review"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"


class Skin(TimeStampedModel):
    """A storefront template bundle.

    Two kinds:

    * ``source=builtin`` — a folder at ``templates/shopfront/skins/<slug>/``,
      shipped by a developer, rendered with the normal (trusted) Jinja env.
    * ``source=upload`` — templates stored as :class:`SkinFile` rows, uploaded by
      a store owner/manager, rendered in a **sandbox** with a curated read-only
      context. Private to ``project`` until a platform admin promotes it
      (``project=None``). Never renders until ``status=approved``.
    """

    slug = models.SlugField(max_length=60, unique=True)
    label = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    preview_image = models.ImageField(upload_to="skins/previews/", blank=True, null=True)
    capabilities = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(
        default=True, help_text="Available for stores to use."
    )
    is_default = models.BooleanField(
        default=False, help_text="Used when a store has not picked a skin."
    )

    source = models.CharField(
        max_length=12, choices=SkinSource.choices, default=SkinSource.BUILTIN
    )
    is_sandboxed = models.BooleanField(
        default=False, help_text="Render in the restricted sandbox environment."
    )
    status = models.CharField(
        max_length=12, choices=SkinStatus.choices, default=SkinStatus.APPROVED
    )
    project = models.ForeignKey(
        "projects.Project", null=True, blank=True, on_delete=models.CASCADE,
        related_name="owned_skins",
        help_text="The store that uploaded this skin. Blank = shared / built-in.",
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="+",
    )
    version = models.CharField(max_length=20, blank=True)
    author = models.CharField(max_length=120, blank=True)
    review_note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["label"]
        constraints = [
            models.UniqueConstraint(
                fields=["is_default"],
                condition=Q(is_default=True),
                name="uniq_default_skin",
            ),
        ]

    def save(self, *args, **kwargs):
        from apps.media.services import shrink_image_field

        shrink_image_field(self.preview_image, target_kb=80, max_edge=800)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.label

    @property
    def template_root(self):
        return f"shopfront/skins/{self.slug}"

    @property
    def is_live(self):
        return self.is_active and self.status == SkinStatus.APPROVED


class SkinFile(TimeStampedModel):
    """One sandboxed template of an uploaded skin (e.g. ``partials/_card.jinja``)."""

    skin = models.ForeignKey(Skin, on_delete=models.CASCADE, related_name="files")
    path = models.CharField(max_length=160)
    content = models.TextField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["skin", "path"], name="uniq_skinfile_path"),
        ]
        ordering = ["path"]

    def __str__(self):
        return f"{self.skin.slug}:{self.path}"


class SkinAsset(TimeStampedModel):
    """A static asset (css/js/image/font) of an uploaded skin, served from media."""

    skin = models.ForeignKey(Skin, on_delete=models.CASCADE, related_name="assets")
    path = models.CharField(max_length=160)
    file = models.FileField(upload_to=_skin_asset_path, max_length=255)
    content_type = models.CharField(max_length=100, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["skin", "path"], name="uniq_skinasset_path"),
        ]
        ordering = ["path"]

    # No auto-shrink here: _skin_asset_path() derives the storage name from
    # instance.path (not the filename passed to .save()), so re-saving under
    # a different extension (as shrink_image_field always does — .webp) would
    # collide with the existing path and land WebP bytes under a randomised
    # name that still ends in the original extension — a broken content type.
    # Asset bundles are expected to arrive pre-optimized from the theme author.

    def __str__(self):
        return f"{self.skin.slug}:{self.path}"


class ThemeSettings(TenantScopedModel):
    """One row per project. Colours are first-class for the control form; the
    rest of the design system lives in ``tokens``.
    """

    skin = models.ForeignKey(
        "cms.Skin", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="theme_settings",
    )
    primary_color = models.CharField(max_length=9, default="#111111")
    secondary_color = models.CharField(max_length=9, default="#ffffff")
    accent_color = models.CharField(max_length=9, default="#2563eb")
    font_body = models.CharField(max_length=120, blank=True)
    font_heading = models.CharField(max_length=120, blank=True)
    header_layout = models.CharField(max_length=40, blank=True)
    footer_layout = models.CharField(max_length=40, blank=True)
    button_style = models.CharField(max_length=40, blank=True)
    product_card_style = models.CharField(max_length=40, blank=True)
    homepage_sections = models.JSONField(default=list, blank=True)
    tokens = models.JSONField(default=dict, blank=True)
    custom_css = models.TextField(blank=True)
    # Botanica 3.0: also show the "Shop by category" row above the hero.
    category_above_hero = models.BooleanField(default=False)
    # Botanica 3.0: show the text heading over the category rows (off = tiles only).
    show_category_headings = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["project"], name="uniq_theme_per_project"),
        ]
        verbose_name = "theme settings"
        verbose_name_plural = "theme settings"

    def __str__(self):
        return f"Theme<{self.project_id}>"

    def as_dict(self):
        return {
            "primary": self.primary_color,
            "secondary": self.secondary_color,
            "accent": self.accent_color,
            "fonts": {"body": self.font_body, "heading": self.font_heading},
            "layout": {
                "header": self.header_layout,
                "footer": self.footer_layout,
                "button": self.button_style,
                "product_card": self.product_card_style,
            },
            "homepage_sections": self.homepage_sections or [],
            "tokens": self.tokens or {},
        }


class StoreProfile(TenantScopedModel):
    """Store owner's public identity — logo + the contact / legal details a
    storefront footer shows. One row per project.
    """

    logo = models.ImageField(upload_to="stores/logos/", blank=True)
    tagline = models.CharField(
        max_length=200, blank=True,
        help_text="Short line under the logo in the footer.",
    )

    support_email = models.EmailField(blank=True)
    support_phone = models.CharField(max_length=32, blank=True)
    whatsapp = models.CharField(
        max_length=32, blank=True, help_text="Number in international format, e.g. +9198…",
    )
    address = models.TextField(
        blank=True, help_text="Full postal address, shown in the footer.",
    )
    gstin = models.CharField(max_length=20, blank=True, verbose_name="GSTIN")

    instagram_url = models.URLField(blank=True)
    facebook_url = models.URLField(blank=True)
    youtube_url = models.URLField(blank=True)
    x_url = models.URLField(blank=True, verbose_name="X / Twitter URL")

    copyright_text = models.CharField(
        max_length=200, blank=True,
        help_text="Footer copyright line. Defaults to “© <year> <store name>”.",
    )
    show_payment_icons = models.BooleanField(
        default=True, help_text="Show accepted-payment icons in the footer.",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["project"], name="uniq_storeprofile_per_project"),
        ]
        verbose_name = "store profile"
        verbose_name_plural = "store profiles"

    def __str__(self):
        return f"StoreProfile<{self.project_id}>"

    def save(self, *args, **kwargs):
        from apps.media.services import shrink_image_field

        shrink_image_field(self.logo, target_kb=40, max_edge=512)
        super().save(*args, **kwargs)

    @property
    def whatsapp_link(self):
        if not self.whatsapp:
            return ""
        digits = "".join(c for c in self.whatsapp if c.isdigit())
        return f"https://wa.me/{digits}" if digits else ""

    @property
    def social_links(self):
        return [
            (label, url)
            for label, url in (
                ("Instagram", self.instagram_url),
                ("Facebook", self.facebook_url),
                ("YouTube", self.youtube_url),
                ("X", self.x_url),
            )
            if url
        ]
