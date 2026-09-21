"""is_subscription_manager() is the single shared "is this user the DGC
credited on this store's subscription" check behind both has_store_role()'s
owner-level bypass and Mission Control's chrome context (managed_by_user in
apps.control.context_processors) -- previously two independent copies of
the same query that could silently drift apart."""

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.accounts.models import PlatformRole, Profile, StoreRole
from apps.accounts.permissions import OWNER_MANAGER, has_store_role, is_subscription_manager
from apps.billing import services as billing_svc
from apps.projects.models import Project

User = get_user_model()


class IsSubscriptionManagerTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="ManagedCo", status="active")
        sub = billing_svc.ensure_subscription(self.project)
        self.dgc = User.objects.create_user("dgc", "dgc@t.test", "pw", is_staff=True)
        Profile.objects.filter(user=self.dgc).update(platform_role=PlatformRole.MANAGER)
        # create_user()'s post_save signal constructs Profile(user=self.dgc)
        # to get_or_create it -- Django caches that (default-role) instance
        # as self.dgc's reverse .profile relation as a side effect of
        # setting profile.user, before the .update() above ever runs. Any
        # direct (non-HTTP) use of self.dgc.profile after that would read
        # the stale pre-update snapshot without this refresh.
        self.dgc.refresh_from_db()
        sub.manager = self.dgc
        sub.save(update_fields=["manager"])

    def test_true_for_the_credited_manager(self):
        self.assertTrue(is_subscription_manager(self.dgc, self.project))

    def test_false_for_an_unrelated_dgc(self):
        other = User.objects.create_user("dgc2", "dgc2@t.test", "pw", is_staff=True)
        self.assertFalse(is_subscription_manager(other, self.project))

    def test_false_for_none_project(self):
        self.assertFalse(is_subscription_manager(self.dgc, None))

    def test_false_for_anonymous_user(self):
        from django.contrib.auth.models import AnonymousUser

        self.assertFalse(is_subscription_manager(AnonymousUser(), self.project))

    def test_has_store_role_bypass_uses_it(self):
        # A DGC with no real membership still gets owner/manager-level
        # capability on a store they're credited as manager for -- the
        # exact behaviour is_subscription_manager() now backs.
        self.assertTrue(has_store_role(self.dgc, self.project, OWNER_MANAGER))

    def test_has_store_role_bypass_false_once_unmanaged(self):
        sub = self.project.subscription
        sub.manager = None
        sub.save(update_fields=["manager"])
        self.assertFalse(has_store_role(self.dgc, self.project, OWNER_MANAGER))
