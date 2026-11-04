"""The /shop/ page's result-count span is marked hx-swap-oob so an htmx
filter change can replace it wherever it lives in the DOM. _grid.jinja is
shared between the full-page render (plain {% include %}) and the htmx
partial response — without a guard, the oob span rendered unconditionally
and showed up as a second, visible "(N products)" line sitting above the
grid on every normal page load, duplicating the one already in the page
heading. ShopView now only sets ``is_fragment`` on the htmx branch, and
each skin's _grid.jinja gates the oob span behind it."""
from django.test import TestCase, override_settings

from apps.projects.models import Domain, Project


@override_settings(ALLOWED_HOSTS=["*"])
class ShopResultCountTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="Acme", feature_flags={"onboarded": True},
        )
        Domain.objects.create(project=self.project, host="acme.test", is_verified=True)

    def test_full_page_load_has_exactly_one_result_count(self):
        resp = self.client.get("/shop/", HTTP_HOST="acme.test")
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertEqual(html.count('id="result-count"'), 1)

    def test_htmx_fragment_still_carries_the_oob_count(self):
        resp = self.client.get("/shop/", HTTP_HOST="acme.test", HTTP_HX_REQUEST="true")
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertEqual(html.count('id="result-count"'), 1)
        self.assertIn("hx-swap-oob", html)
