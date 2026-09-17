"""Bulk trash on the media library — upload already accepted multiple
files in one submit, delete didn't. Reversible (30-day Trash), same as
the single-asset action."""

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import Membership, StoreRole
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.media.models import MediaAsset
from apps.projects.models import Project

User = get_user_model()


def _asset(project, name="a.jpg"):
    return MediaAsset.objects.create(
        project=project, file=ContentFile(b"x", name=name),
        original_name=name, kind="image", size=1,
    )


@override_settings(ALLOWED_HOSTS=["*"])
class MediaBulkDeleteTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="MediaCo", status="active", feature_flags={"onboarded": True},
        )
        self.owner = User.objects.create_user("mowner", "mowner@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.project, user=self.owner, role=StoreRole.OWNER)
        self.client.force_login(self.owner)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()
        self.a1 = _asset(self.project, "one.jpg")
        self.a2 = _asset(self.project, "two.jpg")

    def test_bulk_trash_moves_all_selected(self):
        resp = self.client.post("/admin/media/bulk-delete/", {
            "pks": [self.a1.pk, self.a2.pk],
        }, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.a1.refresh_from_db()
        self.a2.refresh_from_db()
        self.assertIsNotNone(self.a1.trashed_at)
        self.assertIsNotNone(self.a2.trashed_at)

    def test_no_selection_is_a_no_op(self):
        resp = self.client.post("/admin/media/bulk-delete/", {}, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.a1.refresh_from_db()
        self.assertIsNone(self.a1.trashed_at)

    def test_cannot_touch_another_projects_asset(self):
        other = Project.objects.create(name="OtherMediaCo", status="active")
        foreign = _asset(other, "foreign.jpg")
        self.client.post("/admin/media/bulk-delete/", {"pks": [foreign.pk]})
        foreign.refresh_from_db()
        self.assertIsNone(foreign.trashed_at)

    def test_staff_cannot_bulk_trash(self):
        staff = User.objects.create_user("mstaff", "mstaff@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.project, user=staff, role=StoreRole.STAFF)
        self.client.force_login(staff)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()
        resp = self.client.post("/admin/media/bulk-delete/", {"pks": [self.a1.pk]})
        self.assertEqual(resp.status_code, 403)

    def test_library_shows_checkboxes_and_toolbar(self):
        resp = self.client.get("/admin/media/")
        self.assertContains(resp, 'name="pks"')
        self.assertContains(resp, 'id="bulk-select-all"')
        self.assertContains(resp, "Trash selected")

    def test_trash_view_has_no_bulk_toolbar(self):
        MediaAsset.objects.filter(pk=self.a1.pk).update(trashed_at=timezone.now())
        resp = self.client.get("/admin/media/?trash=1")
        self.assertNotContains(resp, "bulk-media-form")
