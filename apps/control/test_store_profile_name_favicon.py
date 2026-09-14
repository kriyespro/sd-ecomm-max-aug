"""/admin/cms/store-profile/ — editing the store name (browser tab title +
storefront header fallback) and a dedicated favicon (browser tab icon),
alongside the existing logo/contact fields."""

import io

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from apps.accounts.models import Membership
from apps.billing import services as billing_svc
from apps.cms.models import StoreProfile
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.core.store_resolver import bust_project_chrome
from apps.projects.models import Domain, Project

User = get_user_model()


def _png():
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (64, 64), "blue").save(buf, format="PNG")
    buf.seek(0)
    return buf.read()


@override_settings(ALLOWED_HOSTS=["*"])
class StoreNameAndFaviconFormTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="OldName", status="active", feature_flags={"onboarded": True},
        )
        Domain.objects.create(project=self.project, host="oldname.test", is_verified=True)
        billing_svc.ensure_subscription(self.project)
        owner = User.objects.create_user("owner", "owner@t.test", "pw", is_staff=True)
        Membership.objects.create(user=owner, project=self.project, role="owner")
        self.client.force_login(owner)
        session = self.client.session
        session[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        session.save()

    def _base_payload(self, **extra):
        payload = {"store_name": "OldName", "show_payment_icons": "on"}
        payload.update(extra)
        return payload

    def test_store_name_field_is_prefilled_from_project_name(self):
        body = self.client.get("/admin/cms/store-profile/").content.decode()
        self.assertIn('value="OldName"', body)

    def test_saving_a_new_store_name_renames_the_project(self):
        resp = self.client.post(
            "/admin/cms/store-profile/", self._base_payload(store_name="New Name"),
        )
        self.assertEqual(resp.status_code, 302)
        self.project.refresh_from_db()
        self.assertEqual(self.project.name, "New Name")

    def test_new_store_name_appears_in_the_browser_title(self):
        self.client.post(
            "/admin/cms/store-profile/", self._base_payload(store_name="New Name"),
        )
        body = self.client.get("/", HTTP_HOST="oldname.test").content.decode()
        self.assertIn("<title>New Name</title>", body)

    def test_no_favicon_or_logo_renders_no_icon_link(self):
        body = self.client.get("/", HTTP_HOST="oldname.test").content.decode()
        self.assertNotIn('rel="icon"', body)

    def test_uploaded_favicon_renders_as_the_browser_icon(self):
        self.client.post(
            "/admin/cms/store-profile/",
            self._base_payload(favicon=SimpleUploadedFile("fav.png", _png(), content_type="image/png")),
        )
        profile = StoreProfile.objects.get(project=self.project)
        self.assertTrue(profile.favicon)
        bust_project_chrome(self.project.pk)

        body = self.client.get("/", HTTP_HOST="oldname.test").content.decode()
        self.assertIn('rel="icon" href="https://oldname.test/media/', body)

    def test_favicon_falls_back_to_logo_when_not_set(self):
        self.client.post(
            "/admin/cms/store-profile/",
            self._base_payload(logo=SimpleUploadedFile("logo.png", _png(), content_type="image/png")),
        )
        profile = StoreProfile.objects.get(project=self.project)
        self.assertTrue(profile.logo)
        self.assertFalse(profile.favicon)
        bust_project_chrome(self.project.pk)

        body = self.client.get("/", HTTP_HOST="oldname.test").content.decode()
        self.assertIn('rel="icon"', body)
        self.assertIn(profile.logo.url.rsplit("/", 1)[-1], body)
