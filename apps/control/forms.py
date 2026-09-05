"""ModelForms for the control panel (project.md: use ModelForms, no manual validation).

Every form is bound to an ``active_project``; the ``project`` FK is never a form
field and related querysets are scoped to that project.
"""

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import SetPasswordForm
from django.contrib.auth.password_validation import validate_password

from decimal import Decimal

from apps.accounts.models import PlatformRole
from apps.catalog.models import Brand, Product, ProductType, Tag, Variant
from apps.categories.models import Category
from apps.cms.models import (
    FAQ,
    Banner,
    BannerPlacement,
    ContentBlock,
    Menu,
    MenuItem,
    Page,
    Skin,
    StoreProfile,
    ThemeSettings,
)
from apps.coupons.models import Coupon
from apps.customers.models import Customer, CustomerGroup
from apps.inventory.models import InventoryItem, Warehouse
from apps.notifications.models import NotificationSettings, NotificationTemplate
from apps.payments.models import PaymentProviderConfig
from apps.seo.models import Redirect, SeoMeta, SeoSettings
from apps.shipping.models import ShippingMethod, ShippingZone
from apps.webhooks.models import WebhookEndpoint

TEXT = "w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-slate-900 focus:outline-none"
CHECK = "h-4 w-4 rounded border-slate-300"


class ProjectScopedForm(forms.ModelForm):
    def __init__(self, *args, project=None, **kwargs):
        self.project = project
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            widget = field.widget
            if isinstance(widget, forms.CheckboxInput):
                widget.attrs.setdefault("class", CHECK)
            elif isinstance(widget, (forms.Select, forms.SelectMultiple)):
                widget.attrs.setdefault("class", TEXT)
            elif isinstance(widget, (forms.TextInput, forms.NumberInput, forms.Textarea, forms.EmailInput, forms.URLInput, forms.ClearableFileInput)):
                widget.attrs.setdefault("class", TEXT)

    def save(self, commit=True):
        obj = super().save(commit=False)
        if self.project is not None:
            obj.project = self.project
        if commit:
            obj.save()
            self.save_m2m()
        return obj


