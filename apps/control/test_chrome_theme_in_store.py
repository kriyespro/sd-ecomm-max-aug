"""Mission Control sidebar colour (apps.control.context_processors
._chrome_theme, templates/control/base_control.jinja): a platform admin
or DGC's usual colour stays untouched on platform-wide screens (Stores,
Users, Billing, Skins, the platform dashboard) -- the request the user
made. But once they've picked a store and are on a store-scoped screen
(Products, Orders, etc), the sidebar switches to a lighter, visually
distinct shade (exact brand hex, since Tailwind's -950 shade lands
near-black for every hue and two -950s didn't read as different enough)
as a "you're inside someone's store right now" cue. A plain store
owner/manager/staff's colour never changes either way -- this only
applies to platform_staff.

platform / platform_in_store / dgc_in_store use literal hex
(apps.control base_control.jinja _side); dgc's own platform-wide colour
is untouched Tailwind orange-950, so still asserted by hue name."""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.accounts.models import Membership, PlatformRole, Profile, StoreRole
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.projects.models import Project

User = get_user_model()


def _sidebar_bg(body: str) -> str:
    """The <aside>'s bg- token: a bare hue name ("indigo") for the normal
    Tailwind-scale states, or the literal hex ("[#2BBBD7]") for the three
    custom-colour states."""
    start = body.index('<aside')
    end = body.index('>', start)
    chunk = body[start:end]
    marker = "bg-"
    idx = chunk.index(marker) + len(marker)
    if chunk[idx] == "[":
        return chunk[idx:chunk.index("]", idx) + 1]
    return chunk[idx:chunk.index("-", idx)]


def _sidebar_tag(body: str) -> str:
    start = body.index('<aside')
    return body[start:body.index('>', start)]


@override_settings(ALLOWED_HOSTS=["*"])
class PlatformAdminChromeTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("chadmin", "chadmin@t.test", "pw")
        self.project = Project.objects.create(
            name="ChromeCo", status="active", feature_flags={"onboarded": True},
        )
        self.client.force_login(self.admin)

    def test_indigo_hex_on_a_platform_wide_screen(self):
        body = self.client.get("/admin/stores/").content.decode()
        self.assertEqual(_sidebar_bg(body), "[#010736]")

    def test_sky_hex_once_working_inside_a_store(self):
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()
        body = self.client.get("/admin/products/").content.decode()
        self.assertEqual(_sidebar_bg(body), "[#2BBBD7]")

    def test_still_platform_hex_on_a_platform_wide_screen_even_with_a_store_selected(self):
        # A store pick in session shouldn't leak the "in store" tint onto
        # platform-wide tools -- only an actual store-scoped screen should.
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()
        body = self.client.get("/admin/stores/").content.decode()
        self.assertEqual(_sidebar_bg(body), "[#010736]")

    def test_badge_still_reads_platform_either_way(self):
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()
        body = self.client.get("/admin/products/").content.decode()
        self.assertIn(">Platform<", body)

    def test_dark_navy_platform_screen_keeps_light_text(self):
        # #010736 is dark -- original light-grey-on-dark scheme stays.
        body = self.client.get("/admin/stores/").content.decode()
        tag = _sidebar_tag(body)
        self.assertIn("text-slate-300", tag)
        self.assertNotIn("text-slate-900", tag)

    def test_light_cyan_in_store_screen_flips_to_dark_text(self):
        # #2BBBD7 is light -- light-grey text would be unreadable there, so
        # the sidebar flips to dark text/tints for this state only.
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()
        body = self.client.get("/admin/products/").content.decode()
        tag = _sidebar_tag(body)
        self.assertIn("text-slate-900", tag)
        self.assertNotIn("text-slate-300", tag)


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
        self.assertEqual(_sidebar_bg(body), "orange")

    def test_amber_hex_once_working_inside_their_managed_store(self):
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()
        body = self.client.get("/admin/products/").content.decode()
        self.assertEqual(_sidebar_bg(body), "[#FFA259]")

    def test_light_amber_in_store_screen_flips_to_dark_text(self):
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()
        body = self.client.get("/admin/products/").content.decode()
        tag = _sidebar_tag(body)
        self.assertIn("text-slate-900", tag)
        self.assertNotIn("text-slate-300", tag)

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
        self.assertEqual(_sidebar_bg(body), "emerald")
