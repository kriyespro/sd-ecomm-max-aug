"""Storefront inline editor: who gets it, what it may change, undo/redo, hand-off."""

import io
import json

from django.contrib.auth import get_user_model
from django.core import signing
from django.core.management import call_command
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from apps.accounts.models import Membership, PlatformRole, Profile, StoreRole
from apps.cms.models import Banner, BannerPlacement, InlineEditLog, Page, StoreProfile, ThemeSettings
from apps.projects.models import Domain, Project
from apps.shopfront import inline_edit

User = get_user_model()
HOST = "acme.test"


def _png(color=(200, 30, 30)):
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (40, 20), color).save(buf, "PNG")
    return buf.getvalue()


@override_settings(ALLOWED_HOSTS=["*"])
class InlineEditTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="Acme", feature_flags={"onboarded": True})
        Domain.objects.create(project=self.project, host=HOST, is_verified=True)
        self.other = Project.objects.create(name="Other", feature_flags={"onboarded": True})
        self.owner = User.objects.create_user("owner", "o@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.project, user=self.owner, role=StoreRole.OWNER)
        self.staff = User.objects.create_user("staff", "s@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.project, user=self.staff, role=StoreRole.STAFF)
        self.shopper = User.objects.create_user("shopper", "sh@t.test", "pw")
        self.banner = Banner.objects.create(
            project=self.project, name="Hero", placement=BannerPlacement.HERO,
            heading="Spring drop", subheading="New in", cta_label="Shop", cta_url="/shop/",
        )
        self.page = Page.objects.create(project=self.project, title="About", slug="about", status="published",
                                        body="<p>Hello</p>")

    # --- helpers ---------------------------------------------------------
    def get(self, path="/", **kw):
        return self.client.get(path, HTTP_HOST=HOST, **kw)

    def save(self, kind, pk, field, value):
        return self.client.post(
            "/_edit/save/", data=json.dumps({"kind": kind, "pk": pk, "field": field, "value": value}),
            content_type="application/json", HTTP_HOST=HOST,
        )

    # --- who gets the editor ----------------------------------------------
    def test_anonymous_gets_no_markers_no_script_and_stays_cacheable(self):
        resp = self.get("/?edit=1")
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertNotIn("data-ed=", body)
        self.assertNotIn("inline-edit.js", body)
        self.assertIn("s-maxage", resp["Cache-Control"])

    def test_shopper_and_staff_role_get_nothing(self):
        for user in (self.shopper, self.staff):
            self.client.force_login(user)
            body = self.get("/?edit=1").content.decode()
            self.assertNotIn("data-ed=", body)
            self.assertNotIn("inline-edit.js", body)

    def test_owner_sees_pill_then_markers_in_edit_mode(self):
        self.client.force_login(self.owner)
        body = self.get("/").content.decode()
        self.assertIn("inline-edit.js", body)
        self.assertIn('data-active="0"', body)
        self.assertNotIn("data-ed=", body)

        resp = self.get("/?edit=1")
        body = resp.content.decode()
        self.assertIn('data-active="1"', body)
        self.assertIn(f'data-ed="banner:{self.banner.pk}:heading"', body)
        self.assertIn('data-ed="profile:0:logo"', body)
        self.assertIn("no-store", resp["Cache-Control"])
        # sticky across pages, switchable off
        self.assertIn(f'data-ed="page:{self.page.pk}:title"', self.get("/page/about/").content.decode())
        self.assertNotIn("data-ed=", self.get("/?edit=0").content.decode())

    def test_platform_admin_can_edit(self):
        admin = User.objects.create_superuser("root", "r@t.test", "pw")
        self.client.force_login(admin)
        self.assertIn("data-ed=", self.get("/?edit=1").content.decode())

    # --- saving ------------------------------------------------------------
    def test_text_save_logs_and_cleans(self):
        self.client.force_login(self.owner)
        r = self.save("banner", self.banner.pk, "heading", "  <b>Big</b>   sale  ")
        self.assertEqual(r.status_code, 200, r.content)
        self.banner.refresh_from_db()
        self.assertEqual(self.banner.heading, "Big sale")
        log = InlineEditLog.objects.get()
        self.assertEqual((log.old_value, log.new_value, log.user), ("Spring drop", "Big sale", self.owner))
        self.assertIn("Big sale", self.get("/").content.decode())  # chrome cache busted

    def test_validation_errors(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.save("banner", self.banner.pk, "heading", "x" * 500).status_code, 400)
        self.assertEqual(self.save("banner", self.banner.pk, "cta_url", "javascript:alert(1)").status_code, 400)
        self.assertEqual(self.save("banner", self.banner.pk, "cta_url", "//evil.test").status_code, 400)
        self.assertEqual(self.save("page", self.page.pk, "title", "   ").status_code, 400)
        self.assertEqual(self.save("profile", 0, "support_email", "nope").status_code, 400)
        self.assertEqual(self.save("theme", 0, "primary_color", "red").status_code, 400)
        self.assertFalse(InlineEditLog.objects.exists())
        self.assertEqual(self.save("banner", self.banner.pk, "cta_url", "https://ok.test/x").status_code, 200)

    def test_only_whitelisted_fields(self):
        self.client.force_login(self.owner)
        for field in ("name", "is_active", "placement", "project", "video_url"):
            self.assertEqual(self.save("banner", self.banner.pk, field, "x").status_code, 400, field)
        self.assertEqual(self.save("project", 1, "name", "x").status_code, 400)
        self.assertEqual(self.save("banner", self.banner.pk, "image", "evil/path.png").status_code, 400)

    def test_cannot_touch_another_stores_rows(self):
        theirs = Banner.objects.create(project=self.other, name="H", placement="hero", heading="Theirs")
        self.client.force_login(self.owner)
        self.assertEqual(self.save("banner", theirs.pk, "heading", "Hacked").status_code, 400)
        theirs.refresh_from_db()
        self.assertEqual(theirs.heading, "Theirs")

    def test_denied_for_staff_role_shopper_and_anonymous(self):
        for user in (None, self.shopper, self.staff):
            if user:
                self.client.force_login(user)
            else:
                self.client.logout()
            self.assertEqual(self.save("banner", self.banner.pk, "heading", "x").status_code, 403)
        self.banner.refresh_from_db()
        self.assertEqual(self.banner.heading, "Spring drop")

    def test_rich_body_is_sanitised(self):
        self.client.force_login(self.owner)
        r = self.save("page", self.page.pk, "body", '<p>Hi</p><script>alert(1)</script><img src=x onerror=alert(1)>')
        self.assertEqual(r.status_code, 200)
        self.page.refresh_from_db()
        self.assertNotIn("<script", self.page.body)
        self.assertNotIn("onerror", self.page.body)
        self.assertIn("Hi", self.page.body)

    def test_singletons_are_created_on_demand(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.save("profile", 0, "tagline", "Made with care").status_code, 200)
        self.assertEqual(StoreProfile.objects.get(project=self.project).tagline, "Made with care")
        self.assertEqual(self.save("theme", 0, "primary_color", "#AA00BB").status_code, 200)
        self.assertEqual(ThemeSettings.objects.get(project=self.project).primary_color, "#aa00bb")

    # --- undo / redo ---------------------------------------------------------
    def test_undo_redo_roundtrip(self):
        self.client.force_login(self.owner)
        log_id = self.save("banner", self.banner.pk, "heading", "Changed").json()["log"]
        post = lambda url: self.client.post(url, data='{"id": %d}' % log_id,
                                            content_type="application/json", HTTP_HOST=HOST)
        r = post("/_edit/undo/")
        self.assertEqual((r.status_code, r.json()["value"]), (200, "Spring drop"))
        self.banner.refresh_from_db()
        self.assertEqual(self.banner.heading, "Spring drop")
        self.assertEqual(post("/_edit/undo/").status_code, 400)  # already undone
        r = post("/_edit/redo/")
        self.assertEqual((r.status_code, r.json()["value"]), (200, "Changed"))
        self.assertEqual(post("/_edit/redo/").status_code, 400)

    def test_cannot_undo_another_stores_log(self):
        theirs = Banner.objects.create(project=self.other, name="H", placement="hero", heading="B")
        log = InlineEditLog.objects.create(project=self.other, kind="banner", object_id=theirs.pk,
                                           field="heading", old_value="A", new_value="B")
        self.client.force_login(self.owner)
        r = self.client.post("/_edit/undo/", data='{"id": %d}' % log.pk,
                             content_type="application/json", HTTP_HOST=HOST)
        self.assertEqual(r.status_code, 400)
        theirs.refresh_from_db()
        self.assertEqual(theirs.heading, "B")

    # --- images --------------------------------------------------------------
    def _upload(self, name, data, kind="banner", pk=None, field="image"):
        return self.client.post("/_edit/image/", {
            "kind": kind, "pk": self.banner.pk if pk is None else pk, "field": field,
            "file": SimpleUploadedFile(name, data),
        }, HTTP_HOST=HOST)

    def test_image_upload_and_undo(self):
        self.client.force_login(self.owner)
        r = self._upload("hero.png", _png())
        self.assertEqual(r.status_code, 200, r.content)
        self.banner.refresh_from_db()
        self.assertTrue(self.banner.image)
        self.assertEqual(r.json()["value"], self.banner.image.url)
        log = InlineEditLog.objects.get()
        self.assertEqual(log.old_value, "")
        undo = self.client.post("/_edit/undo/", data='{"id": %d}' % log.pk,
                                content_type="application/json", HTTP_HOST=HOST)
        self.assertEqual(undo.status_code, 200)
        self.banner.refresh_from_db()
        self.assertFalse(self.banner.image)

    def test_logo_upload_creates_profile(self):
        self.client.force_login(self.owner)
        r = self._upload("logo.png", _png(), kind="profile", pk=0, field="logo")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(StoreProfile.objects.get(project=self.project).logo)

    def test_bad_uploads_rejected(self):
        self.client.force_login(self.owner)
        self.assertEqual(self._upload("x.png", b"not an image").status_code, 400)
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
        self.assertEqual(self._upload("x.svg", svg).status_code, 400)
        self.assertEqual(self._upload("x.png", _png(), field="heading").status_code, 400)
        self.assertFalse(InlineEditLog.objects.exists())

    # --- cross-host hand-off (platform admin / DGC) -----------------------------
    def test_handoff_grants_editor_only_session(self):
        admin = User.objects.create_user("dgc", "d@t.test", "pw", is_staff=True)
        Profile.objects.update_or_create(user=admin, defaults={"platform_role": PlatformRole.MANAGER})
        admin.refresh_from_db()
        from apps.billing import services as billing_svc

        sub = billing_svc.ensure_subscription(self.project)
        sub.manager = admin
        sub.save(update_fields=["manager"])

        url = inline_edit.handoff_url(admin, self.project)
        self.assertIn("sd_edit=", url)
        token = url.split("sd_edit=")[1]
        # a fresh browser on the store host — NOT logged in there
        resp = self.client.get(f"/?sd_edit={token}&edit=1", HTTP_HOST=HOST)
        self.assertEqual(resp.status_code, 302)
        self.assertNotIn("sd_edit", resp["Location"])
        body = self.get("/").content.decode()
        self.assertIn("data-ed=", body)
        self.assertEqual(self.save("banner", self.banner.pk, "heading", "By DGC").status_code, 200)
        self.assertEqual(InlineEditLog.objects.get().user, admin)
        # ...but no Mission Control access on that host
        self.assertNotEqual(self.client.get("/admin/", HTTP_HOST=HOST).status_code, 200)

    def test_handoff_rejects_bad_expired_and_wrong_store_tokens(self):
        self.client.force_login(self.owner)
        good = signing.dumps({"u": self.owner.pk, "p": self.project.pk}, salt=inline_edit.HANDOFF_SALT)
        wrong_store = signing.dumps({"u": self.owner.pk, "p": self.other.pk}, salt=inline_edit.HANDOFF_SALT)
        wrong_salt = signing.dumps({"u": self.owner.pk, "p": self.project.pk}, salt="other")
        self.client.logout()
        for tok in ("garbage", wrong_store, wrong_salt):
            resp = self.client.get(f"/?sd_edit={tok}&edit=1", HTTP_HOST=HOST)
            self.assertEqual(resp.status_code, 200)
            self.assertNotIn("data-ed=", resp.content.decode())
        with self.settings(SESSION_COOKIE_NAME="sessionid"):
            inline_edit.HANDOFF_MAX_AGE, old = -1, inline_edit.HANDOFF_MAX_AGE
            try:
                resp = self.client.get(f"/?sd_edit={good}&edit=1", HTTP_HOST=HOST)
            finally:
                inline_edit.HANDOFF_MAX_AGE = old
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("data-ed=", resp.content.decode())

    def test_handoff_not_issued_to_staff_role(self):
        self.assertIsNone(inline_edit.handoff_url(self.staff, self.project))

    def test_control_edit_store_redirects_with_token(self):
        self.client.force_login(self.owner)
        s = self.client.session
        s["active_project_id"] = self.project.pk
        s.save()
        resp = self.client.get("/admin/edit-store/", HTTP_HOST=HOST)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("sd_edit=", resp["Location"])
        self.assertTrue(resp["Location"].endswith("&edit=1"))


@override_settings(ALLOWED_HOSTS=["*"])
class InlineEditSkinCoverageTests(TestCase):
    """The skins that own their home.jinja carry the same editor markers."""

    def setUp(self):
        self.project = Project.objects.create(name="Acme", feature_flags={"onboarded": True})
        Domain.objects.create(project=self.project, host=HOST, is_verified=True)
        self.banner = Banner.objects.create(
            project=self.project, name="Hero", placement=BannerPlacement.HERO,
            heading="Spring drop", subheading="New in", cta_label="Shop", cta_url="/shop/",
        )
        self.promo = Banner.objects.create(
            project=self.project, name="Promo", placement=BannerPlacement.PROMO,
            heading="Sale", subheading="Now on", cta_label="Go",
        )
        self.admin = User.objects.create_superuser("root", "r@t.test", "pw")
        call_command("seed_festive_skin", verbosity=0)  # not registered by a migration

    def test_markers_present_per_skin(self):
        from apps.cms.models import Skin

        self.client.force_login(self.admin)
        for slug in ("default", "botanica2", "botanica3", "festive"):
            skin = Skin.objects.get(slug=slug)
            resp = self.client.get("/", HTTP_HOST=HOST, data={"preview_skin": skin.pk, "edit": "1"})
            self.assertEqual(resp.status_code, 200, slug)
            body = resp.content.decode()
            for field in ("heading", "subheading", "cta_label", "image"):
                self.assertIn(f'data-ed="banner:{self.banner.pk}:{field}"', body, f"{slug} hero {field}")
            self.assertIn(f'data-ed-link="banner:{self.banner.pk}:cta_url"', body, slug)
            self.assertIn(f'data-ed="banner:{self.promo.pk}:heading"', body, f"{slug} promo")
            self.assertIn('data-ed="profile:0:logo"', body, f"{slug} logo")
            self.assertIn('data-ed="profile:0:tagline"', body, f"{slug} footer")
            # and the same render, edit mode off, is marker-free
            off = self.client.get("/", HTTP_HOST=HOST, data={"preview_skin": skin.pk, "edit": "0"})
            self.assertNotIn("data-ed=", off.content.decode(), slug)


@override_settings(ALLOWED_HOSTS=["*"])
class InlineEditV2Tests(TestCase):
    """Product cards, menu labels, benefit tiles, popup banner, section order."""

    def setUp(self):
        from decimal import Decimal

        from apps.catalog.models import Product
        from apps.cms.models import BenefitItem, BenefitItemKind, Menu, MenuItem

        self.project = Project.objects.create(name="Acme", feature_flags={"onboarded": True})
        Domain.objects.create(project=self.project, host=HOST, is_verified=True)
        self.other = Project.objects.create(name="Other", feature_flags={"onboarded": True})
        self.owner = User.objects.create_user("owner", "o@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.project, user=self.owner, role=StoreRole.OWNER)
        self.product = Product.objects.create(
            project=self.project, title="Ring", price=Decimal("1200"), sale_price=Decimal("900"),
            status="active", is_featured=True, is_new_arrival=True,
        )
        menu = Menu.objects.create(project=self.project, name="Main", location="main", is_active=True)
        self.item = MenuItem.objects.create(menu=menu, label="Rings", link_type="url", url="/shop/")
        other_menu = Menu.objects.create(project=self.other, name="Main", location="main", is_active=True)
        self.other_item = MenuItem.objects.create(menu=other_menu, label="Theirs", link_type="url", url="/x/")
        self.why = BenefitItem.objects.create(project=self.project, kind=BenefitItemKind.WHY,
                                              title="Pure", description="Plant based")
        self.trust = BenefitItem.objects.create(project=self.project, kind=BenefitItemKind.TRUST,
                                                title="Free shipping", description="Over 999")
        self.popup = Banner.objects.create(project=self.project, name="Pop", placement=BannerPlacement.POPUP,
                                           heading="10% off", cta_label="Claim")
        Banner.objects.create(project=self.project, name="Hero", placement=BannerPlacement.HERO, heading="Hi")
        self.admin = User.objects.create_superuser("root", "r@t.test", "pw")
        call_command("seed_festive_skin", verbosity=0)

    def save(self, kind, pk, field, value):
        return self.client.post(
            "/_edit/save/", data=json.dumps({"kind": kind, "pk": pk, "field": field, "value": value}),
            content_type="application/json", HTTP_HOST=HOST,
        )

    def test_menu_label_scoped_to_store(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.save("menuitem", self.item.pk, "label", "Jewellery").status_code, 200)
        self.item.refresh_from_db()
        self.assertEqual(self.item.label, "Jewellery")
        self.assertEqual(self.save("menuitem", self.other_item.pk, "label", "Hacked").status_code, 400)
        self.assertEqual(self.save("menuitem", self.item.pk, "url", "/evil/").status_code, 400)
        self.other_item.refresh_from_db()
        self.assertEqual(self.other_item.label, "Theirs")

    def test_benefit_edit(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.save("benefit", self.why.pk, "title", "Pure & simple").status_code, 200)
        self.assertEqual(self.save("benefit", self.trust.pk, "icon", "★").status_code, 200)
        self.assertEqual(self.save("benefit", self.why.pk, "icon", "toolong").status_code, 400)
        self.assertEqual(self.save("benefit", self.why.pk, "kind", "trust").status_code, 400)
        self.why.refresh_from_db()
        self.assertEqual(self.why.title, "Pure & simple")

    def test_product_price_and_title(self):
        from decimal import Decimal

        self.client.force_login(self.owner)
        self.assertEqual(self.save("product", self.product.pk, "title", "Gold ring").status_code, 200)
        r = self.save("product", self.product.pk, "price", "1,499.5")
        self.assertEqual(r.status_code, 200, r.content)
        self.product.refresh_from_db()
        self.assertEqual((self.product.title, self.product.price), ("Gold ring", Decimal("1499.50")))
        self.assertEqual(self.product.slug, "ring")   # URL stays stable
        for bad in ("abc", "-5", "", "1e999", "NaN", "9" * 12):
            self.assertEqual(self.save("product", self.product.pk, "price", bad).status_code, 400, bad)
        # sale price may be cleared; undo restores it as a Decimal
        r = self.save("product", self.product.pk, "sale_price", "")
        self.assertEqual(r.status_code, 200)
        self.product.refresh_from_db()
        self.assertIsNone(self.product.sale_price)
        undo = self.client.post("/_edit/undo/", data=json.dumps({"id": r.json()["log"]}),
                                content_type="application/json", HTTP_HOST=HOST)
        self.assertEqual(undo.status_code, 200)
        self.product.refresh_from_db()
        self.assertEqual(self.product.sale_price, Decimal("900.00"))
        # fields outside the whitelist
        for field in ("cost_price", "status", "slug", "sku", "description"):
            self.assertEqual(self.save("product", self.product.pk, field, "1").status_code, 400, field)

    def test_cannot_edit_another_stores_product(self):
        from apps.catalog.models import Product

        theirs = Product.objects.create(project=self.other, title="Theirs", price=10, status="active")
        self.client.force_login(self.owner)
        self.assertEqual(self.save("product", theirs.pk, "price", "1").status_code, 400)

    def test_section_order_save_validate_undo(self):
        self.client.force_login(self.owner)
        order = ["new_arrivals", "featured", "categories", "promo", "testimonials"]
        r = self.save("theme", 0, "homepage_sections", order)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(ThemeSettings.objects.get(project=self.project).homepage_sections, order)
        self.assertEqual(self.save("theme", 0, "homepage_sections", ["nope"]).status_code, 400)
        self.assertEqual(self.save("theme", 0, "homepage_sections", "{bad").status_code, 400)
        # a partial list is completed with the skin's remaining sections
        r = self.save("theme", 0, "homepage_sections", ["featured"])
        self.assertEqual(json.loads(r.json()["value"])[0], "featured")
        self.assertEqual(len(json.loads(r.json()["value"])), 5)
        undo = self.client.post("/_edit/undo/", data=json.dumps({"id": r.json()["log"]}),
                                content_type="application/json", HTTP_HOST=HOST)
        self.assertEqual(undo.status_code, 200)
        self.assertEqual(ThemeSettings.objects.get(project=self.project).homepage_sections, order)

    def test_v2_markers_per_skin(self):
        from apps.cms.models import Skin

        self.client.force_login(self.admin)
        for slug in ("default", "botanica2", "botanica3", "festive", "ornza"):
            skin = Skin.objects.get(slug=slug)
            resp = self.client.get("/", HTTP_HOST=HOST, data={"preview_skin": skin.pk, "edit": "1"})
            self.assertEqual(resp.status_code, 200, slug)
            body = resp.content.decode()
            self.assertIn(f'data-ed-product="{self.product.pk}"', body, f"{slug} card edit button")
            self.assertIn('data-product="/admin/products/0/"', body, slug)
            self.assertNotIn(f'data-ed="product:{self.product.pk}:title"', body, f"{slug} inline title")
            self.assertNotIn("data-ed-money", body, f"{slug} inline price")
            self.assertIn(f'data-ed="menuitem:{self.item.pk}:label"', body, f"{slug} menu")
            self.assertIn("data-ed-section=", body, f"{slug} sections")
            self.assertIn("data-ed-popup", body, f"{slug} popup")
            self.assertIn(f'data-ed="banner:{self.popup.pk}:heading"', body, f"{slug} popup heading")
            self.assertNotIn("setTimeout(() => { try { if (!localStorage", body, f"{slug} popup auto-open")
            if slug in ("botanica2", "botanica3"):
                self.assertIn(f'data-ed="benefit:{self.why.pk}:title"', body, f"{slug} benefit")
                self.assertIn(f'data-ed="benefit:{self.trust.pk}:title"', body, f"{slug} trust")
            off = self.client.get("/", HTTP_HOST=HOST, data={"preview_skin": skin.pk, "edit": "0"})
            off_body = off.content.decode()
            for marker in ("data-ed=", "data-ed-money", "data-ed-section", "data-ed-popup", "data-ed-product"):
                self.assertNotIn(marker, off_body, f"{slug} {marker} leaked outside edit mode")
            self.assertIn("setTimeout(() => { try { if (!localStorage", off_body, f"{slug} popup must still auto-open")

    # --- product card -> admin form -> back ------------------------------------
    def test_card_edit_button_needs_a_real_login_else_inline_fallback(self):
        # hand-off (editor-only) session: no admin login on this host -> inline price/title
        self.client.logout()
        tok = inline_edit.handoff_url(self.admin, self.project)
        self.client.get(f"/?sd_edit={tok.split('sd_edit=')[1]}&edit=1", HTTP_HOST=HOST)
        body = self.client.get("/", HTTP_HOST=HOST).content.decode()
        self.assertNotIn("data-ed-product", body)
        self.assertIn(f'data-ed-money="{self.product.pk}"', body)
        self.assertIn(f'data-ed="product:{self.product.pk}:title"', body)

    def _admin_session(self):
        self.client.force_login(self.owner)
        s = self.client.session
        s["active_project_id"] = self.project.pk
        s.save()

    def test_product_form_returns_to_the_storefront(self):
        self._admin_session()
        url = f"/admin/products/{self.product.pk}/"
        page = self.client.get(url + "?next=/shop/%3Fcategory%3Drings", HTTP_HOST=HOST)
        self.assertEqual(page.status_code, 200)
        body = page.content.decode()
        self.assertIn('name="next" value="/shop/?category=rings"', body)
        self.assertIn("Back to your store", body)

        data = {"title": "Ring v2", "price": "1300", "status": "active", "kind": "simple",
                "next": "/shop/?category=rings"}
        resp = self.client.post(url, data, HTTP_HOST=HOST)
        errors = resp.context["form"].errors if resp.status_code == 200 and resp.context else None
        self.assertEqual(resp.status_code, 302, errors)
        self.assertEqual(resp["Location"], "/shop/?category=rings")

    def test_product_form_rejects_offsite_next(self):
        self._admin_session()
        url = f"/admin/products/{self.product.pk}/"
        for bad in ("https://evil.test/", "//evil.test/x", "javascript:alert(1)"):
            body = self.client.get(url + "?next=" + bad, HTTP_HOST=HOST).content.decode()
            self.assertNotIn("Back to your store", body, bad)
            for evil in ('name="next" value="https://evil', 'name="next" value="//evil', 'name="next" value="javascript'):
                self.assertNotIn(evil, body, bad)
