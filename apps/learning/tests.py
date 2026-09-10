"""Learning / training videos."""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings

from apps.accounts.models import Membership, PlatformRole, Profile, StoreRole
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.learning.models import (
    LearningVideo,
    audience_keys_for,
    extract_youtube_id,
    videos_for,
)
from apps.projects.models import Project

User = get_user_model()


def _video(title="V", topic="Getting started", audience=None, **kw):
    kw.setdefault("source_url", "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    return LearningVideo.objects.create(
        title=title, topic=topic, audience=audience or [], **kw)


class ExtractIdTests(TestCase):
    def test_formats(self):
        cases = {
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ": "dQw4w9WgXcQ",
            "https://youtu.be/dQw4w9WgXcQ?t=10": "dQw4w9WgXcQ",
            "https://www.youtube.com/shorts/dQw4w9WgXcQ": "dQw4w9WgXcQ",
            "https://www.youtube.com/embed/dQw4w9WgXcQ": "dQw4w9WgXcQ",
            "dQw4w9WgXcQ": "dQw4w9WgXcQ",
            "https://example.com/nope": "",
            "": "",
        }
        for raw, want in cases.items():
            self.assertEqual(extract_youtube_id(raw), want, raw)


class ModelTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_save_parses_and_busts_cache(self):
        v = _video()
        self.assertEqual(v.youtube_id, "dQw4w9WgXcQ")
        self.assertIn("youtube-nocookie.com/embed/dQw4w9WgXcQ", v.embed_url)
        self.assertEqual(videos_for({"owner"})[0][0], "Getting started")
        v.is_published = False
        v.save()
        self.assertEqual(videos_for({"owner"}), [])


class VideosForTests(TestCase):
    def setUp(self):
        cache.clear()
        _video("A", topic="Basics", audience=["all"])
        _video("B", topic="Payments", audience=["owner", "manager"])
        _video("C", topic="Payments", audience=["dgc"])
        _video("D", topic="Basics", audience=[], source_url="https://x/none")  # no id

    def test_everyone_video_always_shows(self):
        titles = _titles(videos_for({"staff"}))
        self.assertIn("A", titles)
        self.assertNotIn("B", titles)
        self.assertNotIn("C", titles)

    def test_role_scoped(self):
        self.assertIn("B", _titles(videos_for({"owner"})))
        self.assertNotIn("B", _titles(videos_for({"staff"})))
        self.assertIn("C", _titles(videos_for({"dgc"})))

    def test_grouped_by_topic_sorted(self):
        groups = videos_for({"owner", "manager", "dgc"})
        self.assertEqual([t for t, _ in groups], ["Basics", "Payments"])

    def test_missing_youtube_id_excluded(self):
        self.assertNotIn("D", _titles(videos_for({"owner"})))


def _titles(groups):
    return [v.title for _, vids in groups for v in vids]


class AudienceKeysTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="S", status="active")
        self.owner = User.objects.create_user("o", "o@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.project, user=self.owner, role=StoreRole.MANAGER)
        self.admin = User.objects.create_superuser("root", "r@t.test", "pw")
        self.dgc = User.objects.create_user("d", "d@t.test", "pw", is_staff=True)
        Profile.objects.filter(user=self.dgc).update(platform_role=PlatformRole.MANAGER)
        self.dgc = User.objects.get(pk=self.dgc.pk)

    def test_store_role(self):
        self.assertEqual(audience_keys_for(self.owner, self.project), {"manager"})

    def test_admin_sees_all(self):
        self.assertEqual(audience_keys_for(self.admin, None),
                         {"dgc", "owner", "manager", "staff"})

    def test_dgc(self):
        self.assertEqual(audience_keys_for(self.dgc, None), {"dgc"})


@override_settings(ALLOWED_HOSTS=["*"])
class ScreenTests(TestCase):
    def setUp(self):
        cache.clear()
        self.store = Project.objects.create(name="ShopCo", status="active",
                                            feature_flags={"onboarded": True})
        self.owner = User.objects.create_user("o", "o@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.store, user=self.owner, role=StoreRole.OWNER)
        self.admin = User.objects.create_superuser("root", "r@t.test", "pw")
        _video("Owner clip", topic="Sales", audience=["owner"])
        _video("Staff clip", topic="Ops", audience=["staff"])

    def _login(self, user, with_store=True):
        self.client.force_login(user)
        if with_store:
            s = self.client.session
            s[ACTIVE_PROJECT_SESSION_KEY] = self.store.pk
            s.save()

    def test_library_role_filtered(self):
        self._login(self.owner)
        body = self.client.get("/admin/training/").content.decode()
        self.assertIn("Owner clip", body)
        self.assertNotIn("Staff clip", body)

    def test_manage_screen_admin_only(self):
        self._login(self.admin, with_store=False)
        self.assertEqual(self.client.get("/admin/learning/").status_code, 200)
        self._login(self.owner)
        self.assertEqual(self.client.get("/admin/learning/").status_code, 403)

    def test_create_parses_youtube_id(self):
        self._login(self.admin, with_store=False)
        resp = self.client.post("/admin/learning/new/", {
            "title": "Intro", "topic": "Start",
            "source_url": "https://youtu.be/dQw4w9WgXcQ",
            "order": "0", "is_published": "on", "audience": ["owner", "manager"],
        })
        self.assertEqual(resp.status_code, 302)
        v = LearningVideo.objects.get(title="Intro")
        self.assertEqual(v.youtube_id, "dQw4w9WgXcQ")
        self.assertEqual(sorted(v.audience), ["manager", "owner"])

    def test_create_rejects_bad_url(self):
        self._login(self.admin, with_store=False)
        resp = self.client.post("/admin/learning/new/", {
            "title": "X", "topic": "Y", "source_url": "https://example.com/x", "order": "0",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(LearningVideo.objects.filter(title="X").exists())
