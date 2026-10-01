import datetime as dt
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import Membership
from apps.catalog.models import Product, ProductImage
from apps.coach import services
from apps.coach.models import Mission, MissionKind, MissionLog
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.projects.models import Project

User = get_user_model()


def _product(project, slug, images=0, **kw):
    p = Product.objects.create(project=project, title=slug, slug=slug, price=Decimal("10"),
                               status="active", **kw)
    for i in range(images):
        ProductImage.objects.create(product=p, image=f"products/{slug}-{i}.jpg")
    return p


class CoachBase(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="CoachCo", status="active", feature_flags={"onboarded": True}
        )
        self.owner = User.objects.create_user("co-owner", "o@t.test", "pw", is_staff=True)
        Membership.objects.create(user=self.owner, project=self.project, role="owner")

    def login(self, user):
        self.client.force_login(user)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()


class MissionLogicTests(CoachBase):
    def test_seeded_checklist_exists(self):
        self.assertTrue(Mission.objects.filter(kind="task", is_active=True).count() >= 5)
        self.assertTrue(Mission.objects.filter(kind="tip").exists())

    def test_photo_task_counts_only_products_with_four_images(self):
        _product(self.project, "a", images=4)
        _product(self.project, "b", images=3)
        _product(self.project, "c", images=5)
        row = next(r for r in services.sync_today(self.project)
                   if r["mission"].auto_key == "products_with_photos")
        self.assertEqual((row["progress"], row["target"], row["done"]), (2, 5, False))
        for n in "def":
            _product(self.project, n, images=4)
        row = next(r for r in services.sync_today(self.project)
                   if r["mission"].auto_key == "products_with_photos")
        self.assertTrue(row["done"])

    def test_auto_completion_survives_deleting_the_work(self):
        for n in "abcde":
            _product(self.project, n, images=4)
        services.sync_today(self.project)
        Product.objects.filter(project=self.project).delete()
        row = next(r for r in services.sync_today(self.project)
                   if r["mission"].auto_key == "products_with_photos")
        self.assertTrue(row["done"])

    def test_manual_toggle_roundtrip(self):
        m = Mission.objects.filter(kind="task", auto_key="").first()
        self.assertTrue(services.toggle_manual(self.project, m, self.owner))
        self.assertTrue(next(r for r in services.sync_today(self.project) if r["mission"] == m)["done"])
        self.assertFalse(services.toggle_manual(self.project, m, self.owner))
        with self.assertRaises(ValueError):
            services.toggle_manual(self.project, Mission.objects.filter(auto_key="products_added").first()
                                   or Mission.objects.filter(kind="task").exclude(auto_key="").first(),
                                   self.owner)

    def test_orders_cleared_is_done_when_no_backlog(self):
        row = next(r for r in services.sync_today(self.project) if r["mission"].auto_key == "orders_cleared")
        self.assertTrue(row["done"])

    def test_streak_counts_consecutive_days(self):
        m = Mission.objects.filter(kind="task").first()
        today = services.local_today()
        for back in (0, 1, 2, 4):  # gap at 3
            MissionLog.objects.create(project=self.project, mission=m, day=today - dt.timedelta(days=back),
                                      completed_at=timezone.now())
        streak, week = services.streak_and_week(self.project, today)
        self.assertEqual(streak, 3)
        self.assertEqual([d["done"] for d in week], [False, False, True, False, True, True, True])

    def test_streak_survives_until_today_ends(self):
        m = Mission.objects.filter(kind="task").first()
        today = services.local_today()
        MissionLog.objects.create(project=self.project, mission=m, day=today - dt.timedelta(days=1),
                                  completed_at=timezone.now())
        self.assertEqual(services.streak_and_week(self.project, today)[0], 1)

    def test_message_window_and_tip_rotation(self):
        today = services.local_today()
        Mission.objects.create(kind="message", title="Diwali sale", ends_on=today)
        Mission.objects.create(kind="message", title="Old", ends_on=today - dt.timedelta(days=1))
        Mission.objects.create(kind="message", title="Soon", starts_on=today + dt.timedelta(days=1))
        Mission.objects.create(kind="message", title="Off", is_active=False)
        self.assertEqual([m.title for m in services.messages(today)], ["Diwali sale"])
        self.assertIsNotNone(services.tip_of_the_day(today))
        Mission.objects.filter(kind="tip").delete()
        self.assertIsNone(services.tip_of_the_day(today))

    def test_cta_rejects_unsafe_scheme_and_resolves_store_url(self):
        bad = Mission(kind="task", title="x", cta_url="javascript:alert(1)")
        self.assertIsNone(services.resolve_cta(bad, self.project))
        store = Mission(kind="task", title="x", cta_url="{store_url}", cta_label="Open")
        self.assertEqual(services.resolve_cta(store, self.project)[1], "/admin/domains/")


