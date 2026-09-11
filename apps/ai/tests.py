"""Per-store OpenRouter keys: free-model discovery, key rotation, product
copy generation."""

import io
import json
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from apps.ai import openrouter, services
from apps.ai.models import MAX_KEYS_PER_STORE, AiProviderKey
from apps.projects.models import Project


class _Resp:
    def __init__(self, payload):
        self._p = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._p


def _key(project, api_key="sk-or-v1-aaaaaaaaaaaaaaaaaaaaaaaaaaaa", **kw):
    return AiProviderKey.objects.create(project=project, api_key=api_key, **kw)


class OpenRouterCatalogTests(TestCase):
    def test_list_free_models_filters_by_pricing(self):
        catalog = {"data": [
            {"id": "a/free-one:free", "pricing": {"prompt": "0", "completion": "0"}},
            {"id": "b/paid-one", "pricing": {"prompt": "0.002", "completion": "0.002"}},
        ]}
        with mock.patch("urllib.request.urlopen", lambda *a, **k: _Resp(catalog)):
            from django.core.cache import cache

            cache.clear()
            free = openrouter.list_free_models()
        self.assertEqual([m["id"] for m in free], ["a/free-one:free"])

    def test_ranked_free_models_prefers_known_strong_models(self):
        catalog = {"data": [
            {"id": "some/obscure-model:free", "pricing": {"prompt": "0", "completion": "0"},
             "context_length": 8000},
            {"id": "meta-llama/llama-3.3-70b-instruct:free",
             "pricing": {"prompt": "0", "completion": "0"}, "context_length": 128000},
        ]}
        from django.core.cache import cache

        cache.clear()
        with mock.patch("urllib.request.urlopen", lambda *a, **k: _Resp(catalog)):
            ranked = openrouter.ranked_free_models()
        self.assertEqual(ranked[0], "meta-llama/llama-3.3-70b-instruct:free")

    def test_falls_back_to_hardcoded_list_when_catalog_unreachable(self):
        import urllib.error

        from django.core.cache import cache

        cache.clear()

        def boom(*a, **k):
            raise urllib.error.URLError("no network")

        with mock.patch("urllib.request.urlopen", boom):
            ranked = openrouter.ranked_free_models()
        self.assertTrue(ranked)
        self.assertTrue(all(":free" in m for m in ranked))


class OpenRouterChatTests(TestCase):
    def test_chat_returns_message_content(self):
        payload = {"choices": [{"message": {"content": "hello"}}]}
        with mock.patch("urllib.request.urlopen", lambda *a, **k: _Resp(payload)):
            out = openrouter.chat(api_key="k", model="m", messages=[{"role": "user", "content": "hi"}])
        self.assertEqual(out, "hello")

    def test_http_error_raises_openrouter_error(self):
        import urllib.error

        def boom(*a, **k):
            raise urllib.error.HTTPError("url", 401, "unauthorized", {}, io.BytesIO(b'{"error":"bad key"}'))

        with mock.patch("urllib.request.urlopen", boom):
            with self.assertRaises(openrouter.OpenRouterError) as ctx:
                openrouter.chat(api_key="bad", model="m", messages=[])
        self.assertEqual(ctx.exception.status, 401)


class KeyManagementTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="Shop", status="active")

    def test_add_key_requires_openrouter_format(self):
        with self.assertRaises(services.AiError):
            services.add_key(project=self.project, api_key="not-a-key")

    def test_add_key_auto_labels(self):
        row = services.add_key(project=self.project, api_key="sk-or-v1-xxxx")
        self.assertEqual(row.label, "Key 1")

    def test_max_ten_keys_enforced(self):
        for i in range(MAX_KEYS_PER_STORE):
            services.add_key(project=self.project, api_key=f"sk-or-v1-{i:04d}aaaaaaaaaaaa")
        with self.assertRaises(services.AiError):
            services.add_key(project=self.project, api_key="sk-or-v1-onemore")

    def test_remove_and_toggle(self):
        row = _key(self.project)
        services.toggle_key(project=self.project, key_id=row.pk)
        row.refresh_from_db()
        self.assertFalse(row.is_active)
        services.remove_key(project=self.project, key_id=row.pk)
        self.assertFalse(AiProviderKey.objects.filter(pk=row.pk).exists())

    def test_masked_key(self):
        row = _key(self.project, api_key="sk-or-v1-abcdefghijklmnop")
        self.assertTrue(row.masked_key.startswith("sk-or-v1"))
        self.assertTrue(row.masked_key.endswith("mnop"))
        self.assertNotIn("abcdefghijkl", row.masked_key)


class RotationTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="Shop", status="active")

    def test_no_keys_raises(self):
        with self.assertRaises(services.AiError):
            services.generate(project=self.project, system_prompt="s", user_prompt="u")

    def test_least_recently_used_key_tried_first(self):
        old = _key(self.project, api_key="sk-or-v1-old000000000000",
                   last_used_at=timezone.now() - timezone.timedelta(hours=1))
        fresh = _key(self.project, api_key="sk-or-v1-new000000000000",
                    last_used_at=timezone.now())
        order = services._rotation_order(self.project)
        self.assertEqual(order[0].pk, old.pk)
        self.assertEqual(order[1].pk, fresh.pk)

    def test_never_used_key_goes_first(self):
        used = _key(self.project, api_key="sk-or-v1-used00000000000",
                    last_used_at=timezone.now())
        never = _key(self.project, api_key="sk-or-v1-never0000000000")
        order = services._rotation_order(self.project)
        self.assertEqual(order[0].pk, never.pk)
        self.assertEqual(order[1].pk, used.pk)

    def test_disabled_key_skipped(self):
        _key(self.project, api_key="sk-or-v1-off0000000000000", is_active=False)
        self.assertEqual(services._rotation_order(self.project), [])

    def test_falls_through_to_next_key_on_failure(self):
        bad = _key(self.project, api_key="sk-or-v1-bad0000000000000")
        good = _key(self.project, api_key="sk-or-v1-good000000000000",
                    last_used_at=timezone.now())  # used more recently -> tried second

        def fake_chat(*, api_key, model, messages, max_tokens=800):
            if api_key == bad.api_key:
                raise openrouter.OpenRouterError("boom")
            return "ok response"

        with mock.patch("apps.ai.openrouter.ranked_free_models", return_value=["m1"]), \
             mock.patch("apps.ai.openrouter.chat", side_effect=fake_chat):
            text, model = services.generate(project=self.project, system_prompt="s", user_prompt="u")
        self.assertEqual(text, "ok response")
        bad.refresh_from_db()
        self.assertIn("boom", bad.last_error)
        good.refresh_from_db()
        self.assertEqual(good.request_count, 1)

    def test_all_keys_failing_raises_ai_error(self):
        _key(self.project)
        with mock.patch("apps.ai.openrouter.ranked_free_models", return_value=["m1"]), \
             mock.patch("apps.ai.openrouter.chat", side_effect=openrouter.OpenRouterError("down")):
            with self.assertRaises(services.AiError):
                services.generate(project=self.project, system_prompt="s", user_prompt="u")

    def test_attempts_are_bounded(self):
        for i in range(MAX_KEYS_PER_STORE):
            _key(self.project, api_key=f"sk-or-v1-b{i:04d}aaaaaaaaaaa")
        calls = []

        def fake_chat(*, api_key, model, messages, max_tokens=800):
            calls.append(api_key)
            raise openrouter.OpenRouterError("nope")

        with mock.patch("apps.ai.openrouter.ranked_free_models", return_value=["m1", "m2", "m3"]), \
             mock.patch("apps.ai.openrouter.chat", side_effect=fake_chat):
            with self.assertRaises(services.AiError):
                services.generate(project=self.project, system_prompt="s", user_prompt="u")
        self.assertLessEqual(len(calls), 6)


class ProductCopyTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="Shop", status="active")
        _key(self.project)

    def test_empty_brief_rejected(self):
        with self.assertRaises(services.AiError):
            services.generate_product_copy(self.project, "  ")

    def test_parses_clean_json(self):
        body = json.dumps({
            "title": "Blue Mug", "short_description": "A nice mug",
            "description": "Great mug.\n\nBuy it.", "seo_title": "Blue Mug | Shop",
            "seo_description": "Buy a blue mug.", "tags": "mug, blue, ceramic",
        })
        with mock.patch("apps.ai.services.generate", return_value=(body, "m1")):
            out = services.generate_product_copy(self.project, "blue ceramic mug")
        self.assertEqual(out["title"], "Blue Mug")
        self.assertEqual(out["model"], "m1")
        self.assertIn("mug", out["tags"])

    def test_parses_json_wrapped_in_markdown_fence(self):
        wrapped = "Sure! Here you go:\n```json\n" + json.dumps({
            "title": "T", "short_description": "S", "description": "D",
            "seo_title": "ST", "seo_description": "SD", "tags": "a, b",
        }) + "\n```"
        with mock.patch("apps.ai.services.generate", return_value=(wrapped, "m1")):
            out = services.generate_product_copy(self.project, "brief")
        self.assertEqual(out["title"], "T")

    def test_unparseable_response_raises_ai_error(self):
        with mock.patch("apps.ai.services.generate", return_value=("not json at all", "m1")):
            with self.assertRaises(services.AiError):
                services.generate_product_copy(self.project, "brief")

    def test_fields_are_clipped(self):
        body = json.dumps({
            "title": "x" * 200, "short_description": "y" * 300, "description": "z" * 3000,
            "seo_title": "a" * 100, "seo_description": "b" * 300, "tags": "c" * 300,
        })
        with mock.patch("apps.ai.services.generate", return_value=(body, "m1")):
            out = services.generate_product_copy(self.project, "brief")
        self.assertEqual(len(out["title"]), 70)
        self.assertEqual(len(out["short_description"]), 160)
        self.assertEqual(len(out["seo_title"]), 60)
        self.assertEqual(len(out["seo_description"]), 160)
