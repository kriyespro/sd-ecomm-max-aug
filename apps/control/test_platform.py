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


class ArchiveAndDeleteStoreTests(TestCase):
    """Superadmin-only: archive/unarchive (reversible) and hard delete."""

    def setUp(self):
        self.superuser = User.objects.create_superuser(
            username="root@t.test", email="root@t.test", password="pw"
        )
        self.owner_admin = User.objects.create_user(
            username="oa@t.test", email="oa@t.test", password="pw", is_staff=True
        )
        Profile.objects.filter(user=self.owner_admin).update(platform_role=PlatformRole.OWNER)
        self.owner_admin.refresh_from_db()
        self.project = Project.objects.create(name="ArchiveCo", status="active")

    def test_superuser_archives_and_unarchives(self):
        store_services.archive_store(project=self.project, actor=self.superuser)
        self.project.refresh_from_db()
        self.assertEqual(self.project.status, Project.Status.ARCHIVED)

        store_services.unarchive_store(project=self.project, actor=self.superuser)
        self.project.refresh_from_db()
        self.assertEqual(self.project.status, Project.Status.ACTIVE)

    def test_platform_owner_cannot_archive(self):
        with self.assertRaises(PermissionDenied):
            store_services.archive_store(project=self.project, actor=self.owner_admin)

    def test_delete_requires_exact_name(self):
        with self.assertRaises(ValidationError):
            store_services.delete_store(
                project=self.project, actor=self.superuser, confirm_name="wrong name",
            )
        self.assertTrue(Project.objects.filter(pk=self.project.pk).exists())

    def test_superuser_deletes_store(self):
        pk = self.project.pk
        store_services.delete_store(
            project=self.project, actor=self.superuser, confirm_name="ArchiveCo",
        )
        self.assertFalse(Project.objects.filter(pk=pk).exists())

    def test_platform_owner_cannot_delete(self):
        with self.assertRaises(PermissionDenied):
            store_services.delete_store(
                project=self.project, actor=self.owner_admin, confirm_name="ArchiveCo",
            )
        self.assertTrue(Project.objects.filter(pk=self.project.pk).exists())


class ArchiveAndDeleteScreensTests(TestCase):
    def setUp(self):
        self.superuser = User.objects.create_superuser(
            username="root2@t.test", email="root2@t.test", password="pw"
        )
        self.client.force_login(self.superuser)
        self.project = Project.objects.create(name="ScreenArchiveCo", status="active")

    def test_archive_view_then_unarchive_view(self):
        resp = self.client.post(f"/admin/stores/{self.project.pk}/archive/", follow=True)
        self.assertEqual(resp.status_code, 200)
        self.project.refresh_from_db()
        self.assertEqual(self.project.status, Project.Status.ARCHIVED)

        resp = self.client.post(f"/admin/stores/{self.project.pk}/unarchive/", follow=True)
        self.assertEqual(resp.status_code, 200)
        self.project.refresh_from_db()
        self.assertEqual(self.project.status, Project.Status.ACTIVE)

    def test_delete_view_wrong_name_keeps_store(self):
        resp = self.client.post(
            f"/admin/stores/{self.project.pk}/delete/",
            {"confirm_name": "not it"}, follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(Project.objects.filter(pk=self.project.pk).exists())

    def test_delete_view_correct_name_deletes(self):
        pk = self.project.pk
        resp = self.client.post(
            f"/admin/stores/{pk}/delete/",
            {"confirm_name": "ScreenArchiveCo"}, follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Project.objects.filter(pk=pk).exists())

    def test_non_superuser_gets_no_danger_zone(self):
        admin = User.objects.create_user(
            username="oa2@t.test", email="oa2@t.test", password="pw", is_staff=True
        )
        Profile.objects.filter(user=admin).update(platform_role=PlatformRole.OWNER)
        self.client.force_login(admin)
        resp = self.client.get(f"/admin/stores/{self.project.pk}/")
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "Danger zone")
