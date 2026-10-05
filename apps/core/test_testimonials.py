from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings

from apps.core.models import Testimonial, testimonials_for

User = get_user_model()


def _t(**kw):
    kw.setdefault("quote", "Set up in an afternoon.")
    kw.setdefault("name", "Test Seller")
    kw.setdefault("role", "Owner, Test Shop")
    return Testimonial.objects.create(**kw)


@override_settings(ALLOWED_HOSTS=["*"], PLATFORM_HOSTS=["shop.test"])
class TestimonialLandingTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_hidden_when_none(self):
        resp = self.client.get("/fashion-store/", HTTP_HOST="shop.test")
        self.assertNotContains(resp, "What sellers say")

    def test_needs_both_published_and_consent(self):
        _t(quote="A", is_published=True, consent_confirmed=False)
        _t(quote="B", is_published=False, consent_confirmed=True)
        self.assertEqual(testimonials_for("fashion-store"), [])
        _t(quote="C", is_published=True, consent_confirmed=True)
        self.assertEqual([q for q, *_ in testimonials_for("fashion-store")], ["C"])

    def test_page_filter_and_all_pages(self):
        _t(quote="only-fashion", pages=["fashion-store"], is_published=True, consent_confirmed=True)
        _t(quote="everywhere", pages=[], is_published=True, consent_confirmed=True)
        self.assertEqual({q for q, *_ in testimonials_for("fashion-store")}, {"only-fashion", "everywhere"})
        self.assertEqual({q for q, *_ in testimonials_for("whatsapp-store")}, {"everywhere"})

    def test_renders_on_page_and_cache_busts_on_edit(self):
        t = _t(quote="Loved the WhatsApp orders", is_published=True, consent_confirmed=True)
        resp = self.client.get("/whatsapp-store/", HTTP_HOST="shop.test")
        self.assertContains(resp, "What sellers say")
        self.assertContains(resp, "Loved the WhatsApp orders")
        t.is_published = False
        t.save()
        resp = self.client.get("/whatsapp-store/", HTTP_HOST="shop.test")
        self.assertNotContains(resp, "Loved the WhatsApp orders")

    def test_quote_is_escaped(self):
        _t(quote="<script>alert(1)</script>", is_published=True, consent_confirmed=True)
        resp = self.client.get("/whatsapp-store/", HTTP_HOST="shop.test")
        self.assertNotContains(resp, "<script>alert(1)</script>")


@override_settings(ALLOWED_HOSTS=["*"])
class TestimonialAdminTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = User.objects.create_superuser("root", "root@t.test", "pw")
        self.client.force_login(self.admin)

    def test_list_and_create(self):
        self.assertEqual(self.client.get("/admin/testimonials/").status_code, 200)
        resp = self.client.post("/admin/testimonials/new/", {
            "quote": "Great", "name": "Riya", "role": "Owner", "order": 0,
            "consent_confirmed": "on", "is_published": "on", "pages": ["fashion-store"],
        })
        self.assertEqual(resp.status_code, 302)
        t = Testimonial.objects.get()
        self.assertEqual(t.pages, ["fashion-store"])
        self.assertTrue(t.is_published and t.consent_confirmed)

    def test_cannot_publish_without_consent(self):
        resp = self.client.post("/admin/testimonials/new/", {
            "quote": "Great", "name": "Riya", "is_published": "on", "order": 0,
        })
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Testimonial.objects.exists())

    def test_non_admin_blocked(self):
        self.client.logout()
        u = User.objects.create_user("plain", "p@t.test", "pw")
        self.client.force_login(u)
        self.assertIn(self.client.get("/admin/testimonials/").status_code, (302, 403, 404))
        self.assertEqual(self.client.post("/admin/testimonials/new/", {"quote": "x", "name": "y"}).status_code in (302, 403, 404), True)
        self.assertFalse(Testimonial.objects.exists())
