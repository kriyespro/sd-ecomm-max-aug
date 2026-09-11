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
        from django.core.cache import cache

        cache.clear()
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

        def fake_chat(*, api_key, model, messages, max_tokens=800, timeout=25):
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

        def fake_chat(*, api_key, model, messages, max_tokens=800, timeout=25):
            calls.append(api_key)
            raise openrouter.OpenRouterError("nope")

        with mock.patch("apps.ai.openrouter.ranked_free_models", return_value=["m1", "m2", "m3"]), \
             mock.patch("apps.ai.openrouter.chat", side_effect=fake_chat):
            with self.assertRaises(services.AiError):
                services.generate(project=self.project, system_prompt="s", user_prompt="u")
        self.assertLessEqual(len(calls), services._MAX_ATTEMPTS)

    def test_time_budget_stops_rotation_before_the_proxy_timeout(self):
        """Regression: gunicorn/nginx both cut a request at 30s. Rotation must
        give up with a clean AiError well before that, not run every
        (key, model) combination regardless of how long it's taking."""
        for i in range(MAX_KEYS_PER_STORE):
            _key(self.project, api_key=f"sk-or-v1-tb{i:04d}aaaaaaaaaa")
        calls = []

        def fake_chat(*, api_key, model, messages, max_tokens=800, timeout=25):
            calls.append(api_key)
            raise openrouter.OpenRouterError("slow")

        # started=0, a couple of budget checks still pass, then every
        # subsequent check reports the budget blown.
        clock = iter([0, 0, 0])

        def fake_monotonic():
            return next(clock, 999)

        with mock.patch("apps.ai.openrouter.ranked_free_models",
                        return_value=["m1", "m2", "m3"]), \
             mock.patch("apps.ai.openrouter.chat", side_effect=fake_chat), \
             mock.patch("apps.ai.services.time.monotonic", side_effect=fake_monotonic):
            with self.assertRaises(services.AiError):
                services.generate(project=self.project, system_prompt="s", user_prompt="u")
        self.assertLessEqual(len(calls), 2)

    def test_model_level_error_moves_on_without_trying_every_key(self):
        """Regression: a free model that 403s ("agentic harness only" etc.) is
        a property of the model, not the key — must not burn every key on it."""
        for i in range(5):
            _key(self.project, api_key=f"sk-or-v1-mk{i:04d}aaaaaaaaaa")
        calls = []

        def fake_chat(*, api_key, model, messages, max_tokens=800, timeout=25):
            calls.append((api_key, model))
            if model == "bad-model":
                raise openrouter.OpenRouterError("harness only", status=403)
            return "ok"

        with mock.patch("apps.ai.openrouter.ranked_free_models",
                        return_value=["bad-model", "good-model"]), \
             mock.patch("apps.ai.openrouter.chat", side_effect=fake_chat):
            text, model = services.generate(project=self.project, system_prompt="s", user_prompt="u")
        self.assertEqual(model, "good-model")
        bad_calls = [c for c in calls if c[1] == "bad-model"]
        self.assertEqual(len(bad_calls), 1)  # tried once, then skipped for every other key

    def test_unparseable_content_falls_through_to_next_attempt(self):
        """A successful API call that returns unusable text (a free model
        ignoring the "JSON only" instruction) must rotate on, not fail
        outright — and must not blacklist the model, since it's a formatting
        hiccup, not the model being unavailable."""
        bad = _key(self.project, api_key="sk-or-v1-badtext0000000")
        good = _key(self.project, api_key="sk-or-v1-goodtext000000",
                   last_used_at=timezone.now())

        def fake_chat(*, api_key, model, messages, max_tokens=800, timeout=25):
            return "not json at all" if api_key == bad.api_key else '{"ok": true}'

        def fail_on_plain_text(text):
            if text == "not json at all":
                raise ValueError("no JSON object found")
            return {"ok": True}

        with mock.patch("apps.ai.openrouter.ranked_free_models", return_value=["m1"]), \
             mock.patch("apps.ai.openrouter.chat", side_effect=fake_chat):
            result, model = services.generate(
                project=self.project, system_prompt="s", user_prompt="u", parse=fail_on_plain_text,
            )
        self.assertEqual(result, {"ok": True})
        from django.core.cache import cache

        self.assertIsNone(cache.get(services._dead_model_key("m1")))

    def test_dead_model_skipped_entirely_on_next_call(self):
        _key(self.project)

        def fake_chat(*, api_key, model, messages, max_tokens=800, timeout=25):
            if model == "bad-model":
                raise openrouter.OpenRouterError("nope", status=403)
            return "ok"

        with mock.patch("apps.ai.openrouter.ranked_free_models",
                        return_value=["bad-model", "good-model"]), \
             mock.patch("apps.ai.openrouter.chat", side_effect=fake_chat) as chat:
            services.generate(project=self.project, system_prompt="s", user_prompt="u")
            chat.reset_mock()
            services.generate(project=self.project, system_prompt="s", user_prompt="u")
        models_tried = {c.kwargs["model"] for c in chat.call_args_list}
        self.assertNotIn("bad-model", models_tried)


