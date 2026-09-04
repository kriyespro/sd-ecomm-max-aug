"""Brute-force brake on the browser login forms."""

from django.core.cache import cache
from django.test import RequestFactory, TestCase

from . import ratelimit


class LoginRateLimitTests(TestCase):
    def setUp(self):
        cache.clear()
        self.rf = RequestFactory()

    def _req(self, ip="203.0.113.9"):
        r = self.rf.post("/accounts/login/")
        r.META["REMOTE_ADDR"] = ip
        return r

    def test_locks_after_max_failures(self):
        req = self._req()
        for _ in range(ratelimit.MAX_FAILURES):
            self.assertFalse(ratelimit.is_locked(req, "victim@example.com"))
            ratelimit.record_failure(req, "victim@example.com")
        self.assertTrue(ratelimit.is_locked(req, "victim@example.com"))

    def test_clear_resets(self):
        req = self._req()
        for _ in range(ratelimit.MAX_FAILURES):
            ratelimit.record_failure(req, "victim@example.com")
        ratelimit.clear(req, "victim@example.com")
        self.assertFalse(ratelimit.is_locked(req, "victim@example.com"))

    def test_other_ip_not_locked_by_ip_key(self):
        for _ in range(ratelimit.MAX_FAILURES):
            ratelimit.record_failure(self._req("203.0.113.9"), "victim@example.com")
        # Different IP, same victim: the per-(user,ip) key is clean and the
        # per-ip key is clean too.
        self.assertFalse(
            ratelimit.is_locked(self._req("198.51.100.2"), "victim@example.com")
        )
