"""SSRF guard on outbound webhook endpoint URLs."""

from django.test import SimpleTestCase

from .services import WebhookURLError, validate_endpoint_url


class ValidateEndpointURLTests(SimpleTestCase):
    def test_rejects_non_http_scheme(self):
        for url in ("file:///etc/passwd", "ftp://example.com/x", "gopher://x"):
            with self.assertRaises(WebhookURLError):
                validate_endpoint_url(url)

    def test_rejects_loopback_and_private_and_metadata(self):
        for url in (
            "http://127.0.0.1/hook",
            "http://localhost/hook",
            "http://10.1.2.3/hook",
            "http://192.168.0.5/hook",
            "http://169.254.169.254/latest/meta-data/",
            "http://[::1]/hook",
        ):
            with self.assertRaises(WebhookURLError):
                validate_endpoint_url(url)

    def test_rejects_odd_ports(self):
        with self.assertRaises(WebhookURLError):
            validate_endpoint_url("http://93.184.216.34:6379/")

    def test_allows_public_ip_literal(self):
        # IP literal — no DNS needed; a public address must pass.
        validate_endpoint_url("https://93.184.216.34/webhooks/orders")