def _fake_generate_returning(raw_text, model="m1"):
    """A ``services.generate`` stand-in that applies ``parse`` the same way
    the real rotation does: a ValueError from it surfaces as the same
    AiError the real function raises once every attempt is exhausted."""
    def _gen(*, project, system_prompt, user_prompt, max_tokens=800, parse=None):
        if parse is None:
            return raw_text, model
        try:
            return parse(raw_text), model
        except ValueError as exc:
            raise services.AiError(f"AI is busy right now. ({exc})") from exc
    return _gen


class JsonParsingTests(TestCase):
    """``_parse_json_object`` — the free models this hits don't always follow
    a "JSON only" instruction."""

    def test_clean_json(self):
        out = services._parse_json_object('{"title": "T"}')
        self.assertEqual(out["title"], "T")

    def test_wrapped_in_markdown_fence_with_preamble(self):
        raw = 'Sure! Here you go:\n```json\n{"title": "T"}\n```'
        self.assertEqual(services._parse_json_object(raw)["title"], "T")

    def test_trailing_comma_repaired(self):
        raw = '{"title": "T", "tags": "a, b",}'
        self.assertEqual(services._parse_json_object(raw)["title"], "T")

    def test_empty_response_rejected(self):
        with self.assertRaises(ValueError):
            services._parse_json_object("   ")

    def test_no_braces_at_all_rejected(self):
        with self.assertRaises(ValueError):
            services._parse_json_object("Sorry, I can't help with that.")

    def test_truncated_json_rejected(self):
        with self.assertRaises(ValueError):
            services._parse_json_object('{"title": "Truncated mid-str')


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
        with mock.patch("apps.ai.services.generate", side_effect=_fake_generate_returning(body)):
            out = services.generate_product_copy(self.project, "blue ceramic mug")
        self.assertEqual(out["title"], "Blue Mug")
        self.assertEqual(out["model"], "m1")
        self.assertIn("mug", out["tags"])

    def test_unparseable_response_raises_ai_error(self):
        with mock.patch("apps.ai.services.generate",
                        side_effect=_fake_generate_returning("not json at all")):
            with self.assertRaises(services.AiError):
                services.generate_product_copy(self.project, "brief")

    def test_fields_are_clipped(self):
        body = json.dumps({
            "title": "x" * 200, "short_description": "y" * 300, "description": "z" * 3000,
            "seo_title": "a" * 100, "seo_description": "b" * 300, "tags": "c" * 300,
        })
        with mock.patch("apps.ai.services.generate", side_effect=_fake_generate_returning(body)):
            out = services.generate_product_copy(self.project, "brief")
        self.assertEqual(len(out["title"]), 70)
        self.assertEqual(len(out["short_description"]), 160)
        self.assertEqual(len(out["seo_title"]), 60)
        self.assertEqual(len(out["seo_description"]), 160)

    def test_calls_generate_with_a_bumped_token_budget(self):
        captured = {}

        def _gen(*, project, system_prompt, user_prompt, max_tokens=800, parse=None):
            captured["max_tokens"] = max_tokens
            return parse(json.dumps({"title": "T"})), "m1"

        with mock.patch("apps.ai.services.generate", side_effect=_gen):
            services.generate_product_copy(self.project, "brief")
        self.assertGreater(captured["max_tokens"], 800)