class ScoreTests(CoachBase):
    def _score(self):
        from apps.control import quick_launch

        streak, week = services.streak_and_week(self.project)
        return services.store_score(self.project, week=week, last=services.last_activity(self.project),
                                    setup_steps=quick_launch.owner_steps(self.project))

    def test_empty_store_scores_low_and_stays_in_range(self):
        s = self._score()
        self.assertTrue(0 <= s["score"] < 30)
        self.assertEqual(s["tier"], "Just starting")
        self.assertEqual(sum(p["max"] for p in s["parts"]), 100)
        self.assertTrue(all(0 <= p["score"] <= p["max"] for p in s["parts"]))

    def test_catalogue_and_activity_raise_the_score(self):
        before = self._score()["score"]
        for i in range(10):
            _product(self.project, f"p{i}", images=3, description="nice")
        after = self._score()
        self.assertGreater(after["score"], before)
        self.assertGreaterEqual(next(p for p in after["parts"] if p["key"] == "catalogue")["score"], 14)


class DashboardPanelTests(CoachBase):
    def test_owner_sees_panel_with_score_and_checklist(self):
        self.login(self.owner)
        resp = self.client.get("/admin/")
        self.assertContains(resp, 'id="coach-panel"')
        self.assertContains(resp, "Store score")
        self.assertContains(resp, "Today's to-do")
        self.assertContains(resp, "Talk to a new supplier")

    def test_staff_does_not_see_panel(self):
        staff = User.objects.create_user("co-staff", "s@t.test", "pw", is_staff=True)
        Membership.objects.create(user=staff, project=self.project, role="staff")
        self.login(staff)
        resp = self.client.get("/admin/")
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'id="coach-panel"')

    def test_superadmin_message_shows_on_owner_dashboard(self):
        Mission.objects.create(kind="message", title="Festive season tips inside", icon="🪔")
        self.login(self.owner)
        self.assertContains(self.client.get("/admin/"), "Festive season tips inside")

    def test_htmx_toggle_returns_panel_and_flips_state(self):
        m = Mission.objects.filter(kind="task", auto_key="").first()
        self.login(self.owner)
        url = f"/admin/coach/{m.pk}/toggle/"
        r = self.client.post(url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'id="coach-panel"')
        self.assertTrue(MissionLog.objects.get(project=self.project, mission=m).completed_at)
        self.client.post(url)
        self.assertIsNone(MissionLog.objects.get(project=self.project, mission=m).completed_at)

    def test_toggle_rejects_auto_task_staff_and_get(self):
        auto = Mission.objects.filter(kind="task").exclude(auto_key="").first()
        manual = Mission.objects.filter(kind="task", auto_key="").first()
        self.login(self.owner)
        self.assertEqual(self.client.post(f"/admin/coach/{auto.pk}/toggle/").status_code, 404)
        self.assertEqual(self.client.get(f"/admin/coach/{manual.pk}/toggle/").status_code, 405)
        staff = User.objects.create_user("co-staff2", "s2@t.test", "pw", is_staff=True)
        Membership.objects.create(user=staff, project=self.project, role="staff")
        self.login(staff)
        self.assertEqual(self.client.post(f"/admin/coach/{manual.pk}/toggle/").status_code, 403)

    def test_one_stores_progress_is_not_another_stores(self):
        other = Project.objects.create(name="Other", status="active", feature_flags={"onboarded": True})
        m = Mission.objects.filter(kind="task", auto_key="").first()
        services.toggle_manual(other, m, self.owner)
        row = next(r for r in services.sync_today(self.project) if r["mission"] == m)
        self.assertFalse(row["done"])


class SuperadminEditorTests(CoachBase):
    def setUp(self):
        super().setUp()
        self.admin = User.objects.create_superuser("co-admin", "a@t.test", "pw")

    def test_owner_cannot_open_editor(self):
        self.login(self.owner)
        self.assertEqual(self.client.get("/admin/coach/").status_code, 403)
        self.assertEqual(self.client.post("/admin/coach/new/", {"title": "x"}).status_code, 403)

    def test_admin_lists_and_creates_message(self):
        self.client.force_login(self.admin)
        self.assertContains(self.client.get("/admin/coach/"), "Talk to a new supplier")
        r = self.client.post("/admin/coach/new/", {
            "kind": "message", "title": "Big sale season", "icon": "🪔", "detail": "Add offers now",
            "auto_key": "", "target": 1, "points": 0, "sort_order": 0, "is_active": "on",
            "cta_url": "/admin/coupons/", "cta_label": "Create offer",
        })
        self.assertEqual(r.status_code, 302)
        self.assertTrue(Mission.objects.filter(kind=MissionKind.MESSAGE, title="Big sale season").exists())

    def test_admin_form_rejects_bad_link_and_reversed_dates(self):
        self.client.force_login(self.admin)
        r = self.client.post("/admin/coach/new/", {
            "kind": "task", "title": "x", "icon": "✅", "auto_key": "", "target": 1, "points": 5,
            "sort_order": 0, "cta_url": "javascript:alert(1)",
            "starts_on": "2026-10-10", "ends_on": "2026-10-01",
        })
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Mission.objects.filter(title="x").exists())

    def test_tip_forces_task_fields_off(self):
        self.client.force_login(self.admin)
        self.client.post("/admin/coach/new/", {
            "kind": "tip", "title": "T", "icon": "💡", "auto_key": "coupons_created", "target": 9,
            "points": 99, "sort_order": 0,
        })
        t = Mission.objects.get(title="T")
        self.assertEqual((t.auto_key, t.target, t.points), ("", 1, 0))
