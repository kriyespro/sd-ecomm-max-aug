"""Mission Control sidebar colour (apps.control.context_processors
._chrome_theme, templates/control/base_control.jinja): a platform admin
or DGC's usual indigo/orange stays untouched on platform-wide screens
(Stores, Users, Billing, Skins, the platform dashboard) -- the request the
user made. But once they've picked a store and are on a store-scoped
screen (Products, Orders, etc), the sidebar switches to a lighter shade
(sky for admin, amber for DGC) as a visual "you're inside someone's
store right now" cue. A plain store owner/manager/staff's colour never
changes either way -- this only applies to platform_staff."""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.accounts.models import Membership, PlatformRole, Profile, StoreRole
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.projects.models import Project

User = get_user_model()


def _sidebar_hue(body: str) -> str:
    start = body.index('<aside')
    end = body.index('>', start)
    chunk = body[start:end]
    marker = "bg-"
    idx = chunk.index(marker) + len(marker)
    return chunk[idx:chunk.index("-", idx)]


@override_settings(ALLOWED_HOSTS=["*"])
class PlatformAdminChromeTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("chadmin", "chadmin@t.test", "pw")
        self.project = Project.objects.create(
            name="ChromeCo", status="active", feature_flags={"onboarded": True},
        )
        self.client.force_login(self.admin)

    def test_indigo_on_a_platform_wide_screen(self):
        body = self.client.get("/admin/stores/").content.decode()
        self.assertEqual(_sidebar_hue(body), "indigo")

    def test_sky_once_working_inside_a_store(self):
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()
        body = self.client.get("/admin/products/").content.decode()
        self.assertEqual(_sidebar_hue(body), "sky")

    def test_still_indigo_on_a_platform_wide_screen_even_with_a_store_selected(self):
        # A store pick in session shouldn't leak the "in store" tint onto
        # platform-wide tools -- only an actual store-scoped screen should.
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()
        body = self.client.get("/admin/stores/").content.decode()
        self.assertEqual(_sidebar_hue(body), "indigo")

    def test_badge_still_reads_platform_either_way(self):
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()
        body = self.client.get("/admin/products/").content.decode()
        self.assertIn(">Platform<", body)


@override_settings(ALLOWED_HOSTS=["*"])
class DgcChromeTests(TestCase):
    def setUp(self):
        self.dgc = User.objects.create_user("chdgc", "chdgc@t.test", "pw", is_staff=True)
        Profile.objects.filter(user=self.dgc).update(platform_role=PlatformRole.MANAGER)
        self.dgc = User.objects.get(pk=self.dgc.pk)
        self.project = Project.objects.create(
            name="DgcChromeCo", status="active", feature_flags={"onboarded": True},
        )
        from apps.billing import services as billing_svc

        sub = billing_svc.ensure_subscription(self.project)
        sub.manager = self.dgc
        sub.save(update_fields=["manager"])
        self.client.force_login(self.dgc)

    def test_orange_on_a_platform_wide_screen(self):
        body = self.client.get("/admin/stores/").content.decode()
        self.assertEqual(_sidebar_hue(body), "orange")

    def test_amber_once_working_inside_their_managed_store(self):
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()
        body = self.client.get("/admin/products/").content.decode()
        self.assertEqual(_sidebar_hue(body), "amber")

    def test_badge_still_reads_dgc_either_way(self):
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()
        body = self.client.get("/admin/products/").content.decode()
        self.assertIn(">DGC<", body)


@override_settings(ALLOWED_HOSTS=["*"])
class StoreOwnerChromeUnaffectedTests(TestCase):
    """The in-store tint is a platform_staff-only concept -- a real store
    owner's own colour (emerald) must never change based on which screen
    they're on."""

    def setUp(self):
        self.project = Project.objects.create(
            name="OwnerChromeCo", status="active", feature_flags={"onboarded": True},
        )
        self.owner = User.objects.create_user("chowner", "chowner@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.project, user=self.owner, role=StoreRole.OWNER)
        self.client.force_login(self.owner)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()

    def test_emerald_on_a_store_scoped_screen(self):
        body = self.client.get("/admin/products/").content.decode()
        self.assertEqual(_sidebar_hue(body), "emerald")
