"""Crop/zoom uploader (Cropper.js), extracted from the store-logo form into a
reusable macro (templates/control/_image_crop.jinja) and wired to:

- Banner image/mobile_image — a ratio *picker* pre-selected to 1600:760 (the
  storefront's hero/promo/category/product shape), because a Banner also
  covers popup (natural ratio, no forced box) and announcement (image never
  shown) — those need 1:1 or Free, not a hard 1600:760 lock.
- Category image — hard-locked to 3:2, the storefront tile's one and only
  display shape, no ambiguity to pick from."""

from html.parser import HTMLParser

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.cms.models import Banner, BannerPlacement
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.projects.models import Project

User = get_user_model()


class _XDataCollector(HTMLParser):
    """A real HTML parser — unlike a substring check, this actually resolves
    attribute boundaries the way a browser does, so it catches a quote
    collision a substring match would miss (the corrupted markup still
    *contains* the expected substring, right up to the point a stray quote
    truncates the attribute)."""

    def __init__(self):
        super().__init__()
        self.values = []

    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            if name == "x-data" and value and "imageCropField" in value:
                self.values.append(value)


def _x_data_values(html):
    p = _XDataCollector()
    p.feed(html)
    return p.values


class _AdminBase(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username="cropadmin", email="cropadmin@t.test", password="pw",
        )
        self.project = Project.objects.create(name="CropCo", status="active")
        self.client.force_login(self.user)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()


class LogoCropRefactorTests(_AdminBase):
    """The logo cropper was refactored onto the new shared macro — must keep
    behaving exactly as before."""

    def test_logo_form_still_renders_the_cropper(self):
        resp = self.client.get("/admin/cms/store-profile/")
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn("imageCropField('id_logo'", body)
        self.assertIn("Crop logo", body)
        self.assertIn('id="preview-id_logo"', body)
        self.assertIn('id="crop-src-id_logo"', body)


class BannerCropFieldTests(_AdminBase):
    def test_new_banner_form_renders_ratio_picker_preselected_to_1600x760(self):
        resp = self.client.get("/admin/cms/banners/new/")
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn(
            "imageCropField('id_image', { aspectRatio: 2.1052631578947367, ratioKey: 'wide' })",
            body,
        )
        self.assertIn(
            "imageCropField('id_mobile_image', { aspectRatio: 2.1052631578947367, ratioKey: 'wide' })",
            body,
        )
        self.assertIn("Crop image", body)
        self.assertIn("Crop mobile image", body)
        # a picker IS offered (popup/announcement need a different shape)
        self.assertIn('text-slate-500">Ratio:<', body)
        self.assertIn("1:1 — popup", body)

    def test_create_banner_still_works_through_the_new_template(self):
        resp = self.client.post("/admin/cms/banners/new/", {
            "name": "Sale", "placement": BannerPlacement.PROMO,
            "heading": "Big Sale", "subheading": "", "cta_label": "", "cta_url": "",
            "priority": "100", "is_active": "on",
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(Banner.objects.filter(project=self.project, name="Sale").exists())

    def test_edit_banner_form_prefills_existing_image_preview(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        b = Banner.objects.create(project=self.project, name="X", placement=BannerPlacement.PROMO)
        b.image.save("b.png", SimpleUploadedFile("b.png", _png()), save=True)
        resp = self.client.get(f"/admin/cms/banners/{b.pk}/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b.image.url, resp.content.decode())


def _png():
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (1600, 760), "red").save(buf, format="PNG")
    buf.seek(0)
    return buf.read()


class CategoryCropFieldTests(_AdminBase):
    def test_category_form_renders_locked_ratio_cropper_for_image(self):
        resp = self.client.get("/admin/categories/new/")
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn("imageCropField('id_image', { aspectRatio: 1.5, ratioKey: 'free' })", body)
        self.assertIn("Crop image", body)
        # no picker offered — the tile only ever displays at 3:2
        self.assertNotIn('text-slate-500">Ratio:<', body)

    def test_create_category_still_works_through_the_new_template(self):
        resp = self.client.post("/admin/categories/new/", {
            "name": "Rings", "is_active": "on", "home_row": "below", "order": "0",
        })
        self.assertEqual(resp.status_code, 302)
        from apps.categories.models import Category

        self.assertTrue(Category.objects.filter(project=self.project, name="Rings").exists())


class RatioKeyAttributeQuotingTests(_AdminBase):
    """Regression: initial_ratio_key was embedded via |tojson (double-quoted)
    inside the double-quoted x-data="..." HTML attribute — e.g.
    x-data="imageCropField('id_image', { ..., ratioKey: "wide" })". The
    embedded double-quote closed the attribute early, truncating it right
    before `wide"`, so Alpine got a syntactically invalid expression and
    every reactive property on the component (open, cropped, ratioKey) came
    back "not defined" in the browser console — the crop modal for a Banner
    or Category image field with any initial_ratio was permanently broken:
    stuck showing (open state undefined -> Alpine's x-show throws instead of
    hiding it) with every button inert (no working scope to call into)."""

    def test_banner_image_and_mobile_image_x_data_is_valid_js(self):
        body = self.client.get("/admin/cms/banners/new/").content.decode()
        values = _x_data_values(body)
        self.assertEqual(len(values), 2)
        for v in values:
            self.assertIn("ratioKey: 'wide'", v)
            self.assertTrue(v.endswith("})"), v)  # the attribute wasn't truncated

    def test_category_image_x_data_is_valid_js(self):
        body = self.client.get("/admin/categories/new/").content.decode()
        values = _x_data_values(body)
        self.assertEqual(len(values), 1)
        self.assertIn("ratioKey: 'free'", values[0])
        self.assertTrue(values[0].endswith("})"), values[0])
