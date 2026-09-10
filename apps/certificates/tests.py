"""Jewellery / gemstone certificates + storefront verification."""

import io
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

try:
    from PIL import Image
    _HAVE_PIL = True
except Exception:  # noqa: BLE001
    _HAVE_PIL = False

from apps.accounts.models import Membership, StoreRole
from apps.catalog.models import Product
from apps.certificates.models import Certificate, _ALPHABET, ensure_footer_page, lookup
from apps.cms.models import Page
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.projects.models import Project

User = get_user_model()


def _png(name="cert.png"):
    buf = io.BytesIO()
    Image.new("RGB", (20, 20), "white").save(buf, format="PNG")
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/png")


def _cert(project, **kw):
    kw.setdefault("title", "1.02ct Round Diamond")
    kw.setdefault("image_front", _png())
    return Certificate.objects.create(project=project, **kw)


@override_settings(MEDIA_ROOT="/tmp/sd-cert-tests")
class ModelTests(TestCase):
    def setUp(self):
        if not _HAVE_PIL:
            self.skipTest("Pillow not available")
        self.project = Project.objects.create(name="Gems", status="active", currency="INR")

    def test_code_minted_on_save(self):
        c = _cert(self.project)
        self.assertEqual(len(c.code), 8)
        self.assertTrue(all(ch in _ALPHABET for ch in c.code))

    def test_code_is_stable_on_edit(self):
        c = _cert(self.project)
        code = c.code
        c.title = "changed"
        c.save()
        self.assertEqual(c.code, code)

    def test_code_unique_per_project(self):
        codes = {_cert(self.project, image_front=_png()).code for _ in range(15)}
        self.assertEqual(len(codes), 15)

    def test_lookup_by_code_and_lab_number(self):
        c = _cert(self.project, lab_number="GIA-12345")
        self.assertEqual(lookup(self.project, c.code.lower()), c)
        self.assertEqual(lookup(self.project, "gia-12345"), c)
        self.assertIsNone(lookup(self.project, "nope"))

    def test_lookup_skips_non_public(self):
        c = _cert(self.project, is_public=False)
        self.assertIsNone(lookup(self.project, c.code))

    def test_lookup_scoped_to_project(self):
        other = Project.objects.create(name="Other", status="active")
        c = _cert(self.project)
        self.assertIsNone(lookup(other, c.code))

    def test_spec_rows_only_filled(self):
        c = _cert(self.project, gem_type="Diamond", carat_weight=Decimal("1.02"))
        labels = [k for k, _ in c.spec_rows]
        self.assertIn("Gem", labels)
        self.assertIn("Carat", labels)
        self.assertNotIn("Metal", labels)

    def test_ensure_footer_page_idempotent(self):
        ensure_footer_page(self.project)
        ensure_footer_page(self.project)
        pages = Page.objects.filter(project=self.project, slug="verify")
        self.assertEqual(pages.count(), 1)
        self.assertTrue(pages.first().is_live)


@override_settings(MEDIA_ROOT="/tmp/sd-cert-tests", ALLOWED_HOSTS=["*"])
class StorefrontVerifyTests(TestCase):
    def setUp(self):
        if not _HAVE_PIL:
            self.skipTest("Pillow not available")
        self.project = Project.objects.create(name="Gems", status="active",
                                              currency="INR", primary_domain="gems.example")
        self.cert = _cert(self.project, gem_type="Diamond", lab_number="IGI-99")

    def test_form_renders(self):
        r = self.client.get("/verify/", HTTP_HOST="gems.example")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"Verify", r.content)

    def test_valid_code_shows_certificate(self):
        r = self.client.get(f"/verify/?code={self.cert.code}", HTTP_HOST="gems.example")
        self.assertContains(r, self.cert.title)
        self.assertContains(r, "issued by")

    def test_path_style_code(self):
        r = self.client.get(f"/verify/{self.cert.code}/", HTTP_HOST="gems.example")
        self.assertContains(r, self.cert.title)

    def test_unknown_code_reports_not_found(self):
        r = self.client.get("/verify/?code=ZZZZ9999", HTTP_HOST="gems.example")
        self.assertContains(r, "No certificate matches")

    def test_private_certificate_not_shown(self):
        self.cert.is_public = False
        self.cert.save()
        r = self.client.get(f"/verify/?code={self.cert.code}", HTTP_HOST="gems.example")
        self.assertContains(r, "No certificate matches")

    def test_footer_page_slug_renders_verify(self):
        ensure_footer_page(self.project)
        r = self.client.get("/page/verify/", HTTP_HOST="gems.example")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"verification code", r.content.lower())


@override_settings(MEDIA_ROOT="/tmp/sd-cert-tests", ALLOWED_HOSTS=["*"])
class AdminScreenTests(TestCase):
    def setUp(self):
        if not _HAVE_PIL:
            self.skipTest("Pillow not available")
        self.store = Project.objects.create(name="Gems", status="active",
                                            feature_flags={"onboarded": True})
        self.owner = User.objects.create_user("o", "o@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.store, user=self.owner, role=StoreRole.OWNER)

    def _login(self):
        self.client.force_login(self.owner)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.store.pk
        s.save()

    def test_create_mints_code_and_footer_page(self):
        self._login()
        r = self.client.post("/admin/certificates/new/", {
            "title": "Emerald 2ct", "gem_type": "Emerald",
            "image_front": _png(), "is_public": "on",
        })
        self.assertEqual(r.status_code, 302)
        cert = Certificate.objects.get(project=self.store)
        self.assertEqual(len(cert.code), 8)
        self.assertTrue(Page.objects.filter(project=self.store, slug="verify").exists())

    def test_reject_non_image_extension(self):
        self._login()
        bad = SimpleUploadedFile("cert.gif", b"GIF89a", content_type="image/gif")
        r = self.client.post("/admin/certificates/new/", {
            "title": "X", "image_front": bad,
        })
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Certificate.objects.filter(project=self.store).exists())

    def test_edit_keeps_image_and_code(self):
        self._login()
        cert = _cert(self.store)
        r = self.client.post(f"/admin/certificates/{cert.pk}/", {
            "title": "Renamed", "is_public": "on",
        })
        self.assertEqual(r.status_code, 302)
        cert.refresh_from_db()
        self.assertEqual(cert.title, "Renamed")
        self.assertTrue(cert.image_front)

    def test_search(self):
        self._login()
        _cert(self.store, title="Ruby pendant")
        _cert(self.store, title="Sapphire ring")
        r = self.client.get("/admin/certificates/?q=ruby")
        self.assertContains(r, "Ruby pendant")
        self.assertNotContains(r, "Sapphire ring")
