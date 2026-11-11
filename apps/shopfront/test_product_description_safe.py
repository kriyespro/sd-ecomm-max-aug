"""Product.description is nh3-sanitized on save and renders raw on the PDP
accordion -- but only when it's actually present. The short_description
fallback (plain CharField, never sanitized) must stay autoescaped, or a
seller typing a literal '<' into it would XSS their own customers."""

from django.test import TestCase, override_settings

from apps.catalog.models import Product
from apps.projects.models import Domain, Project


@override_settings(ALLOWED_HOSTS=["*"])
class ProductDescriptionEscapingTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="DescCo", status="active", currency="INR")
        Domain.objects.create(project=self.project, host="desc.shop.test", is_verified=True)

    def test_description_renders_unescaped_html(self):
        product = Product.objects.create(
            project=self.project, title="Ring", price="999", slug="ring",
            description="<p>Hand <b>crafted</b></p>", status="active",
        )
        resp = self.client.get(f"/p/{product.slug}/", HTTP_HOST="desc.shop.test")
        self.assertContains(resp, "<p>Hand <b>crafted</b></p>", html=False)

    def test_short_description_fallback_is_escaped(self):
        product = Product.objects.create(
            project=self.project, title="Ring", price="999", slug="ring2",
            short_description="Not <script>alert(1)</script> safe", status="active",
        )
        resp = self.client.get(f"/p/{product.slug}/", HTTP_HOST="desc.shop.test")
        self.assertNotContains(resp, "<script>alert(1)</script>")
        self.assertContains(resp, "&lt;script&gt;alert(1)&lt;/script&gt;")