class CategoryForm(ProjectScopedForm):
    class Meta:
        model = Category
        fields = [
            "parent", "name", "slug", "description",
            "image", "banner", "icon",
            "is_active", "is_featured", "order",
            "seo_title", "seo_description", "seo_keywords",
        ]
        help_texts = {"slug": "Leave blank to auto-generate from the name."}
        widgets = {"slug": forms.TextInput(attrs={"placeholder": "auto from name"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["slug"].required = False
        qs = Category.objects.filter(project=self.project)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        self.fields["parent"].queryset = qs
        self.fields["parent"].required = False


class BrandForm(ProjectScopedForm):
    class Meta:
        model = Brand
        fields = ["name", "slug", "logo", "description", "is_active"]
        help_texts = {"slug": "Leave blank to auto-generate from the name."}
        widgets = {"slug": forms.TextInput(attrs={"placeholder": "auto from name"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["slug"].required = False


class ProductTypeForm(ProjectScopedForm):
    class Meta:
        model = ProductType
        fields = ["name", "kind"]
        help_texts = {"kind": "How products of this type behave: simple, variable (has options), digital or service."}


class TagForm(ProjectScopedForm):
    class Meta:
        model = Tag
        fields = ["name"]


class ProductForm(ProjectScopedForm):
    # Plain comma list instead of Product.tags' auto-generated
    # ModelMultipleChoiceField: a native <select multiple> over an
    # always-empty-at-first queryset (Tag rows only exist via a separate,
    # unlinked /admin/tags/ screen) made this field look unfillable — nobody
    # had ever created a Tag anywhere. Typed names are get_or_create'd on save,
    # same pattern as the Size/Colour quick builder.
    tags = forms.CharField(
        required=False,
        help_text="Comma separated — new tags are created automatically.",
    )

    class Meta:
        model = Product
        fields = [
            "title", "slug", "kind", "price", "sale_price", "cost_price", "status",
            "type", "brand", "category",
            "sku", "barcode", "hsn_sac", "tax_class",
            "short_description", "description",
            "weight", "length", "width", "height",
            "is_featured", "is_new_arrival", "is_bestseller", "search_indexed",
            "seo_title", "seo_description", "seo_keywords",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "short_description": forms.Textarea(attrs={"rows": 2}),
            "slug": forms.TextInput(attrs={"placeholder": "auto from title"}),
            "sku": forms.TextInput(attrs={"placeholder": "auto"}),
        }
        help_texts = {
            "slug": "Leave blank to auto-generate from the title.",
            "sku": "Leave blank to auto-generate.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["slug"].required = False
        self.fields["sku"].required = False
        self.fields["type"].queryset = ProductType.objects.filter(project=self.project)
        self.fields["brand"].queryset = Brand.objects.filter(project=self.project)
        self.fields["category"].queryset = Category.objects.filter(project=self.project)
        for f in ("type", "brand", "category"):
            self.fields[f].required = False
        # "tags" is declared on the class (not in Meta.fields, so Django's
        # auto _save_m2m leaves it alone) which puts it last; move it back
        # next to the other flags where it used to sit.
        order = [name for name in self.fields if name != "tags"]
        order.insert(order.index("search_indexed") + 1, "tags")
        self.order_fields(order)
        if self.instance.pk:
            self.fields["tags"].initial = ", ".join(
                self.instance.tags.order_by("name").values_list("name", flat=True)
            )

    def save(self, commit=True):
        obj = super().save(commit=commit)
        names = [n.strip() for n in self.cleaned_data.get("tags", "").split(",") if n.strip()]

        def _sync():
            tags = [
                Tag.objects.get_or_create(project=self.project, name__iexact=name, defaults={"name": name})[0]
                for name in dict.fromkeys(names)  # de-dupe, keep order
            ]
            obj.tags.set(tags)

        if commit:
            _sync()
        else:
            self.save_m2m = _sync
        return obj


class WarehouseForm(ProjectScopedForm):
    class Meta:
        model = Warehouse
        fields = [
            "name", "code", "is_active", "is_default",
            "address_line1", "address_line2", "city", "state", "postal_code", "country",
        ]


class InventoryItemForm(ProjectScopedForm):
    """Create a stock record for a product/variant at a warehouse."""

    initial_quantity = forms.IntegerField(min_value=0, initial=0, required=False)

    class Meta:
        model = InventoryItem
        fields = ["warehouse", "product", "variant", "low_stock_threshold"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["warehouse"].queryset = Warehouse.objects.filter(
            project=self.project, is_active=True
        )
        self.fields["product"].queryset = Product.objects.filter(project=self.project)
        self.fields["variant"].queryset = Variant.objects.filter(product__project=self.project)
        self.fields["variant"].required = False

    def clean(self):
        cleaned = super().clean()
        product = cleaned.get("product")
        variant = cleaned.get("variant")
        if variant and product and variant.product_id != product.id:
            self.add_error("variant", "Variant does not belong to the chosen product.")

        warehouse = cleaned.get("warehouse")
        if warehouse and product:
            dupe = InventoryItem.objects.filter(
                warehouse=warehouse, product=product, variant=variant
            )
            if self.instance.pk:
                dupe = dupe.exclude(pk=self.instance.pk)
            if dupe.exists():
                raise forms.ValidationError(
                    "A stock record already exists for this product/variant at this warehouse."
                )
        return cleaned

    def save(self, commit=True):
        # InventoryItem is not TenantScoped (project reached via warehouse);
        # skip ProjectScopedForm.save's project assignment.
        obj = forms.ModelForm.save(self, commit=False)
        if commit:
            obj.save()
            self.save_m2m()
        return obj


class PaymentProviderForm(ProjectScopedForm):
    class Meta:
        model = PaymentProviderConfig
        fields = [
            "provider", "display_name", "is_enabled", "is_test_mode",
            "priority", "credentials", "config",
        ]
        widgets = {
            "credentials": forms.Textarea(attrs={"rows": 4, "class": TEXT, "spellcheck": "false"}),
            "config": forms.Textarea(attrs={"rows": 3, "class": TEXT, "spellcheck": "false"}),
        }
        help_texts = {
            "credentials": 'JSON. Razorpay: {"key_id": "", "key_secret": "", "webhook_secret": ""}',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # provider is fixed once created (it's half of the unique key).
        if self.instance.pk:
            self.fields["provider"].disabled = True


class ShippingZoneForm(ProjectScopedForm):
    class Meta:
        model = ShippingZone
        fields = ["name", "is_active", "priority", "countries", "states", "postal_prefixes"]
        widgets = {
            "countries": forms.Textarea(attrs={"rows": 2, "class": TEXT}),
            "states": forms.Textarea(attrs={"rows": 2, "class": TEXT}),
            "postal_prefixes": forms.Textarea(attrs={"rows": 2, "class": TEXT}),
        }
        help_texts = {
            "countries": 'JSON list, e.g. ["IN"]. Empty = any country.',
            "postal_prefixes": 'JSON list of pincode prefixes, e.g. ["56", "4000"].',
        }


class ShippingMethodForm(ProjectScopedForm):
    class Meta:
        model = ShippingMethod
        fields = [
            "zone", "name", "carrier", "rate_type",
            "base_rate", "per_kg_rate", "free_over",
            "min_subtotal", "max_subtotal", "max_weight",
            "cod_available", "cod_fee",
            "min_days", "max_days", "is_active", "priority",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["zone"].queryset = ShippingZone.objects.filter(project=self.project)


class CustomerGroupForm(ProjectScopedForm):
    class Meta:
        model = CustomerGroup
        fields = ["name", "slug", "description", "discount_percent", "is_default"]


class CustomerForm(ProjectScopedForm):
    class Meta:
        model = Customer
        fields = [
            "first_name", "last_name", "phone", "group",
            "marketing_opt_in", "notes",
        ]
        widgets = {"notes": forms.Textarea(attrs={"rows": 3, "class": TEXT})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["group"].queryset = CustomerGroup.objects.filter(project=self.project)
        self.fields["group"].required = False


class CouponForm(ProjectScopedForm):
    class Meta:
        model = Coupon
        fields = [
            "code", "description",
            "discount_type", "value", "max_discount", "min_order_amount",
            "applies_to", "products", "categories", "customer_groups",
            "first_order_only",
            "usage_limit", "usage_limit_per_customer",
            "starts_at", "expires_at", "is_active",
        ]
        widgets = {
            "starts_at": forms.DateTimeInput(attrs={"type": "datetime-local", "class": TEXT}),
            "expires_at": forms.DateTimeInput(attrs={"type": "datetime-local", "class": TEXT}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["products"].queryset = Product.objects.filter(project=self.project)
        self.fields["categories"].queryset = Category.objects.filter(project=self.project)
        self.fields["customer_groups"].queryset = CustomerGroup.objects.filter(project=self.project)
        for f in ("products", "categories", "customer_groups"):
            self.fields[f].required = False


class StockAdjustForm(forms.Form):
    new_quantity = forms.IntegerField(min_value=0, widget=forms.NumberInput(attrs={"class": TEXT}))
    low_stock_threshold = forms.IntegerField(
        min_value=0, required=False, widget=forms.NumberInput(attrs={"class": TEXT})
    )
    note = forms.CharField(
        max_length=255, required=False, widget=forms.TextInput(attrs={"class": TEXT})
    )


# --- CMS -----------------------------------------------------------

class PageForm(ProjectScopedForm):
    class Meta:
        model = Page
        fields = [
            "kind", "title", "slug", "excerpt", "body",
            "status", "published_at", "show_in_sitemap", "template_key",
            "seo_title", "seo_description", "seo_keywords",
        ]
        widgets = {
            "body": forms.Textarea(attrs={"rows": 12, "class": TEXT}),
            "excerpt": forms.Textarea(attrs={"rows": 2, "class": TEXT}),
            "published_at": forms.DateTimeInput(attrs={"type": "datetime-local", "class": TEXT}),
        }


class BannerForm(ProjectScopedForm):
    class Meta:
        model = Banner
        fields = [
            "name", "placement", "image", "mobile_image",
            "heading", "subheading", "cta_label", "cta_url",
            "category", "starts_at", "ends_at", "priority", "is_active",
        ]
        widgets = {
            "starts_at": forms.DateTimeInput(attrs={"type": "datetime-local", "class": TEXT}),
            "ends_at": forms.DateTimeInput(attrs={"type": "datetime-local", "class": TEXT}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = Category.objects.filter(project=self.project)
        self.fields["category"].required = False
        self.fields["placement"].help_text = (
            "Where this banner shows on the storefront — "
            "hero: top of the home page · promo: full-width strip on the home page · "
            "category: top of that category's product listing (set the category below) · "
            "product: strip on every product page · popup: modal on the first home visit · "
            "announcement: the scrolling bar at the very top."
        )
        self.fields["category"].help_text = "Required when placement is “Category”."

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("placement") == BannerPlacement.CATEGORY and not cleaned.get("category"):
            self.add_error("category", "Choose a category for a category-placement banner.")
        return cleaned


class FAQForm(ProjectScopedForm):
    class Meta:
        model = FAQ
        fields = ["group", "question", "answer", "order", "is_active"]
        widgets = {"answer": forms.Textarea(attrs={"rows": 4, "class": TEXT})}


class ContentBlockForm(ProjectScopedForm):
    class Meta:
        model = ContentBlock
        fields = ["key", "name", "block_type", "is_active"]


class MenuForm(ProjectScopedForm):
    class Meta:
        model = Menu
        fields = ["name", "location", "is_active"]


class MenuItemForm(forms.ModelForm):
    class Meta:
        model = MenuItem
        fields = [
            "label", "link_type", "url", "page", "category",
            "parent", "open_in_new_tab", "order", "is_active",
        ]

    def __init__(self, *args, menu=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.menu = menu
        project = menu.project
        self.fields["page"].queryset = Page.objects.filter(project=project)
        self.fields["category"].queryset = Category.objects.filter(project=project)
        self.fields["parent"].queryset = MenuItem.objects.filter(menu=menu, parent__isnull=True)
        if self.instance.pk:
            self.fields["parent"].queryset = self.fields["parent"].queryset.exclude(pk=self.instance.pk)
        for f in ("page", "category", "parent"):
            self.fields[f].required = False
        for name, field in self.fields.items():
            widget = field.widget
            if isinstance(widget, forms.CheckboxInput):
                widget.attrs.setdefault("class", CHECK)
            else:
                widget.attrs.setdefault("class", TEXT)

    def save(self, commit=True):
        obj = super().save(commit=False)
        obj.menu = self.menu
        if commit:
            obj.save()
        return obj


class ThemeSettingsForm(ProjectScopedForm):
    class Meta:
        model = ThemeSettings
        fields = [
            "skin",
            "primary_color", "secondary_color", "accent_color",
            "font_body", "font_heading",
            "header_layout", "footer_layout", "button_style", "product_card_style",
            "custom_css",
        ]
        widgets = {
            "primary_color": forms.TextInput(attrs={"type": "color"}),
            "secondary_color": forms.TextInput(attrs={"type": "color"}),
            "accent_color": forms.TextInput(attrs={"type": "color"}),
            "custom_css": forms.Textarea(attrs={"rows": 6, "class": TEXT, "spellcheck": "false"}),
        }
        help_texts = {
            "skin": "The storefront template bundle. Ask an admin to unlock more.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.cms.skins import allowed_skins_for

        field = self.fields["skin"]
        if self.project is not None:
            field.queryset = allowed_skins_for(self.project)
        field.empty_label = "Default"


class PlatformUserCreateForm(forms.Form):
    """Platform admin mints a standalone account (no store required)."""

    email = forms.EmailField()
    first_name = forms.CharField(required=False)
    last_name = forms.CharField(required=False)
    platform_role = forms.ChoiceField(
        choices=PlatformRole.choices, initial=PlatformRole.NONE, required=False,
        help_text="Platform Owner / Manager get Mission Control access. "
                  "“None” = a plain account you can assign to a store later.",
    )
    new_password1 = forms.CharField(label="Password", widget=forms.PasswordInput, strip=False)
    new_password2 = forms.CharField(label="Confirm password", widget=forms.PasswordInput, strip=False)

    def clean_email(self):
        email = (self.cleaned_data["email"] or "").strip().lower()
        if get_user_model().objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("A user with that email already exists.")
        return email

    def clean(self):
        cleaned = super().clean()
        p1, p2 = cleaned.get("new_password1"), cleaned.get("new_password2")
        if p1 and p2 and p1 != p2:
            self.add_error("new_password2", "The two passwords don’t match.")
        elif p1:
            try:
                validate_password(p1)
            except forms.ValidationError as exc:
                self.add_error("new_password1", exc)
        return cleaned


class UserRoleChangeForm(forms.Form):
    """Platform admin promotes/demotes an existing account's platform role."""

    platform_role = forms.ChoiceField(
        choices=PlatformRole.choices, label="Platform role",
        help_text="Digital Growth Consultant (DGC) can create stores and earn "
                  "commission on the ones assigned to them.",
    )


class StoreManagerAssignForm(forms.Form):
    """Platform admin (re)assigns which DGC a store's commission credits to."""

    manager = forms.ModelChoiceField(
        required=False, label="DGC / marketing partner",
        queryset=get_user_model().objects.filter(
            profile__platform_role=PlatformRole.MANAGER, is_active=True
        ).order_by("email"),
        help_text="Leave blank for a direct signup with no DGC credited.",
    )


class AdminSetPasswordForm(SetPasswordForm):
    """Platform admin sets a new password for another user. Django's
    SetPasswordForm runs the configured password validators."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", TEXT)


class StoreProfileForm(ProjectScopedForm):
    class Meta:
        model = StoreProfile
        fields = [
            "logo", "tagline",
            "support_email", "support_phone", "whatsapp",
            "address", "gstin",
            "instagram_url", "facebook_url", "youtube_url", "x_url",
            "copyright_text", "show_payment_icons",
        ]
        widgets = {
            "address": forms.Textarea(attrs={"rows": 3, "class": TEXT}),
            "tagline": forms.TextInput(attrs={"class": TEXT}),
        }
        help_texts = {
            "logo": "PNG or SVG with a transparent background works best. "
                    "Shown in the storefront header; falls back to the store name.",
        }


class SkinUploadForm(forms.Form):
    label = forms.CharField(
        max_length=120, required=False,
        widget=forms.TextInput(attrs={"class": TEXT, "placeholder": "e.g. Midnight"}),
        help_text="Optional — defaults to the name in theme.json.",
    )
    bundle = forms.FileField(
        help_text="A .zip of the converted skin folder (templates + assets/). Max 10 MB.",
    )

    def clean_bundle(self):
        f = self.cleaned_data["bundle"]
        if not f.name.lower().endswith(".zip"):
            raise forms.ValidationError("Upload a .zip file.")
        if f.size > 10 * 1024 * 1024:
            raise forms.ValidationError("Bundle is over 10 MB.")
        return f


class SkinForm(forms.ModelForm):
    """Platform-level. Not project-scoped."""

    class Meta:
        model = Skin
        fields = ["slug", "label", "description", "preview_image",
                  "is_active", "capabilities"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3, "class": TEXT}),
            "capabilities": forms.Textarea(attrs={"rows": 3, "class": TEXT, "spellcheck": "false"}),
        }
        help_texts = {
            "slug": "Folder name under templates/shopfront/skins/. Deploy the "
                    "templates before a store can use it.",
            "capabilities": "Optional JSON, e.g. {\"variants\": true}.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            w = field.widget
            if isinstance(w, forms.CheckboxInput):
                w.attrs.setdefault("class", CHECK)
            elif not w.attrs.get("class"):
                w.attrs["class"] = TEXT
        if self.instance.pk:
            self.fields["slug"].disabled = True


# --- SEO -----------------------------------------------------------

class SeoSettingsForm(ProjectScopedForm):
    class Meta:
        model = SeoSettings
        fields = [
            "title_suffix", "default_description", "default_og_image", "default_robots",
            "twitter_handle", "google_site_verification", "facebook_app_id", "sitemap_enabled",
        ]
        widgets = {"default_description": forms.Textarea(attrs={"rows": 2, "class": TEXT})}


class SeoMetaForm(ProjectScopedForm):
    class Meta:
        model = SeoMeta
        fields = [
            "path", "title", "description", "canonical",
            "og_title", "og_description", "og_image", "robots",
        ]
        widgets = {"description": forms.Textarea(attrs={"rows": 2, "class": TEXT})}


class RedirectForm(ProjectScopedForm):
    class Meta:
        model = Redirect
        fields = ["from_path", "to_path", "is_permanent", "is_active"]


# --- Phase 11 ----------------------------------------------------

class WebhookEndpointForm(ProjectScopedForm):
    events_text = forms.CharField(
        required=False, widget=forms.Textarea(attrs={"rows": 3, "class": TEXT}),
        help_text="One event key per line (e.g. order.created). Blank = all events.",
    )

    class Meta:
        model = WebhookEndpoint
        fields = ["url", "description", "is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["events_text"].initial = "\n".join(self.instance.events or [])

    def clean_url(self):
        from apps.webhooks.services import WebhookURLError, validate_endpoint_url

        url = self.cleaned_data["url"]
        try:
            validate_endpoint_url(url)
        except WebhookURLError as exc:
            raise forms.ValidationError(str(exc))
        return url

    def save(self, commit=True):
        obj = super().save(commit=False)
        raw = self.cleaned_data.get("events_text", "")
        obj.events = [line.strip() for line in raw.splitlines() if line.strip()]
        if commit:
            obj.save()
        return obj


class NotificationSettingsForm(ProjectScopedForm):
    class Meta:
        model = NotificationSettings
        fields = ["from_email", "from_name", "reply_to",
                  "email_provider", "sms_provider"]


class NotificationTemplateForm(ProjectScopedForm):
    class Meta:
        model = NotificationTemplate
        fields = ["event", "channel", "subject", "body", "is_active"]
        widgets = {"body": forms.Textarea(attrs={"rows": 8, "class": TEXT})}


class B2BListingForm(forms.Form):
    product = forms.ModelChoiceField(queryset=Product.objects.none())
    wholesale_price = forms.DecimalField(
        min_value=Decimal("0.01"), max_digits=12, decimal_places=2,
        label="Wholesale price", help_text="What a reseller pays you per unit.",
    )

    def __init__(self, *args, project=None, **kwargs):
        super().__init__(*args, **kwargs)
        if project is not None:
            self.fields["product"].queryset = Product.objects.filter(project=project).order_by("title")


class B2BImportForm(forms.Form):
    markup_pct = forms.DecimalField(
        min_value=Decimal("0"), max_digits=6, decimal_places=2, initial=Decimal("20"),
        label="Your markup %",
        help_text="Your selling price = wholesale price + this markup.",
    )


class B2BShipForm(forms.Form):
    tracking_number = forms.CharField(required=False, max_length=120)
    courier = forms.CharField(required=False, max_length=120)


class B2BMarkPaidForm(forms.Form):
    payout_ref = forms.CharField(
        required=False, max_length=120, label="Reference / note",
        help_text="e.g. a UPI/bank transfer reference, for your own records.",
    )


class MediaUploadForm(forms.Form):
    file = forms.FileField()
    folder = forms.CharField(required=False, widget=forms.TextInput(attrs={"class": TEXT}))
    alt = forms.CharField(required=False, widget=forms.TextInput(attrs={"class": TEXT}))
    title = forms.CharField(required=False, widget=forms.TextInput(attrs={"class": TEXT}))
