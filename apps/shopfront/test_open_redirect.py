"""Storefront login's next= param must never send a shopper off-site right
after they hand over their real password."""

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.projects.models import Domain, Project

User = get_user_model()


class LoginNextParamTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="RedirCo", status="active", currency="INR")
        Domain.objects.create(project=self.project, host="redir.iso.test", is_verified=True)
        self.client.post(
            "/account/register/", {"email": "shop@redir.test", "password": "Str0ngPassw0rd!"},
            HTTP_HOST="redir.iso.test",
        )
        self.client.logout()

    def test_external_next_is_blocked(self):
        resp = self.client.post(
            "/account/login/",
            {"email": "shop@redir.test", "password": "Str0ngPassw0rd!",
             "next": "https://evil.example.com/phish"},
            HTTP_HOST="redir.iso.test",
        )
        self.assertEqual(resp.status_code, 302)
        self.assertNotIn("evil.example.com", resp["Location"])

    def test_same_host_next_is_honoured(self):
        resp = self.client.post(
            "/account/login/",
            {"email": "shop@redir.test", "password": "Str0ngPassw0rd!", "next": "/shop/"},
            HTTP_HOST="redir.iso.test",
        )
        self.assertRedirects(resp, "/shop/", fetch_redirect_response=False)
