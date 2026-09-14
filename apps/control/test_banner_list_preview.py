"""Banner list preview thumbnails — the list showed only name/placement/dates
before, no way to tell what a banner looked like (or whether a mobile crop
was even uploaded) without opening it. Adds a preview column with both
images and their pixel dimensions."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from apps.accounts.models import Membership
from apps.billing import services as billing_svc
from apps.cms.models import Banner, BannerPlacement
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.projects.models import Project

User = get_user_model()


def _png(size):
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, "blue").save(buf, format="PNG")
    buf.seek(0)
    return buf.read()


@override_settings(ALLOWED_HOSTS=["*"])
class BannerListPreviewTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="PreviewCo", status="active", feature_flags={"onboarded": True},
        )
        billing_svc.ensure_subscription(self.project)
        self.owner = User.objects.create_user("owner", "owner@t.test", "pw", is_staff=True)
        Membership.objects.create(user=self.owner, project=self.project, role="owner")
        self.client.force_login(self.owner)
        session = self.client.session
        session[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        session.save()

    def test_desktop_and_mobile_dimensions_both_shown(self):
        b = Banner.objects.create(
            project=self.project, placement=BannerPlacement.HERO, name="Sale",
        )
        b.image.save("d.png", SimpleUploadedFile("d.png", _png((1600, 400))), save=False)
        b.mobile_image.save("m.png", SimpleUploadedFile("m.png", _png((800, 1000))), save=True)

        resp = self.client.get("/admin/cms/banners/")
        body = resp.content.decode()
        self.assertIn("1600×400", body)
        self.assertIn("mobile", body)
        # mobile_image is shrunk on save (shrink_image_field, max_edge=900) —
        # just confirm *a* dimension pair renders, not the exact pre-shrink size.
        self.assertRegex(body, r"\d+×\d+\s*<span class=\"text-slate-300\">mobile</span>")

    def test_no_image_shows_placeholder_text(self):
        Banner.objects.create(project=self.project, placement=BannerPlacement.HERO, name="Empty")
        resp = self.client.get("/admin/cms/banners/")
        self.assertContains(resp, "No image")
