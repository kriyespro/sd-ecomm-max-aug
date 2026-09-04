"""Platform-admin: promote a user to DGC, and (re)assign a store's DGC."""

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase

from apps.accounts.models import Membership, PlatformRole, Profile, StoreRole
from apps.control import services as control_services
from apps.control import store_services
from apps.projects.models import Project

User = get_user_model()


class SetPlatformRoleTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="admin@t.test", email="admin@t.test", password="pw", is_staff=True
        )
        Profile.objects.filter(user=self.admin).update(platform_role=PlatformRole.OWNER)
        self.admin.refresh_from_db()

    def test_admin_promotes_existing_user_to_dgc(self):
        target = User.objects.create_user(username="u@t.test", email="u@t.test", password="pw")
        control_services.set_platform_role(
            actor=self.admin, target=target, platform_role=PlatformRole.MANAGER,
        )
        target.refresh_from_db()
        self.assertEqual(target.profile.platform_role, PlatformRole.MANAGER)
        self.assertTrue(target.is_staff)  # DGC needs Mission Control access

    def test_non_admin_cannot_change_roles(self):
        plain = User.objects.create_user(username="p@t.test", email="p@t.test", password="pw")
        target = User.objects.create_user(username="u2@t.test", email="u2@t.test", password="pw")
        with self.assertRaises(PermissionDenied):
            control_services.set_platform_role(
                actor=plain, target=target, platform_role=PlatformRole.MANAGER,
            )

    def test_only_superuser_grants_platform_owner(self):
        target = User.objects.create_user(username="u3@t.test", email="u3@t.test", password="pw")
        with self.assertRaises(PermissionDenied):
            control_services.set_platform_role(
                actor=self.admin, target=target, platform_role=PlatformRole.OWNER,
            )

    def test_unknown_role_rejected(self):
        target = User.objects.create_user(username="u4@t.test", email="u4@t.test", password="pw")
        with self.assertRaises(ValidationError):
            control_services.set_platform_role(
                actor=self.admin, target=target, platform_role="bogus",
            )


class SetStoreManagerTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="admin2@t.test", email="admin2@t.test", password="pw", is_staff=True
        )
        Profile.objects.filter(user=self.admin).update(platform_role=PlatformRole.OWNER)
        self.admin.refresh_from_db()
        self.dgc = User.objects.create_user(username="dgc@t.test", email="dgc@t.test", password="pw")
        Profile.objects.filter(user=self.dgc).update(platform_role=PlatformRole.MANAGER)
        self.dgc.refresh_from_db()
        self.project = Project.objects.create(name="AssignCo", status="active")

    def test_admin_assigns_dgc_to_existing_store(self):
        store_services.set_store_manager(project=self.project, manager=self.dgc, actor=self.admin)
        self.project.refresh_from_db()
        self.assertEqual(self.project.subscription.manager_id, self.dgc.pk)

    def test_admin_clears_manager(self):
        store_services.set_store_manager(project=self.project, manager=self.dgc, actor=self.admin)
        store_services.set_store_manager(project=self.project, manager=None, actor=self.admin)
        self.project.refresh_from_db()
        self.assertIsNone(self.project.subscription.manager_id)

    def test_non_admin_cannot_assign(self):
        plain = User.objects.create_user(username="p2@t.test", email="p2@t.test", password="pw")
        with self.assertRaises(PermissionDenied):
            store_services.set_store_manager(project=self.project, manager=self.dgc, actor=plain)

    def test_rejects_non_dgc_user_as_manager(self):
        not_dgc = User.objects.create_user(username="nd@t.test", email="nd@t.test", password="pw")
        with self.assertRaises(ValidationError):
            store_services.set_store_manager(project=self.project, manager=not_dgc, actor=self.admin)


class PlatformScreensTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="admin3@t.test", email="admin3@t.test", password="pw", is_staff=True
        )
        Profile.objects.filter(user=self.admin).update(platform_role=PlatformRole.OWNER)
        self.admin.refresh_from_db()
        self.client.force_login(self.admin)

    def test_user_role_change_view_promotes_to_dgc(self):
        target = User.objects.create_user(username="u5@t.test", email="u5@t.test", password="pw")
        resp = self.client.post(
            f"/admin/users/{target.pk}/role/",
            {"platform_role": PlatformRole.MANAGER}, follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        target.refresh_from_db()
        self.assertEqual(target.profile.platform_role, PlatformRole.MANAGER)

    def test_store_manager_assign_view(self):
        dgc = User.objects.create_user(username="dgc2@t.test", email="dgc2@t.test", password="pw")
        Profile.objects.filter(user=dgc).update(platform_role=PlatformRole.MANAGER)
        project = Project.objects.create(name="ScreenAssignCo", status="active")
        resp = self.client.post(
            f"/admin/stores/{project.pk}/manager/", {"manager": dgc.pk}, follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        project.refresh_from_db()
        self.assertEqual(project.subscription.manager_id, dgc.pk)

    def test_store_detail_shows_share_block(self):
        project = Project.objects.create(name="ShareCo", status="active")
        owner = User.objects.create_user(username="own@t.test", email="own@t.test", password="pw")
        Membership.objects.create(project=project, user=owner, role=StoreRole.OWNER, is_active=True)
        resp = self.client.get(f"/admin/stores/{project.pk}/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Share with the owner")
        self.assertContains(resp, "own@t.test")
