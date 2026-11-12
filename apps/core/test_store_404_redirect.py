"""404 handler: a dead link on a store bounces to the store home instead of
showing a bare error page -- on a dedicated custom domain that's "/" (the
storefront is served at the root there, config.storefront_urls); everywhere
else it's "/app/". Admin/backend paths keep Django's plain 404 since staff
need to actually see a broken route while debugging."""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.accounts.models import Membership, StoreRole
from apps.projects.models import Domain, Project

User = get_user_model()


@override_settings(ALLOWED_HOSTS=["*"], DEBUG=False)
class StoreNotFoundRedirectTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="404Co", status="active", currency="INR")
        Domain.objects.create(project=self.project, host="fourohfour.shop.test", is_verified=True)

    def test_dead_link_on_custom_domain_redirects_to_root(self):
        # A verified Domain match is always treated as the store's own
        # dedicated domain -> storefront served at "/".
        resp = self.client.get("/no-such-page-here/", HTTP_HOST="fourohfour.shop.test")
        self.assertRedirects(resp, "/", fetch_redirect_response=False)

    def test_dead_link_on_platform_host_for_a_logged_in_owner_redirects_to_app(self):
        # No Domain/primary_domain match on this host -- project resolves only
        # via the authenticated owner's single membership, so it's not a
        # dedicated domain and the storefront still lives under "/app/".
        owner = User.objects.create_user("owner", "owner@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.project, user=owner, role=StoreRole.OWNER)
        self.client.force_login(owner)
        resp = self.client.get("/no-such-page-here/", HTTP_HOST="shopinaday.com")
        self.assertRedirects(resp, "/app/", fetch_redirect_response=False)

    def test_dead_admin_link_keeps_plain_404(self):
        resp = self.client.get("/admin/no-such-screen/", HTTP_HOST="fourohfour.shop.test")
        self.assertEqual(resp.status_code, 404)

    def test_marketing_host_dead_link_keeps_plain_404(self):
        resp = self.client.get("/no-such-marketing-page/", HTTP_HOST="shopinaday.com")
        self.assertEqual(resp.status_code, 404)
