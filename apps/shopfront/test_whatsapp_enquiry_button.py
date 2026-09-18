"""WhatsApp enquiry button on product cards (Marketing -> WhatsApp enquiry
button, apps/control/marketing_views.py WhatsAppEnquiryView): off by default,
needs both the toggle AND a saved WhatsApp number (StoreProfile.whatsapp),
renders a wa.me link pre-filled with the product name, price and its own
absolute URL. Shared partial (templates/shopfront/partials/_whatsapp_enquiry_btn.jinja)
included from all 4 skin-owned card partials -- checked on default, ornza,
botanica2, botanica3 (every other skin falls back to default's)."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from urllib.parse import quote

from apps.cms.models import Skin, StoreProfile
from apps.catalog.models import Product
from apps.projects.models import Domain, Project

User = get_user_model()


@override_settings(ALLOWED_HOSTS=["*"])
class WhatsAppEnquiryButtonTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="Acme", feature_flags={"onboarded": True},
        )
        Domain.objects.create(project=self.project, host="acme.test", is_verified=True)
        self.product = Product.objects.create(
            project=self.project, title="Gold Ring", slug="gold-ring",
            price=Decimal("999"), status="active",
        )

    def _render(self, skin_slug=None):
        kwargs = {}
        if skin_slug:
            kwargs["data"] = {"preview_skin": Skin.objects.get(slug=skin_slug).pk}
            self.client.force_login(User.objects.create_superuser(
                f"root-{skin_slug}", f"root-{skin_slug}@t.test", "pw"))
        resp = self.client.get("/shop/", HTTP_HOST="acme.test", **kwargs)
        self.assertEqual(resp.status_code, 200)
        return resp.content.decode()

    def test_hidden_when_not_enabled(self):
        # The footer's own "WhatsApp us" chat link (StoreProfile.whatsapp_link)
        # is a separate, pre-existing feature and legitimately renders whenever
        # a number is on file -- assert on the enquiry button's distinguishing
        # "?text=" query param, not the bare wa.me/<number> the footer also uses.
        StoreProfile.objects.create(project=self.project, whatsapp="+919812345678")
        html = self._render()
        self.assertNotIn("wa.me/919812345678?text=", html)

    def test_hidden_when_enabled_but_no_number(self):
        StoreProfile.objects.create(project=self.project, whatsapp_enquiry_enabled=True)
        html = self._render()
        self.assertNotIn("?text=", html)

    def test_shown_when_enabled_and_number_set(self):
        StoreProfile.objects.create(
            project=self.project, whatsapp="+91 98123 45678", whatsapp_enquiry_enabled=True,
        )
        html = self._render()
        self.assertIn("https://wa.me/919812345678?text=", html)
        self.assertIn(quote("Gold Ring"), html)
        self.assertIn(quote("/p/gold-ring/"), html)

    def test_message_includes_price(self):
        StoreProfile.objects.create(
            project=self.project, whatsapp="+919812345678", whatsapp_enquiry_enabled=True,
        )
        html = self._render()
        self.assertIn(quote("999"), html)

    def test_shown_on_ornza_botanica2_botanica3_via_fallback_or_override(self):
        StoreProfile.objects.create(
            project=self.project, whatsapp="+919812345678", whatsapp_enquiry_enabled=True,
        )
        for skin in ("ornza", "botanica2", "botanica3"):
            html = self._render(skin)
            self.assertIn("wa.me/919812345678", html, f"missing on {skin}")
