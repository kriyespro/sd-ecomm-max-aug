"""add_to_cart / checkout_started are recorded server-side (never edge-cached,
so reliable without a beacon) -- see CartAddView / CheckoutView and
apps.analytics.services' "live traffic" docstring for why."""

from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase, override_settings

from apps.analytics.services import funnel_today
from apps.catalog.models import Product
from apps.projects.models import Domain, Project


@override_settings(ALLOWED_HOSTS=["*"])
class FunnelServerSideEventsTests(TestCase):
    def setUp(self):
        cache.clear()
        self.project = Project.objects.create(
            name="FunnelEvCo", status="active", feature_flags={"onboarded": True},
        )
        Domain.objects.create(project=self.project, host="funnelevco.test", is_verified=True)
        self.product = Product.objects.create(
            project=self.project, title="Mug", slug="mug", status="active",
            price=Decimal("299"),
        )

    def test_cart_add_records_add_to_cart_event(self):
        self.client.post(
            "/cart/add/", {"product": "mug", "quantity": "1"}, HTTP_HOST="funnelevco.test",
        )
        self.assertEqual(funnel_today(self.project)["add_to_cart"], 1)

    def test_two_adds_count_twice(self):
        for _ in range(2):
            self.client.post(
                "/cart/add/", {"product": "mug", "quantity": "1"}, HTTP_HOST="funnelevco.test",
            )
        self.assertEqual(funnel_today(self.project)["add_to_cart"], 2)

    def test_checkout_page_records_checkout_started_event(self):
        self.client.post(
            "/cart/add/", {"product": "mug", "quantity": "1"}, HTTP_HOST="funnelevco.test",
        )
        self.client.get("/checkout/", HTTP_HOST="funnelevco.test")
        self.assertEqual(funnel_today(self.project)["checkout"], 1)

    def test_checkout_redirect_with_empty_cart_does_not_record(self):
        self.client.get("/checkout/", HTTP_HOST="funnelevco.test")
        self.assertEqual(funnel_today(self.project)["checkout"], 0)
