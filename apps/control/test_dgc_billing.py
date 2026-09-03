from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.accounts.models import Membership, PlatformRole, Profile
from apps.billing import services as billing_svc
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.projects.models import Project

User = get_user_model()


@override_settings(ALLOWED_HOSTS=["*"])
class DgcManagedStoreHidesPlanTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="Managed Co", status="active", feature_flags={"onboarded": True}
        )
        self.sub = billing_svc.ensure_subscription(self.project)

        self.owner = User.objects.create_user(
            "own", "own@managed.test", "pw", is_staff=True
        )
        Membership.objects.create(user=self.owner, project=self.project, role="owner")

        self.dgc = User.objects.create_user("dgc", "dgc@t.test", "pw", is_staff=True)
        Profile.objects.update_or_create(
            user=self.dgc, defaults={"platform_role": PlatformRole.MANAGER}
        )

    def _login(self, user):
        self.client.force_login(user)
        session = self.client.session
        session[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        session.save()

    def _make_dgc_managed(self):
        self.sub.manager = self.dgc
        self.sub.save(update_fields=["manager"])

    # --- not managed: owner has normal billing access ---
    def test_owner_sees_plan_when_not_dgc_managed(self):
        self._login(self.owner)
        resp = self.client.get("/admin/products/")
        self.assertContains(resp, 'href="/admin/plan/"')
        self.assertEqual(self.client.get("/admin/plan/").status_code, 200)

    # --- managed: hidden + blocked for the store team ---
    def test_owner_loses_plan_when_dgc_managed(self):
        self._make_dgc_managed()
        self._login(self.owner)
        resp = self.client.get("/admin/products/")
        self.assertNotContains(resp, 'href="/admin/plan/"')
        self.assertEqual(self.client.get("/admin/plan/").status_code, 403)

    # --- platform staff still get in ---
    def test_platform_staff_still_see_plan_for_managed_store(self):
        self._make_dgc_managed()
        su = User.objects.create_superuser("root", "root@t.test", "pw")
        self._login(su)
        self.assertEqual(self.client.get("/admin/plan/").status_code, 200)
