from unittest import mock

from django.test import TestCase, override_settings

from apps.projects import domains as domain_svc
from apps.projects.models import Domain, Project

_PATCH = "apps.projects.domains."


class VerifyDomainTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="Acme")
        self.domain = Domain.objects.create(project=self.project, host="shop.acme.test")

    def _verify(self, *, txt=None, ips=None, token_body=""):
        with mock.patch(_PATCH + "_lookup_txt", return_value=txt or []), \
             mock.patch(_PATCH + "_lookup_ips", return_value=ips or []), \
             mock.patch(_PATCH + "_fetch_domain_check", return_value=token_body):
            return domain_svc.verify_domain(self.domain)

    def test_txt_record_verifies(self):
        self.assertTrue(self._verify(txt=[self.domain.txt_value]))
        self.domain.refresh_from_db()
        self.assertTrue(self.domain.is_verified)
        self.assertIsNotNone(self.domain.verified_at)

    @override_settings(PLATFORM_PUBLIC_IP="203.0.113.9")
    def test_direct_a_record_verifies(self):
        self.assertTrue(self._verify(ips=["203.0.113.9"]))
        self.domain.refresh_from_db()
        self.assertTrue(self.domain.is_verified)

    def test_cloudflare_proxy_with_routing_verifies(self):
        # 104.16.0.0/13 is a Cloudflare edge range.
        ok = self._verify(ips=["104.16.5.5"], token_body=self.domain.verification_token)
        self.assertTrue(ok)
        self.domain.refresh_from_db()
        self.assertTrue(self.domain.is_verified)

    def test_cloudflare_proxy_without_routing_fails(self):
        self.assertFalse(self._verify(ips=["104.16.5.5"], token_body=""))
        self.domain.refresh_from_db()
        self.assertFalse(self.domain.is_verified)
        self.assertIn("Cloudflare", self.domain.last_check_error)

    def test_unrelated_dns_fails(self):
        self.assertFalse(self._verify(ips=["198.51.100.1"]))
        self.domain.refresh_from_db()
        self.assertFalse(self.domain.is_verified)
        self.assertTrue(self.domain.last_check_error)


class VerifyPendingDomainsTaskTests(TestCase):
    """A domain added long ago that only just got its TXT record set must
    still be picked up by the periodic retry — no age cutoff should drop it
    out of automatic verification forever."""

    def test_a_domain_older_than_the_old_14_day_cutoff_still_gets_retried(self):
        from datetime import timedelta

        from django.utils import timezone

        from apps.projects.tasks import verify_pending_domains_task

        project = Project.objects.create(name="OldDomainCo")
        domain = Domain.objects.create(project=project, host="late.acme.test")
        Domain.objects.filter(pk=domain.pk).update(
            created_at=timezone.now() - timedelta(days=30)
        )

        with mock.patch(_PATCH + "_lookup_txt", return_value=[domain.txt_value]), \
             mock.patch(_PATCH + "_lookup_ips", return_value=[]), \
             mock.patch(_PATCH + "_fetch_domain_check", return_value=""):
            verify_pending_domains_task()

        domain.refresh_from_db()
        self.assertTrue(domain.is_verified)


class LookupIpsFallbackTests(TestCase):
    def test_falls_back_to_getaddrinfo_when_dig_missing(self):
        info = [(2, 1, 6, "", ("104.16.5.5", 0))]
        with mock.patch(_PATCH + "_dig", return_value=[]), \
             mock.patch(_PATCH + "socket.getaddrinfo", return_value=info) as gai:
            self.assertEqual(domain_svc._lookup_ips("shop.acme.test"), ["104.16.5.5"])
            gai.assert_called_once()

    def test_prefers_dig_result(self):
        with mock.patch(_PATCH + "_dig", return_value=["203.0.113.9"]), \
             mock.patch(_PATCH + "socket.getaddrinfo") as gai:
            self.assertEqual(domain_svc._lookup_ips("shop.acme.test"), ["203.0.113.9"])
            gai.assert_not_called()


@override_settings(ALLOWED_HOSTS=["*"])
class DomainCheckEndpointTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="Acme")
        self.domain = Domain.objects.create(project=self.project, host="shop.acme.test")

    def test_returns_token_for_known_host(self):
        resp = self.client.get("/.well-known/sd-domain-check", HTTP_HOST="shop.acme.test")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content.decode().strip(), self.domain.verification_token)

    def test_404_for_unknown_host(self):
        resp = self.client.get("/.well-known/sd-domain-check", HTTP_HOST="nope.example.test")
        self.assertEqual(resp.status_code, 404)


from django.test import override_settings as _os


@_os(PLATFORM_BASE_DOMAIN="shopinaday.com", PLATFORM_HOSTS=["shopinaday.com"])
class PublicUrlPriorityTests(TestCase):
    def _store(self):
        from apps.projects.models import Project
        return Project.objects.create(name="P", status="active")

    def test_custom_domain_beats_platform_subdomain(self):
        from apps.projects.models import Domain
        p = self._store()
        Domain.objects.create(project=p, host="genze.shopinaday.com", is_verified=True, is_primary=True)
        Domain.objects.create(project=p, host="shop.genze.in", is_verified=True)
        self.assertEqual(p.public_url, "https://shop.genze.in/")
        self.assertEqual(p.public_host, "shop.genze.in")

    def test_falls_back_to_subdomain_when_no_custom_domain(self):
        from apps.projects.models import Domain
        p = self._store()
        Domain.objects.create(project=p, host="genze.shopinaday.com", is_verified=True, is_primary=True)
        self.assertEqual(p.public_url, "https://genze.shopinaday.com/")

    def test_unverified_custom_domain_is_ignored(self):
        from apps.projects.models import Domain
        p = self._store()
        Domain.objects.create(project=p, host="genze.shopinaday.com", is_verified=True, is_primary=True)
        Domain.objects.create(project=p, host="not-yet.genze.in", is_verified=False)
        self.assertEqual(p.public_url, "https://genze.shopinaday.com/")

    def test_none_when_no_domain(self):
        self.assertIsNone(self._store().public_url)
