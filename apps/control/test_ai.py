"""AI settings screen (owner/manager) + the product "write it for me" endpoint
(any store staff who can edit products)."""

import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.accounts.models import Membership, StoreRole
from apps.ai import services as ai_services
from apps.ai.models import MAX_KEYS_PER_STORE, AiProviderKey
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.projects.models import Project

User = get_user_model()


@override_settings(ALLOWED_HOSTS=["*"])
class AiKeysScreenTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="AiCo", status="active", feature_flags={"onboarded": True}
        )
        self.owner = User.objects.create_user("o", "o@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.project, user=self.owner, role=StoreRole.OWNER)
        self.staff = User.objects.create_user("s", "s@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.project, user=self.staff, role=StoreRole.STAFF)

    def _login(self, user):
        self.client.force_login(user)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()

    def test_owner_opens_staff_denied(self):
        self._login(self.owner)
        self.assertEqual(self.client.get("/admin/settings/ai/").status_code, 200)
        self._login(self.staff)
        self.assertEqual(self.client.get("/admin/settings/ai/").status_code, 403)

    def test_owner_can_add_a_key(self):
        self._login(self.owner)
        resp = self.client.post("/admin/settings/ai/", {
            "label": "My key", "api_key": "sk-or-v1-abcdefghijklmno",
        })
        self.assertEqual(resp.status_code, 302)
        row = AiProviderKey.objects.get(project=self.project)
        self.assertEqual(row.label, "My key")
        self.assertEqual(row.api_key, "sk-or-v1-abcdefghijklmno")

    def test_bad_format_rejected(self):
        self._login(self.owner)
        resp = self.client.post("/admin/settings/ai/", {"label": "", "api_key": "not-a-key"})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(AiProviderKey.objects.filter(project=self.project).exists())

    def test_toggle_and_delete(self):
        self._login(self.owner)
        row = ai_services.add_key(project=self.project, api_key="sk-or-v1-toggletest0000")
        self.client.post(f"/admin/settings/ai/{row.pk}/toggle/")
        row.refresh_from_db()
        self.assertFalse(row.is_active)
        self.client.post(f"/admin/settings/ai/{row.pk}/delete/")
        self.assertFalse(AiProviderKey.objects.filter(pk=row.pk).exists())

    def test_cannot_add_an_eleventh_key(self):
        self._login(self.owner)
        for i in range(MAX_KEYS_PER_STORE):
            ai_services.add_key(project=self.project, api_key=f"sk-or-v1-{i:04d}aaaaaaaaaaaa")
        resp = self.client.post("/admin/settings/ai/", {
            "label": "", "api_key": "sk-or-v1-oneoverlimit0000",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(AiProviderKey.objects.filter(project=self.project).count(),
                         MAX_KEYS_PER_STORE)


@override_settings(ALLOWED_HOSTS=["*"])
class ProductAiGenerateViewTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="AiCo2", status="active", feature_flags={"onboarded": True}
        )
        self.staff = User.objects.create_user("s2", "s2@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.project, user=self.staff, role=StoreRole.STAFF)
        self.client.force_login(self.staff)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()

    def test_staff_can_generate(self):
        payload = {
            "title": "Blue Mug", "short_description": "Nice", "description": "Great.",
            "seo_title": "Blue Mug", "seo_description": "Buy it.", "tags": "mug, blue",
            "model": "meta-llama/llama-3.3-70b-instruct:free",
        }
        with mock.patch("apps.ai.services.generate_product_copy", return_value=payload):
            resp = self.client.post(
                "/admin/products/ai-generate/",
                data=json.dumps({"brief": "blue ceramic mug"}),
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["title"], "Blue Mug")

    def test_ai_error_returns_400_with_message(self):
        with mock.patch("apps.ai.services.generate_product_copy",
                        side_effect=ai_services.AiError("Add a key first.")):
            resp = self.client.post(
                "/admin/products/ai-generate/",
                data=json.dumps({"brief": "x"}),
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"], "Add a key first.")

    def test_anonymous_denied(self):
        self.client.logout()
        resp = self.client.post(
            "/admin/products/ai-generate/",
            data=json.dumps({"brief": "x"}), content_type="application/json",
        )
        self.assertNotEqual(resp.status_code, 200)
