"""404 handler: any dead link bounces to that host's home instead of showing
a bare error page -- on a dedicated custom domain that's "/" (the storefront
is served at the root there, config.storefront_urls); on a store resolved
without a dedicated domain it's "/app/"; on the plain marketing host it's
"/" too. Admin/backend paths keep Django's plain 404 since staff need to
actually see a broken route while debugging."""

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

    def test_marketing_host_dead_link_redirects_to_landing_page(self):
        resp = self.client.get("/no-such-marketing-page/", HTTP_HOST="shopinaday.com")
        self.assertRedirects(resp, "/", fetch_redirect_response=False)


@override_settings(ALLOWED_HOSTS=["*"], DEBUG=False)
class RedirectModelWiringTests(TestCase):
    """``apps.seo.models.Redirect`` has a full merchant-facing admin screen
    (``/admin/seo/redirects/``) but ``resolve_redirect()`` was never called
    anywhere -- a merchant-created 301/302 had zero effect. ``not_found()``
    must consult it ahead of both the generic dead-link bounce and a real
    404, since it covers a renamed slug's own ``Http404`` too, not just a
    routing miss."""

    def setUp(self):
        from apps.seo.models import Redirect

        self.project = Project.objects.create(name="RedirCo", status="active", currency="INR")
        Domain.objects.create(project=self.project, host="redir.shop.test", is_verified=True)
        self.redirect = Redirect.objects.create(
            project=self.project, from_path="/old-page/", to_path="/new-page/",
        )

    def test_matching_path_redirects_with_configured_status_and_counts_a_hit(self):
        resp = self.client.get("/old-page/", HTTP_HOST="redir.shop.test")
        self.assertRedirects(resp, "/new-page/", status_code=301, fetch_redirect_response=False)
        self.redirect.refresh_from_db()
        self.assertEqual(self.redirect.hits, 1)

    def test_is_permanent_false_issues_a_302(self):
        self.redirect.is_permanent = False
        self.redirect.save()
        resp = self.client.get("/old-page/", HTTP_HOST="redir.shop.test")
        self.assertEqual(resp.status_code, 302)

    def test_inactive_redirect_is_ignored(self):
        self.redirect.is_active = False
        self.redirect.save()
        resp = self.client.get("/old-page/", HTTP_HOST="redir.shop.test")
        self.assertRedirects(resp, "/", fetch_redirect_response=False)

    def test_non_matching_path_falls_back_to_dead_link_bounce(self):
        resp = self.client.get("/no-such-page-here/", HTTP_HOST="redir.shop.test")
        self.assertRedirects(resp, "/", fetch_redirect_response=False)

    def test_redirect_takes_priority_over_a_views_own_http404(self):
        """A product-slug Http404 (a real routing match, the view's own
        lookup failing) must still honour a configured redirect -- not just
        a Resolver404 (no route matched at all)."""
        from decimal import Decimal

        from apps.catalog.models import Product
        from apps.seo.models import Redirect

        Product.objects.create(
            project=self.project, title="New", slug="new-slug",
            price=Decimal("10"), status="active",
        )
        Redirect.objects.create(
            project=self.project, from_path="/p/old-slug/", to_path="/p/new-slug/",
        )
        resp = self.client.get("/p/old-slug/", HTTP_HOST="redir.shop.test")
        self.assertRedirects(resp, "/p/new-slug/", status_code=301, fetch_redirect_response=False)
