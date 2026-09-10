"""Jewellery / gemstone certificates.

The store owner uploads a scan (JPG/PNG) of a lab certificate; we mint a short
public verification code. Anyone can look a code (or the lab's own number) up on
the storefront's "Verify certificate" page.
"""

import secrets

from django.db import models

from apps.core.models import TenantScopedModel

# Unambiguous alphabet — no 0/O/1/I.
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_CODE_LEN = 8
ALLOWED_UPLOAD_EXT = ("jpg", "jpeg", "png")


def _gen_code():
    return "".join(secrets.choice(_ALPHABET) for _ in range(_CODE_LEN))


class Certificate(TenantScopedModel):
    code = models.CharField(max_length=16, db_index=True, editable=False)

    title = models.CharField(max_length=200, help_text="e.g. “1.02 ct Round Brilliant Diamond”.")
    product = models.ForeignKey(
        "catalog.Product", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="certificates",
    )

    lab_name = models.CharField(max_length=120, blank=True, help_text="GIA, IGI, SGL, in-house…")
    lab_number = models.CharField(max_length=120, blank=True, db_index=True,
                                  help_text="The certificate number printed by the lab.")

    gem_type = models.CharField(max_length=80, blank=True)
    carat_weight = models.DecimalField(max_digits=7, decimal_places=3, null=True, blank=True)
    shape_cut = models.CharField(max_length=80, blank=True)
    color_grade = models.CharField(max_length=40, blank=True)
    clarity_grade = models.CharField(max_length=40, blank=True)
    measurements = models.CharField(max_length=120, blank=True)
    metal = models.CharField(max_length=80, blank=True, help_text="18K Gold, Platinum 950…")
    gross_weight = models.CharField(max_length=40, blank=True)
    issued_on = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)

    image_front = models.ImageField(upload_to="certificates/")
    image_back = models.ImageField(upload_to="certificates/", blank=True)

    is_public = models.BooleanField(
        default=True, help_text="Off = the code stops verifying on the storefront.")

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["project", "code"], name="uniq_certificate_code"),
        ]

    def __str__(self):
        return f"{self.code} · {self.title}"

    def save(self, *args, **kwargs):
        from apps.media.services import shrink_image_field

        if not self.code:
            self.code = self._unique_code()
        shrink_image_field(self.image_front, target_kb=400, max_edge=2000)
        shrink_image_field(self.image_back, target_kb=400, max_edge=2000)
        super().save(*args, **kwargs)

    def _unique_code(self):
        for _ in range(12):
            code = _gen_code()
            if not Certificate.objects.filter(project_id=self.project_id, code=code).exists():
                return code
        return _gen_code() + secrets.choice(_ALPHABET)

    @property
    def spec_rows(self):
        """(label, value) pairs that have a value — for the verify page."""
        pairs = [
            ("Gem", self.gem_type),
            ("Carat", f"{self.carat_weight:g}" if self.carat_weight is not None else ""),
            ("Shape / cut", self.shape_cut),
            ("Colour", self.color_grade),
            ("Clarity", self.clarity_grade),
            ("Measurements", self.measurements),
            ("Metal", self.metal),
            ("Gross weight", self.gross_weight),
            ("Lab", self.lab_name),
            ("Lab number", self.lab_number),
            ("Issued", self.issued_on.isoformat() if self.issued_on else ""),
        ]
        return [(k, v) for k, v in pairs if v]


def lookup(project, query):
    """Find a public certificate by our code or the lab's own number."""
    q = (query or "").strip()
    if not q or project is None:
        return None
    return (
        Certificate.objects
        .filter(project=project, is_public=True)
        .filter(models.Q(code__iexact=q) | models.Q(lab_number__iexact=q))
        .select_related("product")
        .first()
    )


def ensure_footer_page(project):
    """Create the storefront "Verify certificate" page (shows in the footer of
    every skin) the first time a store adds a certificate. Owner can rename /
    hide it afterwards like any CMS page."""
    from apps.cms.models import Page, PublishStatus

    Page.objects.get_or_create(
        project=project, slug="verify",
        defaults={
            "title": "Verify certificate",
            "status": PublishStatus.PUBLISHED,
            "body": "",
            "show_in_sitemap": False,
        },
    )
