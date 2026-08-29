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
