"""Every sidebar link a role can see must actually work for that role — a nav
item gated looser than its view's real permission check is a dead link (403
on click). This is a regression test for exactly that class of bug: a DGC
who only manages a store via Subscription.manager (no real Membership, i.e.
apps.accounts.permissions.dgc_without_membership) saw "Backup & restore" and
the whole "B2B / Wholesale" section in their sidebar, but every one of those
views also mixes in StoreDataAccessMixin (they export full order/customer/
payables data) and 403'd. navigation.py's _OWNER_ONLY gate alone doesn't know
about that second, stricter gate — the fix adds those items to
_STORE_DATA_ONLY too, matching the view stack exactly.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.accounts.models import Membership, PlatformRole, Profile, StoreRole
from apps.billing import services as billing_svc
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.projects.models import Project

User = get_user_model()

_DGC_BLOCKED_URLS = [
    "/admin/backup/",
    "/admin/b2b/settings/",
    "/admin/b2b/marketplace/",
    "/admin/b2b/orders/",
    "/admin/b2b/payables/",
]


@override_settings(ALLOWED_HOSTS=["*"])
class DgcWithoutMembershipNavParityTests(TestCase):
    """A DGC who only manages the store via Subscription.manager (no real
    Membership) — the common real-world shape for both a DGC-provisioned
    store and, since the affiliate program, a store a DGC was later hand-
    assigned to manage."""

    def setUp(self):
        self.project = Project.objects.create(
            name="Nav Parity Co", status="active", feature_flags={"onboarded": True},
        )
        sub = billing_svc.ensure_subscription(self.project)
        self.dgc = User.objects.create_user("navdgc", "navdgc@t.test", "pw", is_staff=True)
        Profile.objects.filter(user=self.dgc).update(platform_role=PlatformRole.MANAGER)
        self.dgc = User.objects.get(pk=self.dgc.pk)  # fresh — no stale profile cache
        sub.manager = self.dgc
        sub.save(update_fields=["manager"])

        self.client.force_login(self.dgc)
        session = self.client.session
        session[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        session.save()

    def test_blocked_screens_are_not_linked_in_the_sidebar(self):
        resp = self.client.get("/admin/products/")
        self.assertNotContains(resp, 'href="/admin/backup/"')
        self.assertNotContains(resp, 'href="/admin/b2b/settings/"')
        self.assertNotContains(resp, 'href="/admin/b2b/marketplace/"')
        self.assertNotContains(resp, 'href="/admin/b2b/orders/"')
        self.assertNotContains(resp, 'href="/admin/b2b/payables/"')

    def test_blocked_screens_still_403_directly(self):
        # The nav hiding them must never be mistaken for the real access
        # control — confirm the views themselves still refuse a direct hit.
        for url in _DGC_BLOCKED_URLS:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)

    def test_store_showcase_stays_visible_and_working(self):
        """Not every _OWNER_ONLY item is store-data-sensitive — the live
        store listing toggle has no order/customer data behind it, so it
        must stay reachable for a managing DGC."""
        resp = self.client.get("/admin/products/")
        self.assertContains(resp, 'href="/admin/showcase/settings/"')
        self.assertEqual(self.client.get("/admin/showcase/settings/").status_code, 200)


@override_settings(ALLOWED_HOSTS=["*"])
class RealOwnerNavParityTests(TestCase):
    """A genuine store owner (real Membership) must keep full access to
    everything the DGC-blocking fix above touches — this is the regression
    guard against over-tightening the gate."""

    def setUp(self):
        self.project = Project.objects.create(
            name="Real Owner Co", status="active", feature_flags={"onboarded": True},
        )
        billing_svc.ensure_subscription(self.project)
        self.owner = User.objects.create_user("realowner", "ro@t.test", "pw", is_staff=True)
        Membership.objects.create(
            user=self.owner, project=self.project, role=StoreRole.OWNER, is_active=True,
        )
        self.client.force_login(self.owner)
        session = self.client.session
        session[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        session.save()

    def test_owner_keeps_backup_and_b2b_access(self):
        resp = self.client.get("/admin/products/")
        for url in _DGC_BLOCKED_URLS:
            with self.subTest(url=url):
                self.assertContains(resp, f'href="{url}"')
                self.assertEqual(self.client.get(url).status_code, 200)
