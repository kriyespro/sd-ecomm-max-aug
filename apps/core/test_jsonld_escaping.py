"""JSON-LD <script> blocks built with plain json.dumps() let a `</` in any
embedded value (a plan name, a brand name, a blog post title) close the
<script> tag early and inject markup after it -- the FAQ helper already
guarded against this; root()/pricing()/blog now share the same guard."""

from django.test import TestCase

from apps.core.views import _jsonld


class JsonLdEscapingTests(TestCase):
    def test_closing_script_tag_sequence_is_broken_up(self):
        payload = _jsonld({"name": "Evil</script><script>alert(1)</script>"})
        self.assertNotIn("</script>", payload)
        self.assertIn("<\\/script>", payload)

    def test_ordinary_values_round_trip_unchanged(self):
        import json
        payload = _jsonld({"name": "Jewellery Plan", "price": "999"})
        self.assertEqual(json.loads(payload), {"name": "Jewellery Plan", "price": "999"})
