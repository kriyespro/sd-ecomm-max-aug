"""is_near_cap() — the shared threshold behind the "approaching your
limit" nudge on Products/Domains/Team, distinct from (and less severe
than) the hard at-cap block."""

from django.test import SimpleTestCase

from apps.billing.limits import is_near_cap


class IsNearCapTests(SimpleTestCase):
    def test_no_cap_never_near(self):
        self.assertFalse(is_near_cap(1000, None))

    def test_zero_cap_never_near(self):
        self.assertFalse(is_near_cap(0, 0))

    def test_below_threshold_not_near(self):
        self.assertFalse(is_near_cap(3, 5))  # 60%

    def test_at_threshold_is_near(self):
        self.assertTrue(is_near_cap(4, 5))  # exactly 80%

    def test_above_threshold_still_near_until_full(self):
        self.assertTrue(is_near_cap(9, 10))  # 90%

    def test_at_cap_is_not_near_its_full(self):
        self.assertFalse(is_near_cap(5, 5))

    def test_over_cap_is_not_near_its_full(self):
        self.assertFalse(is_near_cap(6, 5))
